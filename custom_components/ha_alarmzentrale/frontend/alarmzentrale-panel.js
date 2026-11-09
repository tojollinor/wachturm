/** Wachturm 0.8.2: local admin panel; no outside JS dependencies.
 * All values originating from HA/configuration are HTML escaped before render.
 * Only administrators may call the server's mutation WebSocket endpoints.
 */
const E = (value) => String(value ?? "").replace(/[&<>"']/g, (m) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[m]);
const clone = (value) => JSON.parse(JSON.stringify(value));
const slug = (value) => String(value || "").trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_");
const split = (value) => String(value || "").split(",").map((x) => x.trim()).filter(Boolean);
const SECTIONS = [
  ["overview", "Übersicht"], ["areas", "Bereiche"], ["modes", "Modi"],
  ["actions", "Aktionen"], ["diagnostics", "Diagnose"], ["users", "Benutzer"],
  ["roles", "Rollen"], ["bindings", "Bediengeräte"], ["audit", "Bedienhistorie"],
];
const SENSOR_PROFILES={
  door:{label:'Tür',normal:'door-closed',active:'door-open'},
  window:{label:'Fenster',normal:'window-closed-variant',active:'window-open-variant'},
  garage:{label:'Garagentor',normal:'garage',active:'garage-open'},
  gate:{label:'Tor',normal:'gate',active:'gate-open'},
  motion:{label:'Bewegungsmelder',normal:'motion-sensor-off',active:'motion-sensor'},
  glass:{label:'Glasbruchmelder',normal:'shield-check',active:'glass-fragile'},
  vibration:{label:'Erschütterungsmelder',normal:'vibrate-off',active:'vibrate'},
  smoke:{label:'Rauchmelder',normal:'smoke-detector',active:'smoke-detector-alert'},
  fire:{label:'Flammenmelder',normal:'fire-off',active:'fire'},
  heat:{label:'Temperatur-/Hitzemelder',normal:'thermometer',active:'thermometer-alert'},
  water:{label:'Wasser-/Leckagemelder',normal:'water-check',active:'water-alert'},
  gas:{label:'Gasmelder',normal:'shield-check',active:'gas-cylinder'},
  co:{label:'Kohlenmonoxidmelder',normal:'shield-check',active:'molecule-co'},
  panic:{label:'Panik-/Überfalltaster',normal:'gesture-tap-button',active:'alarm-light'},
  sabotage:{label:'Sabotagemelder',normal:'shield-check',active:'shield-alert'},
  sound:{label:'Geräuschmelder',normal:'volume-off',active:'volume-high'},
  generic:{label:'Sonstiger Alarmsensor',normal:'shield-home',active:'shield-alert'},
};
const SENSOR_TYPES=[['auto','Automatisch'],...Object.entries(SENSOR_PROFILES).map(([key,p])=>[key,p.label])];
const SABOTAGE_AREA='system_sabotage';
const PERMS = [["arm", "Scharfschalten"], ["disarm", "Unscharfschalten"],
  ["mute", "Stummschalten"], ["acknowledge", "Quittieren"]];

class HaAlarmzentralePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._doc = null;
    this._original = null;
    this._revision = null;
    this._pinUsers = [];
    this._statuses = [];
    this._diagnostics = {enabled:false};
    this._haUsers = [];
    this._audit = [];
    this._auditFilter = {user_key:"",source:"",area:"",action:"",device:"",since:"",until:""};
    this._active = "overview";
    this._area = "";
    this._dirty = false;
    this._busy = false;
    this._safe = false;
    this._error = "";
    this._notice = "";
    this._dialog = null;
    this._unsubscribe = null;
    this._loadStarted = false;
    this._confirmDiscard = false;
    this._catalog = {devices:[],entities:[],services:{}};
    this._catalogReady = false;
    this._catalogError = '';
    this._canAdmin=true;this._runtime=null;this._polling=false;this._pollTimer=null;this._countdownTimer=null;this._query='';
  }

  set hass(value) {
    this._hass = value;
    this._canAdmin=value?.user?.is_admin!==false;
    // HA updates hass frequently; never rebuild forms or lose keyboard focus.
    if (this.isConnected && !this._loadStarted) void this._load();
    if (this.isConnected) {
      if(this._checkNotificationRoute())this._render();
      const full=this.shadowRoot.querySelector('ha-alarmzentrale-card');if(full)full.hass=value;
      this._updateLiveValues();
      this.shadowRoot.querySelectorAll('ha-selector').forEach(el=>{el.hass=value;});
    }
  }
  _checkNotificationRoute(){
    if(!this._doc||this._dialog||!window.location)return false;
    const query=new URLSearchParams(window.location.search);if(query.get('az_test')&&this._testRouteHandled!==query.get('az_test')+':'+query.get('az_test_action')&&this._canAdmin){this._testRouteHandled=query.get('az_test')+':'+query.get('az_test_action');this._alarmView=false;this._active='diagnostics';this._testRoute={token:query.get('az_test'),operation:query.get('az_test_action')==='mute'?'mute':'stop'};void this._controlActionTest(this._testRoute.operation,this._testRoute.token);return true;}const wasAlarmView=!!this._alarmView;this._alarmView=query.get("alarm")==="1";const routeChanged=wasAlarmView!==this._alarmView;const operation=query.get('az_action'),token=query.get('az_token');
    if(!['mute','disarm'].includes(operation)||!token||this._lastNotificationToken===token)return routeChanged;
    this._lastNotificationToken=token;this._dialog={kind:'notification_confirm',operation,area:query.get('az_area'),token};this._active='overview';
    for(const key of ['az_action','az_area','az_token'])query.delete(key);
    if(window.history?.replaceState)window.history.replaceState(window.history.state,'',window.location.pathname+(query.toString()?'?'+query:'')+window.location.hash);
    return true;
  }
  set route(value){this._route=value;if(this._checkNotificationRoute())this._render();}
  set panel(value) { this._panel = value; }
  connectedCallback() { this._render(); if (this._hass && !this._loadStarted) void this._load();else if(this._doc){this._pollTimer ||= setInterval(()=>void this._pollRuntime(),4000);this._countdownTimer ||= setInterval(()=>this._updateCountdowns(),1000);void this._pollRuntime();} }
  disconnectedCallback() { this._pinDialog?.finish(null);clearTimeout(this._noticeTimer);this._noticeText='';this._stopLearning();clearInterval(this._pollTimer);clearInterval(this._countdownTimer);this._pollTimer=null;this._countdownTimer=null; }

  async _load() {
    if (!this._hass || this._busy) return;
    this._loadStarted = true;
    this._busy = true;
    this._error = "";
    this._render();
    try {
      const runtime=await this._hass.callWS({type:'ha_alarmzentrale/runtime/get'});
      this._runtime=runtime;
      const data=this._canAdmin?await this._hass.callWS({type:'ha_alarmzentrale/editor/get'}):runtime;
      if(!this._canAdmin){
        this._active='overview';this._dialog=null;
        const modes=new Map();
        for(const area of runtime.areas_state||[])for(const mode of area.modes||[]){
          if(!modes.has(mode.key))modes.set(mode.key,{...mode,area_keys:[]});
          modes.get(mode.key).area_keys.push(area.key);
        }
        data.configuration={schema:2,areas:(runtime.areas_state||[]).map(a=>({key:a.key,name:a.name,icon:a.icon})),modes:[...modes.values()],sensors:[]};
      }
      this._doc = data.configuration;
      this._original = clone(data.configuration);
      this._revision = data.revision;
      this._pinUsers = data.pin_users || [];
      this._statuses = runtime.areas_state || [];
      this._diagnostics = data.diagnostics || {enabled:false};
      this._haUsers = data.ha_users || [];
      this._audit = data.audit || [];
      this._safe = this._canAdmin&&Boolean(runtime.safe_to_edit);
      this._dirty = false;
      this._checkNotificationRoute();

      if (!this._doc.areas.some((area) => area.key === this._area)) this._area = this._doc.areas[0]?.key || "";
      if (this._canAdmin&&!this._catalogReady) void this._loadCatalog();
      if(!this._pollTimer)this._pollTimer=setInterval(()=>void this._pollRuntime(),4000);
      if(!this._countdownTimer)this._countdownTimer=setInterval(()=>this._updateCountdowns(),1000);
    } catch (err) {
      this._error = `Konfiguration konnte nicht geladen werden: ${String(err.message || err)}`;
      this._loadStarted = false;
    } finally { this._busy = false; this._render(); }
  }

  async _pollRuntime() {
    if(this._polling||this._busy||!this.isConnected||document.hidden)return;
    this._polling=true;
    try {
      const data=await this._hass.callWS({type:'ha_alarmzentrale/runtime/get'});
      this._runtime=data;this._statuses=data.areas_state||[];if(this._checkNotificationRoute())this._render();
      const header=this.shadowRoot.querySelector('.header-mode');if(header){header.dataset.phase=this._headerPhase();}

      const changed=this._safe!==(this._canAdmin&&Boolean(data.safe_to_edit));
      this._safe=this._canAdmin&&Boolean(data.safe_to_edit);
      if(data.diagnostics)this._diagnostics=data.diagnostics;
      if(data.audit&&!Object.values(this._auditFilter).some(Boolean))this._audit=data.audit;
      this._runtimeError='';
      const view=this.shadowRoot.getElementById('runtime-overview');
      if(view&&!this._dialog){this._captureDetails(view);view.innerHTML=this._overviewContent();this._restoreDetails(view);this._wireRuntime();}
      else if(changed&&!this._dialog&&!(this._active==='diagnostics'&&this.shadowRoot.getElementById('diagnostic-settings')?.open))this._render();
      const lock=this.shadowRoot.getElementById('configuration-lock');if(lock)lock.hidden=this._safe;
      const apply=this.shadowRoot.querySelector('[data-action="diag-apply"]');if(apply)apply.disabled=!this._safe;
      if(this._active==='diagnostics'&&!this._dialog){this._refreshDiagnosticReadouts();const target=this.shadowRoot.getElementById('sensor-test-results');if(target){const temp=document.createElement('div');temp.innerHTML=this._testControls();target.innerHTML=temp.querySelector('#sensor-test-results')?.innerHTML||'';}}
      if(this._active==='audit'&&!this._dialog){const target=this.shadowRoot.getElementById('history-event-list');if(target){const buffer=document.createElement('div');buffer.innerHTML=this._sectionAudit();const updated=buffer.querySelector('#history-event-list');if(updated){const opened=new Set([...target.querySelectorAll('[data-event-group][open]')].map(el=>el.dataset.eventGroup));target.innerHTML=updated.innerHTML;target.querySelectorAll('[data-event-group]').forEach(el=>{el.open=opened.has(el.dataset.eventGroup);});}}}
      this._updateCountdowns();
      this._runtimeError='';
    }catch(error){
      this._runtimeError='Liveverbindung unterbrochen. Bedienung nach erneutem Laden prüfen.';
      const target=this.shadowRoot.getElementById('runtime-error');if(target){target.hidden=false;target.textContent=this._runtimeError;}
    }finally{this._polling=false;}
  }
  _updateCountdowns() {
    this.shadowRoot.querySelectorAll('[data-alarm-start]').forEach(el=>{const n=Math.max(0,Math.floor(Date.now()/1000-Number(el.dataset.alarmStart)));el.textContent=[Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(v=>String(v).padStart(2,'0')).join(':');});
    this.shadowRoot.querySelectorAll('[data-deadline]').forEach(el=>{
      const seconds=Math.max(0,Math.ceil(Number(el.dataset.deadline)-Date.now()/1000));
      el.textContent=seconds?`${seconds} s`:'Wird aktualisiert …';
    });
  }
  get _notice(){return this._noticeText||'';}
  set _notice(value){
    this._noticeText=value;clearTimeout(this._noticeTimer);
    if(value)this._noticeTimer=setTimeout(()=>{this._noticeText='';this.shadowRoot?.getElementById('success-notice')?.remove();},5000);
  }
  _tone(phase){return phase==='triggered'?'error':['arming','pending'].includes(phase)?'warning':phase==='armed'?'good':'neutral';}
  _headerIcon(){return 'wachturm:shield';}
  _headerPhase(){const areas=this._statuses||[];return ['triggered','pending','arming','armed'].find(phase=>areas.some(a=>a.phase===phase))||'disarmed';}
  _eventTone(kind){if(['disarmed','disarm'].includes(kind))return 'neutral';return /alarm_started|alarm_retriggered|failed|fault|offline/.test(kind)?'error':/delay|arming|battery|unstable/.test(kind)?'warning':/normal|armed|acknowledged|succeeded/.test(kind)?'good':'neutral';}
  _modeIcon(mode){return mode?.icon||(/nacht|night/i.test((mode?.name||'')+(mode?.key||''))?'mdi:weather-night':/zuhause|home/i.test((mode?.name||'')+(mode?.key||''))?'mdi:home-account':'mdi:shield-lock-outline');}
  _areaIcon(area){return area?.icon||(area?.key===SABOTAGE_AREA?'mdi:shield-alert-outline':'mdi:home-outline');}
  _currentMode(){const active=(this._statuses||[]).find(a=>a.mode&&a.phase!=='disarmed');return this._doc?.modes?.find(m=>m.key===active?.mode);}
  _eventIcon(kind){
    return ({test_sensor_start:'mdi:flask-outline',test_sensor_stop:'mdi:flask-check-outline',test_notify:'mdi:bell-check-outline',arm:'mdi:shield-lock',armed:'mdi:shield-lock',arming:'mdi:timer-outline',disarm:'mdi:shield-off-outline',disarmed:'mdi:shield-off-outline',alarm_started:'mdi:alarm-light',alarm_retriggered:'mdi:alarm-light',zone_active:'mdi:alert-circle-outline',zone_normal:'mdi:check-circle-outline',sensor_fault:'mdi:lan-disconnect',configuration_save:'mdi:cog-check',mute:'mdi:volume-off',actions_muted:'mdi:volume-off',acknowledge:'mdi:check-all',alarm_acknowledged:'mdi:check-all',diagnostic_battery:'mdi:battery-alert',diagnostic_offline:'mdi:wifi-off',entry_delay_started:'mdi:timer-sand'})[kind]||'mdi:information-outline';
  }
  _phase(value) {return ({disarmed:'Unscharf',arming:'Ausgangszeit',armed:'Scharf',pending:'Standby',triggered:'Alarm'})[value]||value;}
  _explain(value) {
    return String(value||'').replace('Invalid panel credential','PIN ist nicht korrekt.').replace('Panel temporarily locked','PIN-Eingabe vorübergehend gesperrt.').replace('Zone already active:','Kontakt bereits ausgelöst:').replace('Zone not available:','Sensor nicht verfügbar:')
      .replace('Diagnostic reachability not verified:','Erreichbarkeit nicht bestätigt:').replace('Operation not authorized','Keine Berechtigung für diese Bedienhandlung')
      .replace('HA account has no alarm identity','Home-Assistant-Konto unter Benutzer für Wachturm freischalten').replace('Unknown alarm area','Alarmbereich nicht verfügbar').replace('Grant references unknown area/mode','Ein Bereich oder Modus wird noch in Berechtigungen verwendet. Bitte zuerst die Berechtigung aktualisieren.').replace('Device binding references unknown area/mode','Ein Bediengerät verweist noch auf diesen Bereich oder Modus. Bitte zuerst dessen Zuordnung aktualisieren.');
  }
  _eventLabel(value) {return ({alarm_started:'Alarm ausgelöst',alarm_retriggered:'Erneut ausgelöst',armed:'Scharfgeschaltet',arming:'Ausgangszeit gestartet',disarmed:'Deaktiviert',entry_delay_started:'Standby-Zeit gestartet',alarm_acknowledged:'Alarm quittiert',actions_muted:'Alarmaktionen stummgeschaltet',action_window_expired:'Alarmaktionsdauer beendet',zone_fault_warning:'Sensorstörung',configuration_save:'Konfiguration gespeichert',arm:'Scharfschalten',disarm:'Deaktivieren',acknowledge:'Quittieren',mute:'Stummschalten',panel_pin_set:'Lokale PIN eingerichtet',panel_pin_remove:'Lokale PIN entfernt',mode_changed_with_alarm:'Modus bei aktivem Alarm gewechselt',zone_normal:'Kontakt normal',zone_active:'Kontakt ausgelöst',sensor_fault:'Sensor nicht verfügbar',arming_waiting_for_sensor:'Warte auf Sensorbereitschaft',zone_following_entry_delay:'Standby läuft',diagnostic_fault_warning:'Gerätestörung',diagnostic_battery:'Batteriewarnung',diagnostic_offline:'Verbindungsverlust',diagnostic_unstable:'Instabile Verbindung',recovery_checked:'Neustartprüfung abgeschlossen',runtime_restored:'Zustand wiederhergestellt',test_sensor_start:'Sensortest gestartet',test_sensor_stop:'Sensortest beendet',test_notify:'Testmeldung gesendet',action_succeeded:'Aktion ausgeführt',action_failed:'Aktion fehlgeschlagen'})[value]||value;}
  _localTime(value) {try{return new Date(typeof value==='number'?value*1000:value).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});}catch{return String(value||'');}}
  _overviewContent() {
    const areas=this._statuses||[],summary=this._runtime?.summary||{phase:this._headerPhase(),mode:this._currentMode()?.key},phase=summary.phase||'disarmed',modes=this._doc?.modes||[],current=modes.find(m=>m.key===summary.mode),alarm=areas.some(a=>a.alarm),active=areas.some(a=>a.phase!=='disarmed'),muted=summary.muted??(alarm&&areas.filter(a=>a.alarm).every(a=>!a.action_window_active));
    const pin=op=>this._runtime?.pin_required?.[op]&&this._runtime?.pin_available;
    const hints=area=>{const map=new Map();for(const [key,z] of Object.entries(area.zones||{})){const triggered=(area.alarm_sources||[]).some(k=>k.replace('entry_timeout:','')===key);const blocking=Object.values(area.checks||{}).some(c=>c.ready===false&&c.reason?.endsWith(z.name));if(!triggered&&!(z.observation==='active'&&(z.active_in_mode||z.deferred||z.ignored||blocking)))continue;const id=z.entity_id+':'+(z.attribute||'');map.set(id,{...z,closed:triggered&&z.observation==='normal',tone:triggered?'error':z.deferred||z.ignored?'warning':'error'});}return [...map.values()];};
    const global=new Map();for(const a of areas)for(const z of hints(a))global.set(z.entity_id+':'+(z.attribute||''),z);
    const label=z=>z.closed?'Wieder geschlossen':z.ignored||z.deferred?'Ignoriert':'Ausgelöst';
    const events=areas.flatMap(a=>(a.events||[]).filter(e=>!e.kind.startsWith('access_')).map(e=>({...e,info:a.zones?.[e.info]?.name||e.info}))).sort((a,b)=>b.at-a.at).slice(0,8);
    return `<div class="hero tone-${this._tone(phase)} ${alarm?'alarm-pulse':''}"><ha-icon icon="${E(alarm?'mdi:alarm-light-outline':({disarmed:'mdi:shield-off-outline',armed:'mdi:shield-check-outline',arming:'mdi:shield-clock-outline',pending:'mdi:shield-alert-outline',triggered:'mdi:alarm-light-outline'})[phase]||'mdi:shield-outline')}"></ha-icon><div><h2>${E(alarm?'Alarm aktiv':current?.name||'Anlage deaktiviert')}</h2><div class="sub">${E(this._phase(phase))}${summary.mixed?' · Mehrere Modi aktiv':''}${muted?' · Stumm':''}${this._runtime?.identity?' · '+E(this._runtime.identity):''}</div></div></div>
      ${summary.deadline?`<p class="countdown-main">${phase==='pending'?'Alarm':'Scharf'} in <strong data-deadline="${summary.deadline}"></strong>${phase==='pending'?' · '+E([...new Set(areas.map(a=>a.pending_source).filter(Boolean))].join(', ')||[...global.values()].map(z=>z.name).join(', ')):''}</p>`:''}
      ${alarm?`<div class="alarm-detail">${summary.alarm_since!=null?`<p class="sub" title="Alarmbeginn: ${E(this._localTime(summary.alarm_since))}">Alarm aktiv seit <strong role="timer" data-alarm-start="${summary.alarm_since}"></strong></p>`:''}${[...global.values()].map(z=>`<div class="ready-line tone-${z.closed?'good':z.tone}"><ha-icon icon="${z.closed?'mdi:check-circle-outline':'mdi:alert-circle-outline'}"></ha-icon><strong>${E(z.name)}</strong><span>${label(z)}</span></div>`).join('')}</div>`:''}
      <div id="runtime-error" class="notice error" ${this._runtimeError?'':'hidden'}>${E(this._runtimeError||'')}</div>
      <div class="actions mode-buttons">${modes.filter(m=>m.area_keys?.length).map(mode=>{const targets=areas.filter(a=>mode.area_keys.includes(a.key)),allowed=pin('arm')||this._runtime?.mode_access?.[mode.key]!==false&&targets.length===mode.area_keys.length&&targets.every(a=>a.arm_modes?.includes(mode.key));const blocked=targets.some(a=>a.checks?.[mode.key]?.ready===false);return `<button class="btn ${current?.key===mode.key&&active?'primary':''}" data-runtime-operation="arm" data-runtime-mode="${E(mode.key)}" ${!allowed||blocked||this._busy||this._runtime?.test_session?.active||current?.key===mode.key&&active?'disabled':''}><ha-icon icon="${E(this._modeIcon(mode))}"></ha-icon>${E(mode.name)}</button>`;}).join('')}<button class="btn" data-runtime-operation="disarm" ${!active||this._busy||!pin('disarm')&&!this._runtime?.global_disarm_allowed?'disabled':''}><ha-icon icon="mdi:shield-off-outline"></ha-icon>Deaktivieren</button>${alarm?`<button class="btn" data-runtime-operation="mute" ${muted||this._busy||!pin('mute')&&!this._runtime?.operation_access?.mute?'disabled':''}><ha-icon icon="${muted?'mdi:volume-off':'mdi:volume-high'}"></ha-icon>Stumm</button><button class="btn" data-runtime-operation="acknowledge" ${this._busy||!pin('acknowledge')&&!this._runtime?.operation_access?.acknowledge?'disabled':''}><ha-icon icon="mdi:check"></ha-icon>Quittieren</button>`:''}</div>
      <details class="sensor-advanced" data-detail-key="readiness"><summary>Bereitschaft und Ausnahmen${this._readinessIndicators()}</summary>${modes.filter(m=>m.area_keys?.length).map(m=>{
        const targets=areas.filter(a=>m.area_keys.includes(a.key)),issues=[...new Set(targets.map(a=>a.checks?.[m.key]?.reason).filter(Boolean))],rights=this._runtime?.mode_issues?.[m.key];
        const exceptions=[...new Map(targets.flatMap(a=>a.checks?.[m.key]?.exceptions||[]).map(x=>[x.entity_id+':'+(x.attribute||''),x])).values()];
        const messages=issues;
        return `<section class="readiness-mode"><div class="readiness-head"><strong><ha-icon icon="${E(this._modeIcon(m))}"></ha-icon>${E(m.name)}</strong><span class="${messages.length||rights&&!pin('arm')?'tone-error':exceptions.length?'tone-warning':'tone-good'}">${messages.length||rights&&!pin('arm')?'Blockiert':exceptions.length?'Bereit mit Ausnahme':'Bereit'}</span></div>${rights&&!pin('arm')?`<div class="tone-error">${E(rights)}</div>`:''}${messages.map(text=>`<div class="tone-error">${E(this._explain(text).replace('Kontakt bereits ausgelöst:','Ausgelöst:'))}</div>`).join('')}${exceptions.map(x=>`<div class="tone-warning">${E(x.name)} · Ignoriert</div>`).join('')}</section>`;
      }).join('')}</details>
      <details class="sensor-advanced area-details" data-detail-key="areas"><summary>Bereichsdetails</summary><div class="area-stack">${[...areas].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA)).map(area=>`<div class="area-detail-row"><div class="area-heading"><ha-icon class="tone-${this._tone(area.phase)}" icon="${E(this._areaIcon(area))}"></ha-icon><strong>${E(area.name)}</strong>${hints(area).map(z=>`<span class="tone-${z.closed?'good':z.tone}">${label(z)}: ${E(z.name)}</span>`).join('')}</div><span class="sub">${E(this._phase(area.phase))}</span>${area.arming_block_reason?`<span class="tone-error">${E(this._explain(area.arming_block_reason))}</span>`:''}${area.alarm&&area.alarm_actions===0?'<span class="tone-warning">Keine Alarmaktion eingerichtet</span>':''}</div>`).join('')}</div></details>
      ${events.length?`<h3>Letzte Ereignisse</h3>${events.map(e=>`<div class="list-item"><div class="item-title"><ha-icon class="tone-${this._eventTone(e.kind)}" icon="${this._eventIcon(e.kind)}"></ha-icon><div><strong>${E(this._eventLabel(e.kind))}</strong>${e.info?`<div class="sub">${E(e.info)}</div>`:''}</div></div><span class="sub">${E(this._localTime(e.at))}</span></div>`).join('')}`:'<p class="sub">Noch keine Ereignisse.</p>'}`;
  }
  _readinessIndicators(){
    const counts={good:0,warning:0,error:0};
    for(const mode of this._doc?.modes||[]){if(!mode.area_keys?.length)continue;const rows=(this._statuses||[]).filter(a=>mode.area_keys.includes(a.key)).map(a=>a.checks?.[mode.key]).filter(Boolean);const denied=this._runtime?.mode_issues?.[mode.key]&&!this._runtime?.pin_required?.arm;const tone=denied||rows.some(c=>c.ready===false)?'error':rows.some(c=>c.exceptions?.length)?'warning':'good';counts[tone]++;}
    return `<span class="readiness-indicators">${[['good','mdi:check-circle','Bereit'],['warning','mdi:alert-circle','Ausnahmen'],['error','mdi:shield-alert','Blockiert']].filter(([key])=>counts[key]).map(([key,icon,label])=>`<span class="tone-${key}" title="${label}: ${counts[key]}" aria-label="${label}: ${counts[key]}"><ha-icon icon="${icon}" aria-hidden="true"></ha-icon>${counts[key]}</span>`).join('')}</span>`;
  }
  _wireDialogFocus() {
    const dialog=this.shadowRoot.querySelector('.modal');
    if(!dialog)return;
    const focusable=()=>[...dialog.querySelectorAll('button,input:not([type="hidden"]),select,textarea,summary,ha-selector')].filter(el=>!el.disabled&&el.getClientRects().length);
    const first=focusable()[0];if(first)first.focus();
    dialog.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&!this._busy){event.preventDefault();this._stopLearning();this._dialog=null;this._render();return;}
      if(event.key==='Tab'){
        const elements=focusable();if(!elements.length)return;
        const active=this.shadowRoot.activeElement;
        if(event.shiftKey&&active===elements[0]){event.preventDefault();elements.at(-1).focus();}
        else if(!event.shiftKey&&active===elements.at(-1)){event.preventDefault();elements[0].focus();}
      }
    });
  }

  _wireRuntime() {
    this.shadowRoot.querySelectorAll('[data-runtime-operation]').forEach(button=>button.addEventListener('click',()=>void this._runtimeCommand(button)));
    this._updateCountdowns();
  }
  async _runtimeCommand(button) {
    if(this._busy||this._pinDialog)return;
    const operation=button.dataset.runtimeOperation;
    let pin;
    if(this._runtime?.pin_required?.[operation]){
      if(!window.alarmzentraleAskPIN)await import('/ha_alarmzentrale_assets/alarmzentrale-card.js');
      pin=await window.alarmzentraleAskPIN(operation==='arm'?'Aktivieren':operation==='disarm'?'Deaktivieren':'Bestätigen',this);if(pin===null)return false;
    }
    this._busy=true;this._error='';
    this.shadowRoot.querySelectorAll('[data-runtime-operation]').forEach(b=>{b.disabled=true;});
    try {
      const result=await this._hass.callWS({type:'ha_alarmzentrale/runtime/command',operation,...(pin?{pin}:{}),...(button.dataset.runtimeArea?{area:button.dataset.runtimeArea}:{}),...(button.dataset.runtimeMode?{mode:button.dataset.runtimeMode}:{}),...(button.dataset.notificationToken?{notification_token:button.dataset.notificationToken}:{})});
      this._runtime=result;this._statuses=result.areas_state||[];this._safe=this._canAdmin&&Boolean(result.safe_to_edit);
      this._notice=this._eventLabel(operation)+' erfolgreich.';return true;
    }catch(error){this._error=this._explain(error.message||error);return false;}
    finally{pin='';this._busy=false;this._render();}
  }
  _sectionSearch(label='Suchen') {return `<label class="field section-search">${E(label)}<input type="search" id="section-search" placeholder="Name, Gerät oder Bereich"></label>`;}
  _itemButtons(index,kind=this._active) {return `<div class="actions"><button class="btn small" data-action="edit" data-index="${index}" ${!this._safe?'disabled':''}>Bearbeiten</button><button class="btn small danger" data-action="delete" data-index="${index}" ${!this._safe?'disabled':''}>Löschen</button>${kind==='users'?`<button class="btn small" data-action="pin" data-index="${index}" ${!this._safe?'disabled':''}>${this._pinUsers.includes(this._doc.users[index]?.key)?'PIN verwalten':'PIN einrichten'}</button><button class="btn small" data-action="user-activity" data-index="${index}">Aktivität</button>`:''}</div>`;}
  _sectionUsers() {
    const configured=this._doc.users||[],accounts=this._haUsers.filter(u=>!u.system_generated);
    const rows=accounts.map(account=>{
      const index=configured.findIndex(u=>u.ha_user_ids?.includes(account.id)),user=configured[index];
      return `<div class="list-item" data-search="${E(account.name)}"><div><div class="item-title"><ha-icon icon="mdi:account-circle-outline"></ha-icon><span class="name">${E(account.name)}</span></div><div class="sub">Home Assistant · ${!account.active?'HA-Konto inaktiv':user?.enabled?'Für Wachturm aktiv':'Nicht freigeschaltet'}${user?' · '+E((user.roles||[]).map(k=>this._doc.roles?.find(r=>r.key===k)?.name||k).join(', ')||'Individuelle Rechte'):''}</div></div>${user?this._itemButtons(index,'users'):`<button class="btn small" data-action="ha-setup" data-ha-user="${E(account.id)}" ${!this._safe||!account.active?'disabled':''}>Einrichten</button>`}</div>`;
    }).join('');
    const local=configured.map((user,index)=>({user,index})).filter(({user})=>!(user.ha_user_ids||[]).length);
    return `<div class="card"><div class="page-heading"><div><h2>Benutzer</h2><p class="sub">Personen, Zugänge und wirksame Berechtigungen.</p></div><button class="btn primary" data-action="add" ${!this._safe?'disabled':''}>+ Eigener Benutzer</button></div>${this._sectionSearch('Benutzer suchen')}<h3>Home-Assistant-Konten</h3>${rows||'<p class="sub">Keine regulären Konten verfügbar.</p>'}<h3 style="margin-top:24px">Eigene Alarmbenutzer</h3>${local.map(({user,index})=>`<div class="list-item" data-search="${E(user.name)}"><div><span class="name">${E(user.name)}</span><div class="sub">${user.enabled?'Aktiv':'Deaktiviert'} · ${this._pinUsers.includes(user.key)?'Lokale PIN eingerichtet':'Keine lokale PIN'} · ${(this._doc.bindings||[]).filter(b=>b.user===user.key).length} Bediengeräte</div></div>${this._itemButtons(index,'users')}</div>`).join('')||'<p class="sub">Noch keine eigenen Benutzer angelegt.</p>'}<details id="pin-policy" class="sensor-advanced"><summary>PIN-Schutz je Bedienhandlung</summary><p class="sub">Zuerst eine lokale PIN beim gewünschten Benutzer einrichten. Die PIN wird nur auf dem Server geprüft.</p>${[['arm','Aktivieren'],['disarm','Deaktivieren'],['mute','Stummschalten'],['acknowledge','Quittieren']].map(([op,label])=>this._switch('policy-pin-'+op,label,this._pinDraft?.[op]??this._runtime?.pin_required?.[op]??false)).join('')}<button class="btn" data-save-pin-policy ${!this._safe?'disabled':''}>PIN-Regeln speichern</button><div id="pin-policy-message" class="${this._pinMessageError?'tone-error':'tone-good'}" role="status">${E(this._pinMessage||'')}</div></details><button class="btn" data-backup>Konfiguration sichern</button></div>`;
  }
  async _savePINPolicy(){
    if(this._busy)return;
    this._pinDraft=Object.fromEntries(['arm','disarm','mute','acknowledge'].map(op=>[op,this._checked('policy-pin-'+op)]));
    const tell=(text,error)=>{this._pinMessage=text;this._pinMessageError=error;const target=this.shadowRoot.getElementById('pin-policy-message');if(target){target.textContent=text;target.className=error?'tone-error':'tone-good';}};
    if(Object.values(this._pinDraft).some(Boolean)&&!this._pinUsers.length){tell('Zuerst eine Benutzer-PIN einrichten.',true);return;}
    try{const candidate=clone(this._doc);candidate.operation_pin=this._pinDraft;await this._persistCandidate(candidate,'',false);this._pinDraft=null;tell('PIN-Regeln gespeichert.',false);}catch(e){tell(this._explain(e.message||e),true);}
  }
  async _downloadBackup(){try{const data=await this._hass.callWS({type:'ha_alarmzentrale/editor/backup'});const blob=new Blob([JSON.stringify(data.configuration,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='alarmzentrale-sicherung-'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);this._notice='Sicherung heruntergeladen.';}catch(e){this._error=e.message||String(e);}this._render();}
  async _runTest(operation){if(this._busy||!['sensor_start','sensor_stop'].includes(operation))return;this._busy=true;try{const data=await this._hass.callWS({type:'ha_alarmzentrale/runtime/test',operation});this._runtime.test_session=data.test_session;this._notice=operation==='sensor_start'?'Sensortest aktiv. Alarmaktionen werden unterdrückt.':'Sensortest beendet.';}catch(e){this._error=e.message||String(e);}finally{this._busy=false;await this._pollRuntime();this._render();}}
  _testControls(){const session=this._runtime?.test_session||{};const checks=(this._doc.sensors||[]).flatMap(sensor=>[...(sensor.entities||[]),...(sensor.sabotage?[{...sensor.sabotage,name:sensor.name+' · Sabotage'}]:[])]);
    return `${this._runtime?.simulation?.active?`<div class="notice warning">Aktionstest aktiv · endet automatisch nach 60 Sekunden.<button class="btn small" data-simulation-stop>Test beenden</button></div>`:''}<details class="sensor-advanced" ${session.active?'open':''}><summary>Geführte Funktionstests</summary><h3>Sensortest ohne Alarmaktionen</h3><p class="sub">Anlage vorher deaktivieren. Kontakte öffnen und schließen, anschließend Sabotagekontakte prüfen. Während des Tests sind Aktivieren und Alarmaktionen gesperrt. Automatisches Ende nach 15 Minuten.</p><div class="test-controls"><button class="btn" data-test="sensor_start" ${!this._safe||session.active?'disabled':''}>Sensortest starten</button><button class="btn" data-test="sensor_stop" ${!session.active?'disabled':''}>Test beenden</button></div><div id="sensor-test-results">${session.active?checks.map(c=>{const sample=(session.samples||[]).find(x=>x.entity_id===c.entity_id);return `<div class="list-item"><span>${E(c.name||this._entityLabel(c.entity_id))}</span><span class="${sample?.closed?'tone-good':'tone-warning'}">${sample?.closed?'Auslösung und Normalisierung erkannt':sample?.opened?'Auslösung erkannt · jetzt schließen':'Öffnen / auslösen'}</span></div>`;}).join(''):''}</div></details>`;
  }
  _sectionSharedModern() {
    if(this._active==='users')return this._sectionUsers();
    const kind=this._active,items=this._doc[kind]||[],roles=kind==='roles';
    return `<div class="card"><div class="page-heading"><div><h2>${roles?'Rollen':'Bediengeräte'}</h2><p class="sub">${roles?'Berechtigungen einmal festlegen und mehreren Personen zuweisen.':'Homematic-Keypads, Fernbedienungen und Taster mit eindeutigen Bedienhandlungen verbinden.'}</p></div><button class="btn primary" data-action="add" ${!this._safe?'disabled':''}>+ ${roles?'Rolle':'Bediengerät'}</button></div>${this._sectionSearch()}${items.map((item,index)=>{
      const grants=(item.grants||[]).map(g=>`${this._doc.areas.find(a=>a.key===g.area)?.name||g.area}: ${g.operations.map(op=>PERMS.find(p=>p[0]===op)?.[1]||op).join(', ')}`).join(' · ');
      const desc=roles?grants:`${this._doc.users?.find(u=>u.key===item.user)?.name||item.user} · ${PERMS.find(p=>p[0]===item.operation)?.[1]||item.operation} · ${this._doc.areas.find(a=>a.key===item.area)?.name||item.area}${item.mode?' · '+(this._doc.modes.find(m=>m.key===item.mode)?.name||item.mode):''}`;
      return `<div class="list-item" data-search="${E((item.name||item.key)+' '+desc)}"><div><div class="item-title"><ha-icon icon="${roles?'mdi:account-key-outline':'mdi:remote'}"></ha-icon><span class="name">${E(item.name||item.key)}</span></div><div class="sub">${E(desc||'Noch keine Berechtigungen')}</div>${roles?`<div class="sub">${(this._doc.users||[]).filter(u=>u.roles?.includes(item.key)).length} zugeordnete Personen</div>`:`<div class="sub">${E(item.address)} · Kanal ${E(item.channel)}</div>`}</div>${this._itemButtons(index,kind)}</div>`;
    }).join('')||'<div class="empty-state">Noch keine Einträge. Über „Hinzufügen“ einrichten.</div>'}</div>`;
  }

  _css() { return `<style>
    :host{display:block;min-height:100%;color:var(--primary-text-color,#222);background:var(--primary-background-color,#fafafa);font:14px/1.5 Roboto,Arial,sans-serif;box-sizing:border-box}
    *{box-sizing:border-box} button,input,select,textarea{font:inherit}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.42}
    [hidden]{display:none!important}ha-selector{display:block;min-width:0}
    details.sensor-advanced{border:1px solid var(--divider-color,#ddd);border-radius:10px;padding:12px;margin:12px 0}details.sensor-advanced>summary{cursor:pointer;font-weight:600}details.sensor-advanced[open]>summary{margin-bottom:12px}
    .wrap{max-width:1150px;padding:18px clamp(12px,3vw,30px) 40px;margin:auto}
    .top{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:20px}
    h1{font-size:23px;line-height:1.25;margin:0;font-weight:600}h2{font-size:17px;margin:0 0 12px}h3{font-size:15px;margin:0 0 10px}
    .sub{color:var(--secondary-text-color,#777);font-size:12px}.top .sub{margin-top:3px}.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .btn{min-height:38px;padding:7px 13px;border:1px solid var(--divider-color,#ccc);border-radius:9px;background:var(--card-background-color,#fff);color:var(--primary-text-color,#222);font-weight:500}
    .btn.primary{background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff);border-color:transparent}
    .btn.danger{border-color:var(--error-color,#db4437);color:var(--error-color,#db4437)}.btn.small{min-height:32px;padding:5px 10px}
    .btn:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--primary-color,#03a9f4);outline-offset:2px}
    .tabs{display:flex;gap:4px;overflow:auto;scrollbar-width:thin;margin:0 0 16px;border-bottom:1px solid var(--divider-color)}
    .tab{min-height:44px;border:0;border-bottom:3px solid transparent;background:transparent;padding:9px 12px;color:var(--secondary-text-color,#777);white-space:nowrap}
    .tab[aria-selected=true]{border-color:var(--primary-color,#03a9f4);color:var(--primary-text-color,#222);font-weight:600}
    .card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:12px;padding:16px;margin-bottom:12px}
    .row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.row.flex-start{justify-content:flex-start}
    .list-item.sensor-card{border:1px solid var(--divider-color,#ddd);border-radius:12px;padding:16px!important;margin-top:16px;align-items:flex-start}.list-item.sensor-card:last-child{border:1px solid var(--divider-color,#ddd)}
    .sensor-title{display:flex;align-items:center;gap:10px;margin-bottom:8px}.sensor-title ha-icon{color:var(--primary-color,#03a9f4);--mdc-icon-size:28px}.sensor-card .checkbox-wrap{margin-top:12px}.entity-statuses{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.entity-status{display:flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid var(--divider-color,#ddd);border-radius:8px}.entity-status ha-icon{--mdc-icon-size:22px;color:var(--status-color)}.entity-status.good{--status-color:var(--success-color,#43a047)}.entity-status.active{--status-color:var(--error-color,#db4437)}.entity-status.warning{--status-color:var(--warning-color,#ef9b0f)}.entity-status.unknown{--status-color:var(--secondary-text-color,#888)}.entity-status.info{--status-color:var(--primary-color,#03a9f4)}
    .list-item{padding:12px 0;display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--divider-color,#ddd)}.list-item:last-child{border:0;padding-bottom:0}
    .name{font-weight:600;overflow-wrap:anywhere}.muted{color:var(--secondary-text-color,#777)}.pill{padding:4px 9px;border-radius:99px;background:var(--secondary-background-color,#eee);font-size:12px;white-space:nowrap}
    .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.metric{padding:13px;border:1px solid var(--divider-color,#ddd);border-radius:10px}.metric b{font-size:21px;display:block}
    .notice{padding:11px 13px;border:1px solid var(--divider-color,#ddd);border-left:4px solid var(--primary-color);border-radius:7px;margin-bottom:12px}
    .notice.error{border-left-color:var(--error-color,#d33)}.notice.warning{border-left-color:#db9000}
    .form-grid.sensor-header{align-items:end;grid-template-columns:repeat(2,minmax(0,1fr))}.sensor-header #sensor-device-picker{grid-column:1/-1;min-width:0}.sensor-header ha-selector{width:100%}@media(max-width:480px){.form-grid.sensor-header{grid-template-columns:minmax(0,1fr)}}.sensor-header input,.sensor-header select{height:56px}.sensor-header #sensor-icon-picker,.sensor-header #sensor-device-picker{display:flex;flex-direction:column;gap:4px}
    .form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;align-items:end}
    label.field{display:flex;flex-direction:column;gap:5px;font-weight:500}.field input,.field select,.field textarea{min-height:38px;width:100%;border:1px solid var(--divider-color,#aaa);background:var(--card-background-color,#fff);color:var(--primary-text-color,#222);border-radius:8px;padding:7px 10px}
    .field textarea{resize:vertical;min-height:70px}.check{display:flex;gap:7px;align-items:center;font-weight:400;min-height:34px}.check input{width:17px;height:17px;accent-color:var(--primary-color,#03a9f4)}
    fieldset{border:1px solid var(--divider-color,#ddd);border-radius:9px;margin:12px 0;padding:10px}legend{font-weight:600;padding:0 5px}.row-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;align-items:center}
    .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:12px;z-index:5}
    .modal{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);color:var(--primary-text-color,#222);width:min(780px,100%);max-height:calc(100dvh - 28px);overflow:auto;border-radius:14px;box-shadow:0 8px 40px #0004}
    .modal-head{position:sticky;top:0;background:var(--card-background-color,#fff);padding:15px 18px;border-bottom:1px solid var(--divider-color,#ddd);z-index:1}
    .modal-content{padding:15px 18px}.modal-foot{position:sticky;bottom:0;background:var(--card-background-color,#fff);padding:12px 18px;border-top:1px solid var(--divider-color,#ddd);display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}
    .checkbox-wrap{display:flex;gap:8px 16px;flex-wrap:wrap}.entity-picker{max-height:190px;overflow-y:auto;border:1px solid var(--divider-color,#ddd);border-radius:9px;padding:6px 10px;margin:7px 0}.entity-picker .check{border-bottom:1px solid var(--divider-color,#ddd);padding:4px 0}.entity-picker .check:last-child{border:0}
    .area-select{min-height:38px;border-radius:9px;border:1px solid var(--divider-color,#ddd);padding:7px;background:var(--card-background-color,#fff);color:var(--primary-text-color)}
    .menu{display:none}@media(max-width:750px){.menu{display:inline-flex}.top{align-items:flex-start}.top .actions{width:100%}.modal-bg{padding:5px}.modal{max-height:calc(100dvh - 10px)}.btn{min-height:41px}.wrap{padding-top:12px}.row .actions{justify-content:flex-start}}
    .wrap{max-width:1280px;padding-top:24px}.top{margin-bottom:24px}.top h1{font-size:27px;letter-spacing:-.5px}.top ha-icon{--mdc-icon-size:19px}.top .btn{display:inline-flex;align-items:center;gap:6px}
    .tabs{gap:6px;padding:6px;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:12px;margin-bottom:24px;flex-wrap:wrap;overflow:visible}.tab{border:0;border-radius:8px;min-height:40px;padding:8px 13px}.tab[aria-selected=true]{background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff)}
    .card{padding:22px;border-radius:16px}.list-item{padding:18px 0}.list-item>div:first-child{min-width:0;flex:1}.name{font-weight:600}.list-item .actions{flex-shrink:0}.list-item .sub{margin-top:4px}.list-item .checkbox-wrap{margin-top:10px}
    .modal{max-width:1000px;border-radius:18px}.modal-content{padding:22px}.modal-head{padding:20px 22px}.modal-head .sub{margin-top:5px}.modal-foot{padding:16px 22px}.modal-content>.form-grid{margin-bottom:18px}.modal-content>fieldset{margin-top:18px}details.sensor-advanced{padding:14px;margin:16px 0;border-radius:12px}.modal-head .sub{display:none}
    .page-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:20px;flex-wrap:wrap}.page-heading h2{margin:0}.page-heading p{margin:5px 0 0}.page-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:16px;margin:18px 0}.status-panel{border:1px solid var(--divider-color,#ddd);border-radius:14px;padding:18px}.status-panel h3{display:flex;align-items:center;gap:9px}.status-panel .actions{margin-top:14px}.status-panel .pill{margin:4px 5px 4px 0}.hero{display:flex;align-items:center;gap:10px;margin:0 0 12px}.hero>div{min-width:0}.hero>ha-icon{flex-shrink:0;--mdc-icon-size:32px;color:var(--primary-color,#03a9f4)}.hero.error>ha-icon{color:var(--error-color,#db4437)}.hero h2{font-size:18px;line-height:1.3;margin:0;overflow-wrap:anywhere}.hero .sub{font-size:12px;line-height:1.4;margin-top:2px;overflow-wrap:anywhere}.mode-buttons .btn{min-height:48px;display:inline-flex;gap:9px;align-items:center}.ready-line{display:flex;align-items:flex-start;gap:8px;margin:7px 0}.ready-line ha-icon{--mdc-icon-size:19px}.tone-good{color:var(--success-color,#43a047)}.tone-warning{color:var(--warning-color,#ef9b0f)}.tone-error{color:var(--error-color,#db4437)}
    .rule-table{color:var(--primary-text-color,#222);width:100%;border-collapse:collapse;margin:12px 0}.rule-table td,.rule-table th{text-align:left;padding:10px 6px;border-bottom:1px solid var(--divider-color,#ddd);vertical-align:middle}.rule-table select{max-width:100%}.rule-table .field{margin:0}.rule-table .sub{display:block}.table-wrap{overflow:auto}.rule-group{margin:18px 0}.rule-group>summary{font-weight:600;cursor:pointer;padding:10px 0}.empty-state{padding:32px 12px;text-align:center;color:var(--secondary-text-color,#777)}.empty-state ha-icon{--mdc-icon-size:36px;display:block;margin:auto auto 12px}.section-search{max-width:320px;margin-bottom:18px}.saving-state{color:var(--secondary-text-color,#777)}.source-note{display:inline-flex;gap:6px;align-items:center}.source-note ha-icon{--mdc-icon-size:16px}.member-summary{display:block;margin:5px 0 0}.item-title{display:flex;align-items:center;gap:9px}.item-title ha-icon{--mdc-icon-size:24px;color:var(--primary-color,#03a9f4)}.identity-banner{margin-bottom:16px}.diagnostic-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:16px}.device-facts{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}.detail-actions{margin-top:16px}.pill{font-size:12px}.filter-row{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-bottom:18px}.filter-row .field{flex:1;min-width:160px}.modal-foot button[disabled]{opacity:.6}
    .area-stack{display:flex;flex-direction:column;gap:14px;margin:18px 0}.area-stack .status-panel{display:flex;align-items:center;justify-content:space-between;gap:24px}.area-status{min-width:0}.area-controls{justify-content:flex-end;flex-shrink:0}.mode-times{margin-top:18px;gap:16px}.mode-times .field{gap:8px}.sensor-mode-settings{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:end}.sensor-mode-settings .field{gap:7px}.history-group{border:1px solid var(--divider-color);border-radius:12px;margin:12px 0;padding:12px}.history-group>summary{cursor:pointer;display:flex;align-items:center;gap:10px;flex-wrap:wrap}.history-group>summary:before{content:'▸'}.history-group[open]>summary:before{content:'▾'}.history-group .list-item:last-child{border-bottom:0}
    .hero.tone-good>ha-icon{color:var(--success-color,#43a047)}.hero.tone-warning>ha-icon{color:var(--warning-color,#ef9b0f)}.hero.tone-error>ha-icon{color:var(--error-color,#db4437)}.hero.tone-neutral>ha-icon{color:var(--secondary-text-color)}.hero h2{color:inherit}.status-good{color:var(--success-color,#43a047);background:color-mix(in srgb,var(--success-color,#43a047) 15%,transparent)}.status-warning{color:var(--warning-color,#ef9b0f)}.status-error{color:var(--error-color,#db4437)}.header-mode{--mdc-icon-size:32px}.duration-controls{display:flex;align-items:flex-start;gap:8px}.duration-controls .field{gap:5px;min-width:0;flex:1}.duration-controls input[type=number]{width:100%;box-sizing:border-box}.duration-controls>span{padding-top:10px}.duration{padding:8px 0}.duration input:disabled{opacity:.45}.modal-content>[data-native-icon]{margin:12px 0 18px}.toggle{position:relative}.toggle input{position:absolute;opacity:0;width:36px;height:24px;left:0;z-index:1}.toggle-track{pointer-events:none;width:36px;height:20px;border-radius:12px;background:var(--disabled-text-color,#777);position:relative;display:inline-block;flex-shrink:0}.toggle-track:after{content:'';position:absolute;top:3px;left:3px;width:14px;height:14px;border-radius:50%;background:var(--primary-background-color,#fff)}.toggle input:checked+.toggle-track{background:var(--primary-color,#03a9f4)}.toggle input:checked+.toggle-track:after{left:19px}.toggle input:focus-visible+.toggle-track{outline:2px solid var(--primary-color);outline-offset:3px}.sensor-card{padding:12px!important;gap:12px!important}.sensor-card .entity-status{padding:5px 8px;gap:6px}.sensor-card .entity-statuses{margin:8px 0}.sensor-links{margin-top:7px}.sensor-links>summary{cursor:pointer;color:var(--secondary-text-color)}.sensor-mode-settings{gap:18px}.sensor-mode-settings .field{display:flex;flex-direction:column;gap:9px}.form-grid{row-gap:18px}.trend-box{display:grid;gap:6px;min-width:160px;flex:1}.trend-box svg{max-width:100%!important;height:110px!important}.notification-options{margin:16px 0}.notice-preview{white-space:pre-wrap}
    .top .header-mode{--mdc-icon-size:44px;width:48px;height:48px}.top{height:96px;box-sizing:border-box;flex-wrap:nowrap;position:relative;gap:20px}.top>div:first-child{min-width:0;flex-shrink:0}.message-slot{display:flex;flex-direction:column;height:86px;min-width:0;flex:1;overflow:auto;text-align:right;align-self:center}.header-message-stack{margin-block:auto}.message-slot .notice{margin:2px 0 5px;padding:6px 10px;font-size:12px}.message-slot .notice[hidden]{display:none}.list-heading{display:flex;align-items:center;gap:10px}.list-heading ha-icon{flex:0 0 28px;--mdc-icon-size:25px}.list-description{margin-left:38px}.sensor-card .list-description{margin-left:0}.status-inline{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}.status-inline .sub{margin:0}.area-detail-row{display:flex;flex-direction:column;gap:6px;padding:10px 0;border-bottom:1px solid var(--divider-color)}.area-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.countdown-main{font-size:20px;margin:10px 0 20px}.area-details{opacity:.9}.alarm-detail{margin:16px 0}.test-controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.graph-inspect{position:relative}.graph-inspect svg{pointer-events:none}.graph-value{position:absolute;bottom:8px;left:8px;right:8px;padding:8px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;box-shadow:0 3px 12px #0004;pointer-events:none}.graph-value[hidden]{display:none}.graph-inspect:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
    .readiness-mode{padding:14px 0;border-bottom:1px solid var(--divider-color)}.readiness-mode:last-child{border:0}.readiness-head{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:8px}.readiness-head strong{display:flex;align-items:center;gap:8px}.readiness-mode>div:not(.readiness-head){margin:6px 0 0 32px}.list-item.sensor-card{display:block}.sensor-main-row{display:flex;align-items:center;gap:16px;flex-wrap:wrap}.sensor-main-row .sensor-title{margin:0;flex:0 1 auto}.sensor-main-row .entity-statuses{margin:0;flex:1}.sensor-main-row .actions{margin-left:auto}.sensor-card .sensor-links{margin-top:9px}.alarm-pulse>ha-icon{animation:panel-alarm-pulse 1.3s ease-in-out infinite}@keyframes panel-alarm-pulse{0%,100%{transform:scale(.85);filter:brightness(.9)}50%{transform:scale(1.18);filter:brightness(1.4) drop-shadow(0 0 10px var(--error-color,#f44336))}}@media(prefers-reduced-motion:reduce){.alarm-pulse>ha-icon{animation:none}}#pin-policy-message{min-height:20px;margin-top:10px}
    @media(max-width:760px){.top{height:100px;align-items:center;gap:12px}.top .row{flex-wrap:nowrap;gap:8px}.top h1{font-size:19px;white-space:nowrap}.top .header-mode{--mdc-icon-size:30px;width:30px;height:34px;flex:0 0 30px}.top>div:first-child{flex:0 0 58%;min-width:0}.top .row>div{min-width:0}.top .sub{font-size:10px}.message-slot{height:92px;flex:1;text-align:right}.message-slot .notice{font-size:11px;padding:5px}.readiness-head{gap:12px}}
    @media(max-width:760px){.area-stack .status-panel{align-items:flex-start;flex-direction:column}.area-controls{flex-wrap:wrap;justify-content:flex-start}.sensor-mode-settings{grid-template-columns:1fr}.modal .rule-table thead{display:none}.modal .rule-table tr{display:flex;flex-direction:column;padding:10px 0;border-bottom:1px solid var(--divider-color)}.modal .rule-table td{border:0;width:100%;box-sizing:border-box}.modal .rule-table select{width:100%;min-width:0}}
    @media(max-width:600px){.wrap{padding:12px}.card{padding:16px}.top h1{font-size:23px}.tab{padding:7px 10px}.modal-content{padding:16px}.rule-table td,.rule-table th{padding:8px 4px}.page-grid{grid-template-columns:minmax(0,1fr)}.list-item{align-items:flex-start;flex-direction:column}.list-item .actions{width:100%}.hero h2{font-size:18px}}
  .sabotage-sensor-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:16px}.sabotage-sensor-content{min-width:0}.sabotage-sensor-content .item-title{display:flex;align-items:center;gap:8px;overflow-wrap:anywhere}.sabotage-sensor-content .item-title ha-icon{flex-shrink:0}.sabotage-sensor-content .entity-statuses{margin-top:10px}.sabotage-sensor-content .entity-status{max-width:100%;box-sizing:border-box;flex-wrap:wrap}.list-item.action-card:last-child{border:1px solid var(--divider-color);padding-bottom:12px}.readiness-indicators{float:right;display:inline-flex;gap:12px;align-items:center;margin-left:12px}.readiness-indicators span{display:inline-flex;align-items:center;gap:4px}.readiness-indicators ha-icon{--mdc-icon-size:20px}details[open]>summary .readiness-indicators{display:none}.diagnostic-alarm-launch{border:1px solid var(--divider-color);border-radius:12px;padding:16px;margin-top:20px}.action-test-panel{border:1px solid var(--divider-color);border-radius:12px;padding:14px;margin-top:16px}.action-test-panel .actions{margin-top:12px}.action-test-panel [role=status]{margin-top:10px}@media(max-width:440px){.sabotage-sensor-row{grid-template-columns:minmax(0,1fr)}.sabotage-sensor-row>.toggle{justify-self:end}}
  .area-sensor-group>summary{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.area-sensor-group>summary:before{content:"▸"}.area-sensor-group[open]>summary:before{content:"▾"}.area-sensor-group>summary ha-icon{flex-shrink:0;--mdc-icon-size:24px}.area-sensor-group>summary .sub{margin-left:auto}.area-sensor-toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.area-sensor-toolbar .primary{margin-left:auto}.area-sensor-group .sensor-card{border:1px solid var(--divider-color);border-radius:12px;margin:10px 0}.area-sensor-group .sensor-card:last-child{border:1px solid var(--divider-color)}
  .form-grid.area-advanced-settings{align-items:start}.area-advanced-settings>.duration{padding:0;display:flex;flex-direction:column;gap:5px}
  .top .project-link{color:var(--primary-color,#03a9f4);text-decoration:none}.top .project-link:hover{text-decoration:underline}.top .project-link:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
  .top .brand-logo{object-fit:contain;width:48px;height:52px;flex:0 0 48px;user-select:none}.brand-logo{object-fit:contain}@media(max-width:760px){.top .brand-logo{display:block;width:28px;height:32px;flex:0 0 28px}}
  :host{caret-color:transparent}.alarm-screen{position:fixed;inset:0;z-index:10;display:grid;grid-template-rows:minmax(0,1fr) auto;background:var(--primary-background-color,#111);overflow:hidden;box-sizing:border-box;padding:8px;gap:8px}#fullscreen-alarm{min-height:0;height:100%;overflow:hidden}#fullscreen-alarm>ha-alarmzentrale-card{height:100%}.alarm-screen footer{display:flex;justify-content:flex-end;padding:0 8px max(4px,env(safe-area-inset-bottom))}.alarm-screen footer button{display:flex;align-items:center;gap:6px}input,textarea,[contenteditable=true]{caret-color:auto}.top .mobile-menu{display:none}.tabs[hidden]{display:none}.list-item.action-card{border:1px solid var(--divider-color);border-radius:12px;margin:10px 0;padding:12px!important}.header-message-stack{display:flex;flex-direction:column;align-items:flex-end;gap:3px}.message-slot .notice{width:fit-content;max-width:100%;box-sizing:border-box;padding:4px 8px;border:0;border-radius:6px;font-size:12px;background:transparent;display:flex;gap:6px;align-items:center}.message-slot .notice ha-icon{--mdc-icon-size:17px}.message-slot .notice.warning{color:var(--warning-color,#ffb300)}@media(max-width:760px){.top .mobile-menu{display:inline-flex;padding:5px}.top .header-mode:not(.brand-logo){display:none}.top>div:first-child{flex:0 1 auto}.top .sub{max-width:180px}.message-slot{flex:1}}
    </style>`; }

  _captureDetails(root,scope=this._renderedScope||''){
    this._detailStates ||= new Map();
    root?.querySelectorAll('details').forEach((el,i)=>{const key=el.dataset.detailKey||el.id||el.querySelector('summary')?.textContent?.trim()+':'+i;this._detailStates.set(scope+':'+key,el.open);});
  }
  _restoreDetails(root,scope=this._renderedScope||''){
    root?.querySelectorAll('details').forEach((el,i)=>{const key=el.dataset.detailKey||el.id||el.querySelector('summary')?.textContent?.trim()+':'+i;const saved=this._detailStates?.get(scope+':'+key);if(saved!==undefined)el.open=saved;});
  }
  _render() {
    this._captureDetails(this.shadowRoot);
    if(this._dialog&&!this._dialog._detailId)this._dialog._detailId=(this._detailSerial=(this._detailSerial||0)+1);
    const detailScope=this._active+':'+(this._dialog?.kind||'')+':'+(this._dialog?._detailId||'');
    if (!this.isConnected) return;
    if(this._alarmView){
      this.shadowRoot.innerHTML=this._css()+`<main class="alarm-screen"><div id="fullscreen-alarm"></div><footer><button class="btn" data-close-alarm aria-label="Schließen"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon>Schließen</button></footer></main>`;
      this.shadowRoot.querySelector('[data-close-alarm]').addEventListener('click',()=>window.location.assign('/lovelace'));
      void this._mountAlarmCard();return;
    }
    const dirty = "Änderungen werden im jeweiligen Dialog gespeichert";
    const locked = !this._safe;
    this.shadowRoot.innerHTML = this._css() + `<div class="wrap">
      <header class="top"><div><div class="row flex-start"><button class="btn mobile-menu" aria-label="Seitenleiste öffnen"><ha-icon icon="mdi:menu"></ha-icon></button><img class="header-mode brand-logo" src="/ha_alarmzentrale_assets/wachturm-logo.png?v=0.8.2" alt="" aria-hidden="true" draggable="false"><div><h1>Wachturm</h1><div class="sub">${this._canAdmin?'Bedienung und Verwaltung':'Bedienung'} · <a class="project-link" href="https://github.com/tojollinor/wachturm" target="_blank" rel="noopener noreferrer" aria-label="Wachturm auf GitHub öffnen">v0.8.2 · GitHub</a></div></div></div></div>
      <div class="message-slot" aria-live="polite"><div class="header-message-stack">${this._error?`<div class="notice error" role="alert">${E(this._error)}</div>`:""}
      ${this._notice?`<div id="success-notice" class="notice" role="status"><ha-icon icon="mdi:check-circle-outline"></ha-icon> ${E(this._notice)}</div>`:""}
      ${this._doc&&this._canAdmin?`<div id="configuration-lock" class="notice warning" ${locked?'':'hidden'}><ha-icon icon="mdi:lock-outline"></ha-icon>${this._runtime?.test_session?.active?'Sensortest aktiv: Alarmaktionen und Bedienung sind gesperrt.':'Änderungen gesperrt: Anlage deaktivieren und Alarme quittieren.'}</div>`:""}
      </div></div></header>
${this._doc && this._doc.schema!==2 ? `<div class="notice"><div class="row"><span>Ältere Konfigurationsstruktur. Die neue Verwaltung benötigt eine geprüfte Übernahme.</span><button class="btn" data-action="upgrade" ${this._busy||!this._safe||this._dirty?'disabled':''}>Übernahme prüfen</button></div></div>` : ''}
      ${this._doc ? `<nav class="tabs" ${this._alarmView?'hidden':''} aria-label="Wachturm-Verwaltung">${(this._canAdmin?SECTIONS:SECTIONS.filter(([key])=>key==='overview')).map(([key,title])=>`<button class="tab" data-tab="${key}" aria-selected="${this._active===key}">${title}</button>`).join('')}</nav>${this._section()}` : `<div class="card">${this._busy?'Konfiguration wird geladen …':'Keine Konfiguration verfügbar. Bitte Integration und Berechtigungen prüfen.'}</div>`}
      ${this._dialog?this._renderDialog():""}
    </div>`;
    this._renderedScope=detailScope;this._restoreDetails(this.shadowRoot);
    this._wire();
  }

  async _mountAlarmCard(){
    if(!customElements.get('ha-alarmzentrale-card'))await import('./alarmzentrale-card.js?v=0.8.2');
    const host=this.shadowRoot.getElementById('fullscreen-alarm');if(!host||host.firstChild)return;
    const entity=Object.keys(this._hass.states||{}).find(id=>this._hass.states[id].attributes?.alarmzentrale_panel===true&&this._hass.states[id].attributes.integration==='ha_alarmzentrale');
    if(!entity){host.textContent='Zentrales Alarm-Panel noch nicht verfügbar.';return;}
    const card=document.createElement('ha-alarmzentrale-card');card.setConfig({entity,fullscreen:true,test_token:new URLSearchParams(window.location.search).get('az_simulation')||undefined});card.hass=this._hass;host.append(card);
  }
  _section() {
    if(this._alarmView)return `<div id="fullscreen-alarm"></div>`;
    if (this._active === "audit") return this._sectionAudit();
    if (this._doc.schema===2) return this._sectionV2();
    const count = (type) => this._doc[type]?.length || 0;
    if (this._active === "overview") return `<div class="metrics">
      ${[["Bereiche",count("areas")],["Sensoren",this._doc.areas.reduce((n,a)=>n+(a.zones||[]).length,0)],
        ["Benutzer",count("users")],["Bediengeräte",count("bindings")]].map(([label,n])=>`<div class="metric">${E(label)}<b>${n}</b></div>`).join('')}</div>
      <div class="card"><h2>Aktuelle Alarmbereiche</h2>${this._statuses.map(x=>`<div class="list-item"><span class="name">${E(x.key)}</span><span class="pill">${E(x.phase)} · ${E(x.mode || 'unscharf')} ${x.alarm?'· Alarm!':''} ${x.reconciling?'· Wiederherstellung':''}</span></div>`).join('')||'<span class="muted">Keine Daten</span>'}</div>
      <div class="card"><h2>Hinweise</h2><p class="muted">Die bestehende Konfiguration kann oben geprüft in die neue Struktur übernommen werden. Danach werden Änderungen im jeweiligen Dialog gespeichert. Zum Ändern müssen alle Bereiche unscharf und quittiert sein.</p></div>`;
    const key = this._active;
    const perArea = ["modes", "zones", "actions"].includes(key);
    const area = this._doc.areas.find(a=>a.key===this._area);
    const items = key==="areas" ? this._doc.areas : perArea ? (area?.[key] || []) : (this._doc[key]||[]);
    const title = SECTIONS.find(x=>x[0]===key)?.[1]||key;
    return `<div class="card"><div class="row"><div><h2>${E(title)}</h2>${perArea?`<select class="area-select" id="area-choice" aria-label="Alarmbereich">${[...this._doc.areas].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA)).map(a=>`<option value="${E(a.key)}" ${a.key===this._area?'selected':''}>${E(a.name)}</option>`).join('')}</select>`:''}</div>
      <button class="btn primary" data-action="add" ${!this._safe?'disabled':''}>+ Hinzufügen</button></div>
      ${items.length?items.map((item,index)=>`<div class="list-item"><div><div class="name">${E(item.name||item.key)}</div><div class="sub">${E(this._describe(key,item))}</div></div><div class="actions"><button class="btn small" data-action="edit" data-index="${index}" ${!this._safe?'disabled':''}>Bearbeiten</button><button class="btn small danger" data-action="delete" data-index="${index}" ${!this._safe?'disabled':''}>Löschen</button>${key==='users'?`<button class="btn small" data-action="pin" data-index="${index}" ${!this._safe||this._dirty?'disabled':''}>${this._pinUsers.includes(item.key)?'PIN ändern':'PIN setzen'}</button>`:''}</div></div>`).join(''):'<p class="muted">Noch keine Einträge vorhanden.</p>'}
      </div>`;
  }

  _describe(kind, item) {
    if(kind==='areas')return `${item.modes?.length||0} Modi · ${item.zones?.length||0} Zonen · ${item.actions?.length||0} Aktionen`;
    if(kind==='modes')return `Ausgangszeit: ${item.exit_delay_seconds||0}s · Alarmaktionen: ${item.mute_after_seconds==null?'kein Zeitlimit':`${item.mute_after_seconds}s`}`;
    if(kind==='zones')return `${item.entity_id} · ${(item.mode_policies||[]).map(p=>p.mode).join(', ')}`;
    if(kind==='actions')return `${item.domain}.${item.service} · ${(item.entity_ids||[]).join(', ')}`;
    if(kind==='users')return `${item.enabled===false?'Deaktiviert':'Aktiv'} · ${(item.ha_user_ids||[]).length?'Home Assistant':'Nur Wachturm'} · ${(item.roles||[]).join(', ')||'direkte Rechte'} · ${this._pinUsers.includes(item.key)?'lokale PIN gesetzt':'keine lokale PIN'}`;
    if(kind==='bindings')return `${item.event_type} · ${item.address}:${item.channel} · ${item.operation} ${item.mode||''}`;
    if(kind==='roles')return `${item.grants?.length||0} Berechtigungsregeln`;
    return item.key;
  }

  _listFor(kind) {
    if(this._doc.schema===2){
      if(kind==='areas')return this._doc.areas;
      if(kind==='modes')return this._doc.modes;
      if(kind==='zones')return this._doc.sensors;
      if(kind==='actions'){
        const area=this._doc.areas.find(a=>a.key===this._area);
        if(area && !area.actions)area.actions=[];
        return [...new Map(this._doc.areas.flatMap(a=>(a.actions||[]).map(action=>[action.key,action]))).values()];
      }
    }
    if(kind==='areas')return this._doc.areas;
    if(["modes","zones","actions"].includes(kind))return this._doc.areas.find(a=>a.key===this._area)?.[kind];
    if(['users','roles','bindings'].includes(kind)&&!this._doc[kind])this._doc[kind]=[];
    return this._doc[kind];
  }

  _default(kind) {
    if(this._doc.schema===2 && ['areas','modes','zones','actions'].includes(kind))return this._defaultV2(kind);
    const id = `neu_${Math.random().toString(36).slice(2,7)}`;
    switch(kind) {
      case 'areas':return {key:id,name:'Neuer Bereich',modes:[{key:'away',name:'Abwesend'}],zones:[],actions:[],ack_policy:'manual',recovery_fault_grace_seconds:30};
      case 'modes':return {key:id,name:'Neuer Modus',exit_delay_seconds:0,delay_on_mode_change:false,mute_after_seconds:1800,restart_timer_on_retrigger:true};
      case 'zones':return {key:id,name:'Neuer Sensor',entity_id:'binary_sensor.beispiel',initial_policy:'block',fault_policy:'block',active_states:['on'],normal_states:['off'],mode_policies:[]};
      case 'actions':return {key:id,name:'Neue Aktion',domain:'notify',service:'notify',entity_ids:[],data:{message:'Alarm'},repeat_on_retrigger:false,timed:false};
      case 'users':return {key:id,name:'Neuer Benutzer',enabled:false,roles:[],grants:[],ha_user_ids:[],time_windows:[]};
      case 'roles':return {key:id,name:'Neue Rolle',grants:[]};
      case 'bindings':return {key:id,name:'Neue Bedienhandlung',user:'',event_type:'homematic.keypress',device_id:'',address:'',event_action:'press_short',channel:1,area:this._doc.areas[0]?.key||'',operation:'arm',mode:null};
    }
  }

  _input(key,label,value,type='text',help='') {
    if(type==='number'&&(/seconds/.test(key)||/^zone-delay-/.test(key)))return this._duration(key,label,value);
    return `<label class="field">${E(label)}<input data-field="${E(key)}" type="${type}" value="${E(value ?? '')}" ${type==='number'?(['connection-warning','connection-offline'].includes(key)?'step="any"':'min="0" step="1"'):''} ${key==='key'?'pattern="[a-zA-Z0-9_-]+"':''}>${help?`<span class="sub">${E(help)}</span>`:''}</label>`;
  }
  _duration(key,label,value,optional=false){
    const total=Number(value)||0, selectable=optional||['exit_delay_seconds','standby_seconds'].includes(key), enabled=optional?value!=null:!selectable||total>0;
    return `<div class="field duration" data-duration="${E(key)}"><span>${E(label.replace(/ ?\((s|Sekunden)\)/g,''))}</span><input type="hidden" data-field="${E(key)}" value="${total}"><div class="duration-controls">${selectable?`<label class="check"><input type="checkbox" data-duration-enable ${enabled?'checked':''} aria-label="Zeit begrenzen"></label>`:''}<label class="field"><input type="number" min="0" step="1" data-duration-min value="${Math.floor(total/60)}" aria-label="${E(label)} Minuten" ${enabled?'':'disabled'}><span class="sub">Minuten</span></label><span>:</span><label class="field"><input type="number" min="0" max="59" step="1" data-duration-sec value="${total%60}" aria-label="${E(label)} Sekunden" ${enabled?'':'disabled'}><span class="sub">Sekunden</span></label></div></div>`;
  }
  _switch(key,label,yes){return `<label class="check toggle"><input type="checkbox" role="switch" data-field="${E(key)}" ${yes?'checked':''}><span class="toggle-track" aria-hidden="true"></span>${E(label)}</label>`;}
  _wireDurations(){this.shadowRoot.querySelectorAll('[data-duration]').forEach(host=>{
    const min=host.querySelector('[data-duration-min]'),sec=host.querySelector('[data-duration-sec]'),hidden=host.querySelector('[data-field]'),enable=host.querySelector('[data-duration-enable]');
    const update=()=>{const on=!enable||enable.checked;min.disabled=sec.disabled=!on;const m=Number(min.value),v=Number(sec.value);hidden.value=!on?'0':/^\d+$/.test(min.value)&&/^\d+$/.test(sec.value)&&Number.isInteger(m)&&m>=0&&Number.isInteger(v)&&v>=0&&v<60?String(m*60+v):'';};
    [min,sec,enable].filter(Boolean).forEach(el=>el.addEventListener('input',update));
  });}
  _select(key,label,value,choices) {
    return `<label class="field">${E(label)}<select data-field="${E(key)}">${choices.map(([v,l])=>`<option value="${E(v)}" ${String(value??'')===String(v)?'selected':''}>${E(l)}</option>`).join('')}</select></label>`;
  }
  _checkbox(key,label,yes) {
    return `<label class="check"><input type="checkbox" data-field="${E(key)}" ${yes?'checked':''}>${E(label)}</label>`;
  }
  _group(label,content) {return `<fieldset><legend>${E(label)}</legend>${content}</fieldset>`;}
  _choices(items,key,selected) {
    return `<div class="checkbox-wrap">${items.map(x=>this._checkbox(`${key}:${x.key}`,x.name||x.key,selected.includes(x.key))).join('')}</div>`;
  }
  _grants(value) {
    return this._group(this._dialog?.kind==='roles'?'Berechtigungen':'Zusätzliche Berechtigungen',`<div class="table-wrap"><table class="rule-table"><thead><tr><th>Bereich</th>${PERMS.map(([key,label])=>`<th>${E(label)}</th>`).join('')}<th>Modi</th></tr></thead><tbody>${[...this._doc.areas].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA)).map(area=>{const i=this._doc.areas.indexOf(area);
      const grant=(value.grants||[]).find(g=>g.area===area.key)||{operations:[],modes:[]};
      return `<tr class="grant-row" data-index="${i}"><td>${E(area.name)}<input type="hidden" data-field="grant-area-${i}" value="${E(area.key)}"></td>${PERMS.map(([key,label])=>`<td><label class="check"><input type="checkbox" aria-label="${E(area.name+' · '+label)}" data-field="grant-op-${i}-${key}" ${grant.operations.includes(key)?'checked':''}></label></td>`).join('')}<td>${this._checkbox('grant-allmodes-'+i,'Alle Modi',!grant.modes.length)}<div data-grant-modes="${i}" ${!grant.modes.length?'hidden':''}>${this._choices(this._modesForArea(area.key),`grant-mode-${i}-${area.key}`,grant.modes)}</div></td></tr>`;
    }).join('')}</tbody></table></div>`);
  }
  _windows(value) {
    const dayNames=['Mo','Di','Mi','Do','Fr','Sa','So'];
    return this._group('Zeitfenster (leer = jederzeit)',`${(value.time_windows||[]).map((w,i)=>`<fieldset class="window-row" data-index="${i}">
      <div class="row"><strong>Zeitfenster ${i+1}</strong><button type="button" class="btn small danger" data-dialog-action="remove-window" data-index="${i}">Entfernen</button></div>
      <div class="checkbox-wrap">${dayNames.map((day,j)=>this._checkbox(`win-day-${i}-${j}`,day,(w.weekdays||[]).includes(j))).join('')}</div>
      <div class="row-grid">${this._input(`win-start-${i}`,'Von',w.start||'08:00','time')}${this._input(`win-end-${i}`,'Bis',w.end||'18:00','time')}</div></fieldset>`).join('')}
      <button type="button" class="btn small" data-dialog-action="add-window">+ Zeitfenster</button>`);
  }

  _renderDialog() {
    if(this._doc.schema===2 && this._dialog && ['areas','modes','zones','actions','membership'].includes(this._dialog.kind))return this._renderDialogV2();
    const dlg=this._dialog;
    if(dlg.kind==='migration')return this._v2Modal('Neue Konfigurationsstruktur übernehmen',`<p>Die bestehende Konfiguration wurde geprüft. Sensoren, Bereiche und Modi werden in die neue Verwaltung übernommen. Vorhandene Alarmregeln und Berechtigungen bleiben erhalten.</p><p class="sub">${dlg.proposal.sensors.length} Sensoren · ${dlg.proposal.modes.length} Modi · ${dlg.proposal.areas.length} Bereiche</p>`);
    if(dlg.kind==='notification_confirm')return this._v2Modal('Smartphone-Aktion bestätigen',`<p>${dlg.operation==='disarm'?'Anlage deaktivieren?':'Sirene des betroffenen Bereichs stummschalten?'}</p><p class="sub">Deine Alarmrechte und der zugehörige aktive Alarm werden geprüft.</p>`).replace('<button class="btn primary" type="submit">Speichern</button>','<button class="btn primary" type="button" data-dialog-action="notification-confirm">Bestätigen</button>');
    if(dlg.kind==='bindings')return this._v2BindingDialog();
    if(dlg.kind==='pin')return this._renderPinDialog();
    if(dlg.kind==='delete')return this._renderDeleteDialog();
    const item=dlg.item,kind=dlg.kind,area=this._doc.areas.find(x=>x.key===this._area);
    const fields=[`<input type="hidden" data-field="key" value="${E(item.key)}">`];
    fields.push(this._input('name',kind==='bindings'?'Name des Bedienelements':kind==='roles'?'Rollenname':'Anzeigename',item.name||''));
    if(kind==='areas')fields.push(this._select('ack_policy','Alarmquittierung',item.ack_policy||'manual', [['manual','Manuell'],['auto_clear','Automatisch bei Normalisierung']]),this._input('recovery_fault_grace_seconds','Neustart-Schutzzeit (Sekunden)',item.recovery_fault_grace_seconds??30,'number'));
    if(kind==='modes')fields.push(this._input('exit_delay_seconds','Ausgangszeit (Sekunden)',item.exit_delay_seconds??0,'number'),this._input('mute_after_seconds','Alarmtimer (Sekunden; leer = unbegrenzt)',item.mute_after_seconds??'','number'),this._checkbox('delay_on_mode_change','Auch beim Wechsel aus einem anderen Modus verzögern',item.delay_on_mode_change),this._checkbox('restart_timer_on_retrigger','Timer bei neuem Alarmereignis neu starten',item.restart_timer_on_retrigger));
    if(kind==='zones'){
      fields.push(this._input('entity_id','Home-Assistant-Entität',item.entity_id,'text','Zum Beispiel binary_sensor.fenster'),
        this._input('active_states','Auslösende Zustände',(item.active_states||['on']).join(', ')),
        this._input('normal_states','Normale Zustände',(item.normal_states||['off']).join(', ')),
        this._select('initial_policy','Beim Scharfschalten bereits ausgelöst',item.initial_policy||'block',[['block','Scharfschaltung blockieren'],['defer','Bis zur Normalmeldung ausnehmen']]),
        this._select('fault_policy','Bei Sensorstörung',item.fault_policy||'block',[['block','Scharfschaltung blockieren'],['warn','Warnen und zulassen'],['alarm','Alarm auslösen']]));
      fields.push(this._group('Zugeordnete Modi und Eingangsverzögerungen',`<div class="sub">Mindestens einen Modus auswählen.</div>${(area?.modes||[]).map(m=>{
        const p=(item.mode_policies||[]).find(x=>x.mode===m.key);
        return `<fieldset><legend>${E(m.name)}</legend>${this._checkbox(`zone-mode-${m.key}`,'In diesem Modus überwachen',!!p)}
          <div class="row-grid">${this._input(`zone-delay-${m.key}`,'Eingangszeit (s)',p?.entry_delay_seconds??0,'number')}${this._checkbox(`zone-follow-${m.key}`,'Laufende Eingangszeit übernehmen',!!p?.follow_entry_delay)}</div></fieldset>`;
      }).join('')}`));
    }
    if(kind==='actions')fields.push(
      this._input('domain','HA-Domain',item.domain),this._input('service','HA-Service',item.service),
      this._input('entity_ids','Zielentitäten',(item.entity_ids||[]).join(', ')),
      `<label class="field">Service-Daten (JSON, erweiterte Option)<textarea data-field="data" rows="4">${E(JSON.stringify(item.data||{},null,2))}</textarea></label>`,
      this._checkbox('repeat_on_retrigger','Bei erneutem Alarmereignis wiederholen',item.repeat_on_retrigger),
      this._checkbox('timed','Zeitbegrenzter Ausgang (nur light/switch/siren.turn_on)',item.timed));
    if(kind==='roles')fields.push(this._select('role-preset','Vorlage (optional)','custom',[['custom','Individuell'],['resident','Bewohner'],['guest','Gast'],['service','Service – nur Scharfschalten']]),this._grants(item));
    if(kind==='users')fields.push(
      this._checkbox('enabled','Benutzer aktiv',item.enabled!==false),
      this._group('Rollen',this._choices(this._doc.roles||[],'role',item.roles||[])),
      this._select('identity_source','Benutzerquelle',(item.ha_user_ids||[]).length?'ha':'local',[["local","Nur Wachturm"],["ha","Home Assistant"]]),
      this._select('ha_account','Home-Assistant-Konto',(item.ha_user_ids||[])[0]||'',[['','Kein Konto'],...this._haUsers.filter(u=>!u.system_generated&&((item.ha_user_ids||[]).includes(u.id)||!(this._doc.users||[]).some(other=>other.key!==item.key&&other.ha_user_ids?.includes(u.id)))).map(u=>[u.id,`${u.name}${u.active?'':' (inaktiv)'}`])]),
      `<p class="sub">Die Kontoquelle ändert keine Home-Assistant-Anmeldung. Ohne ausdrückliche Aktivierung und Alarmrechte ist kein Zugriff erlaubt. Eine lokale PIN kann zusätzlich eingerichtet werden. ${(item.ha_user_ids||[]).length>1?'Vorhandene Mehrfachzuordnungen bleiben beim Bearbeiten unverändert, solange das Konto nicht gewechselt wird.':''}</p>`,
      this._grants(item),this._windows(item));
    if(kind==='bindings')fields.push(
      this._select('user','Zugeordneter Benutzer',item.user, (this._doc.users||[]).map(u=>[u.key,u.name])),
      this._select('event_type','Ereignistyp',item.event_type,[['homematic.keypress','homematic.keypress'],['homematic.impulse','homematic.impulse']]),
      this._input('device_id','HA-Geräte-ID',item.device_id),this._input('address','Homematic-Adresse',item.address),
      this._input('event_action','Ereignisaktion (type)',item.event_action),this._input('channel','Kanal (subtype)',item.channel,'number'),
      this._input('interface_id','Interface-ID (optional)',item.interface_id||''),
      this._select('area','Alarmbereich',item.area,this._doc.areas.filter(a=>this._modesForArea(a.key).length).map(a=>[a.key,a.name])),
      this._select('operation','Befehl',item.operation,PERMS.map(([x,y])=>[x,y])),
      this._select('mode','Zielmodus (nur beim Scharfschalten)',item.mode||'', [['','Keiner'],...this._doc.areas.flatMap(a=>this._modesForArea(a.key).map(m=>[m.key,`${a.name}: ${m.name}`]))]),
      `<div class="notice warning">Nur Signale des echten Bedienelements übernehmen. Ein HA-Event allein ist kein sicherer Identitätsnachweis.</div>
       <button type="button" class="btn" data-dialog-action="learn">Nächsten Homematic-Tastendruck einlernen</button><div id="learn-status" class="sub" role="status"></div>`);
    return `<div class="modal-bg"><div class="modal" role="dialog" aria-modal="true" aria-label="${E(kind)} bearbeiten">
      <div class="modal-head"><h2>${dlg.index===null?'Hinzufügen':'Bearbeiten'} · ${E(SECTIONS.find(x=>x[0]===kind)?.[1]||kind)}</h2><div class="sub">Änderungen werden mit Speichern direkt übernommen.</div></div>
      <form id="editor-form"><div class="modal-content">
      <datalist id="available-entities">${Object.keys(this._hass?.states||{}).slice(0,500).map(key=>`<option value="${E(key)}"></option>`).join('')}</datalist>
      <div class="form-grid">${fields.filter(x=>x.startsWith('<label class="field">')).join('')}</div>
      ${fields.filter(x=>!x.startsWith('<label class="field">')).join('')}<div id="form-error" class="sub" role="alert"></div></div>
      <footer class="modal-foot"><button class="btn" type="button" data-dialog-action="cancel">Abbrechen</button><button class="btn primary" type="submit">Speichern</button></footer></form></div></div>`;
  }

  _v2BindingDialog() {
    const d=this._dialog,item=d.item,type=item.input_type||'homematic';
    return this._v2Modal('Bediengerät zuordnen',`${this._v2KeyField(d)}${this._input('name','Name der Bedienhandlung',item.name||'')}
      ${this._select('input_type','Auslöser',type,[['homematic','Homematic-Taster / Fernbedienung'],['event','Anderes Tasterereignis'],['entity','Entitätszustand'],['switch','Schalter: An / Aus']])}
      <div id="binding-homematic" ${type==='homematic'?'':'hidden'}><div data-native-device="device_id" data-source-label="Homematic-Gerät">${this._input('device_id','Homematic-Gerät',item.device_id||'')}</div><div class="detail-actions"><button class="btn" type="button" data-dialog-action="learn">Tastendruck einlernen</button><div id="learn-status" class="sub" role="status">Gerät und Tastenereignis zuordnen.</div></div></div>
      <div id="binding-entity" ${['entity','switch'].includes(type)?'':'hidden'}><div data-native-source="control-entity" data-source-label="Bedienentität">${this._input('control-entity','Bedienentität',item.entity_id||'')}</div><div id="binding-states">${this._select('control-match','Auswertung',(item.trigger_states||[]).includes('*')?'change':'state',[['state','Bestimmte Zustände'],['change','Jede Zustandsänderung (z. B. input_button)']])}${this._input('trigger_states','Auslösen bei',(item.trigger_states||['on']).join(', '))}</div></div>
      <div class="form-grid">${this._select('user','Zugeordneter Benutzer',item.user,(this._doc.users||[]).map(u=>[u.key,u.name]))}<div id="binding-operation">${this._select('operation','Bedienhandlung',item.operation,[['arm','Modus aktivieren'],['disarm','Anlage deaktivieren']])}</div><div id="binding-mode">${this._select('mode','Modus',item.mode||'',[['','Modus wählen'],...this._doc.modes.map(m=>[m.key,m.name])])}</div></div>
      <p id="binding-switch-hint" class="sub" ${type==='switch'?'':'hidden'}>An aktiviert den gewählten Modus. Aus deaktiviert die Anlage. Nur tatsächliche Zustandswechsel, keine Start- oder Wiederherstellungswerte.</p>
      <details id="binding-event-fields" class="sensor-advanced"><summary>Ereignis und Zuordnung prüfen</summary><div class="form-grid">${this._input('event_type','Ereignistyp',item.event_type||'homematic.keypress')}${this._input('address','Geräteadresse',item.address||'')}${this._input('event_action','Tastenereignis',item.event_action||'press_short')}${this._input('channel','Kanal',item.channel??1,'number')}${this._input('interface_id','Interface (optional)',item.interface_id||'')}</div><label class="field">Identifizierende Ereignisdaten (JSON)<textarea data-field="event_data">${E(JSON.stringify(item.event_data||{}))}</textarea></label></details>
      <p class="sub">Bedienhandlungen verwenden die Alarmrechte des zugeordneten Benutzers. Gemeinsam verwendete Geräte belegen keine persönliche Identität.</p>`);
  }
  _wireBindingDialog(){
    void this._mountSimpleSources();
    const update=()=>{const type=this._val('input_type'),op=this._val('operation');for(const [id,visible] of Object.entries({'binding-homematic':type==='homematic','binding-entity':['entity','switch'].includes(type),'binding-states':type==='entity','binding-operation':type!=='switch','binding-mode':op==='arm'||type==='switch','binding-switch-hint':type==='switch','binding-event-fields':['homematic','event'].includes(type)})){const el=this.shadowRoot.getElementById(id);if(el)el.hidden=!visible;}const states=this._field('trigger_states');if(states)states.closest('label').hidden=this._val('control-match')==='change';};
    for(const key of ['input_type','operation','control-match'])this._field(key)?.addEventListener('change',update);update();
  }

  _renderPinDialog() {
    return `<div class="modal-bg"><div class="modal" role="dialog" aria-modal="true" aria-label="Lokale PIN verwalten">
      <div class="modal-head"><h2>Lokale Alarmkarten-PIN · ${E(this._dialog.item.name)}</h2><div class="sub">Nicht die PIN des Homematic-Keypads. Nur ein Hash wird gespeichert.</div></div>
      <form id="pin-form"><div class="modal-content">${this._input('pin','Neue numerische PIN (mind. 4 Ziffern)','','password')}
      <p class="sub">Nach der Bestätigung wird die Integration neu geladen. Dieser Vorgang kann nicht mit anderen ungespeicherten Änderungen kombiniert werden.</p><div id="form-error" role="alert"></div></div>
      <footer class="modal-foot"><button type="button" class="btn" data-dialog-action="cancel">Abbrechen</button>${this._pinUsers.includes(this._dialog.item.key)?'<button type="button" class="btn danger" data-dialog-action="remove-pin">PIN entfernen (2×)</button>':''}<button type="submit" class="btn primary">PIN setzen</button></footer></form></div></div>`;
  }
  _renderDeleteDialog(){return `<div class="modal-bg"><div class="modal" role="dialog" aria-modal="true" aria-label="Eintrag löschen"><div class="modal-head"><h2>Eintrag löschen?</h2></div><div class="modal-content">${E(this._dialog.item.name||this._dialog.item.key)} wird dauerhaft aus der Konfiguration entfernt. ${this._dialog.originalKind==='areas'?`<p>Verwendet in Modi: ${E(this._doc.modes.filter(m=>m.area_keys.includes(this._dialog.item.key)).map(m=>m.name).join(', ')||'Keine')}</p><p>Zuordnungen und Bereichsrechte werden entfernt. Modus-Bedienhandlungen bleiben erhalten; ausschließlich bereichsgebundene Sonderbefehle entfallen.</p>`:'Bestehende Verknüpfungen werden vor dem Löschen geprüft.'}<div id="form-error" class="notice error" hidden role="alert"></div></div><footer class="modal-foot"><button class="btn" data-dialog-action="cancel">Abbrechen</button><button class="btn danger" data-dialog-action="confirm-delete">Löschen bestätigen</button></footer></div></div>`;}

  _wire() {
    this.shadowRoot.querySelector(".mobile-menu")?.addEventListener("click",()=>this.dispatchEvent(new CustomEvent("hass-toggle-menu",{bubbles:true,composed:true})));
    if(this._alarmView)void this._mountAlarmCard();
    this.shadowRoot.querySelectorAll('[data-simulation-stop]').forEach(b=>b.addEventListener('click',()=>this._controlActionTest('stop')));
    this.shadowRoot.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{if(this._dialog)return;this._active=b.dataset.tab;this._error='';this._render();}));
    this.shadowRoot.querySelector('[data-action="leave-alarm"]')?.addEventListener('click',()=>{this._alarmView=false;window.history.replaceState(window.history.state,'','/ha-alarmzentrale');this._render();});
    this.shadowRoot.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>this._action(b)));
    const area=this.shadowRoot.getElementById('area-choice');if(area)area.addEventListener('change',()=>{this._area=area.value;this._render();});
    this.shadowRoot.querySelectorAll('[data-dialog-action]').forEach(b=>b.addEventListener('click',()=>this._dialogAction(b)));
    this.shadowRoot.getElementById('editor-form')?.addEventListener('submit',e=>{e.preventDefault();if(this._dialog?.kind==='notification_confirm')this._dialogAction({dataset:{dialogAction:'notification-confirm'}});else this._acceptDialog();});
    const suggested=this.shadowRoot.querySelector('[data-field=entity_id]');if(suggested)suggested.setAttribute('list','available-entities');
    // Show only the modes belonging to each grant's selected area.
    this.shadowRoot.querySelectorAll('.grant-row').forEach(row=>{
      const selector=row.querySelector('[data-field^=grant-area-]');
      const update=()=>row.querySelectorAll('.grant-modes').forEach(group=>{group.hidden=group.dataset.area!==selector.value;});
      if(selector){selector.addEventListener('change',update);update();}
    });
    this.shadowRoot.getElementById('pin-form')?.addEventListener('submit',e=>{e.preventDefault();void this._setPin();});
    this.shadowRoot.querySelectorAll('[data-field^="diagnostic-"]').forEach(field=>field.setAttribute('list','diagnostic-entity-list'));
    this.shadowRoot.querySelectorAll('[data-audit-filter]').forEach(el=>el.addEventListener('change',()=>{this._auditFilter[el.dataset.auditFilter]=el.value;void this._refreshAudit();}));
    this.shadowRoot.querySelectorAll('[data-field^="grant-allmodes-"]').forEach(el=>el.addEventListener('change',()=>{const target=this.shadowRoot.querySelector(`[data-grant-modes="${el.dataset.field.slice(15)}"]`);if(target)target.hidden=el.checked;}));
    if(this._doc?.schema===2)this._wireV2Dialog();
    this._wireRuntime();
    this.shadowRoot.querySelectorAll('[data-test]').forEach(el=>el.addEventListener('click',()=>this._runTest(el.dataset.test)));
    this.shadowRoot.querySelector('[data-backup]')?.addEventListener('click',()=>this._downloadBackup());
    this.shadowRoot.querySelector('[data-save-pin-policy]')?.addEventListener('click',()=>void this._savePINPolicy());
    this.shadowRoot.querySelectorAll('[data-field^="policy-pin-"]').forEach(field=>field.addEventListener('change',()=>{this._pinDraft=Object.fromEntries(['arm','disarm','mute','acknowledge'].map(op=>[op,this._checked('policy-pin-'+op)]));}));
    this._wireDurations();this._wireGraphs();void this._mountSimpleSources();
    this._wireDialogFocus();
    this._field('role-preset')?.addEventListener('change',()=>{const preset=this._val('role-preset');if(preset==='custom')return;const allowed=preset==='resident'?PERMS.map(([key])=>key):preset==='guest'?['arm','disarm']:['arm'];this._doc.areas.forEach((area,i)=>{for(const [key] of PERMS){const field=this._field(`grant-op-${i}-${key}`);if(field)field.checked=allowed.includes(key);}const all=this._field('grant-allmodes-'+i);if(all)all.checked=true;});const name=this._field('name');if(name&&(!name.value||name.value==='Neue Rolle'))name.value=({resident:'Bewohner',guest:'Gast',service:'Service'})[preset];});
    if(this._active==='diagnostics'){this._wireRadioEditors();const add=this.shadowRoot.getElementById('add-radio');if(add)add.onclick=()=>{const rows=[...this.shadowRoot.querySelectorAll('[data-radio-index]')];const index=rows.length?Math.max(...rows.map(row=>Number(row.dataset.radioIndex)))+1:0;this.shadowRoot.getElementById('radio-editors').insertAdjacentHTML('beforeend',this._radioRow('funkmodul_'+Math.random().toString(36).slice(2,9),index,{},'Neues Funkmodul'));this._wireRadioEditors();};}
    if(this._dialog?.kind==='bindings')this._wireBindingDialog();
    this.shadowRoot.getElementById('section-search')?.addEventListener('input',event=>{const query=event.target.value.toLocaleLowerCase('de');this.shadowRoot.querySelectorAll('[data-search]').forEach(row=>{row.hidden=!row.dataset.search.toLocaleLowerCase('de').includes(query);});});
  }

  _action(button) {
    const op=button.dataset.action, idx=Number(button.dataset.index);
    if(this._busy)return;
    if(!this._canAdmin&&['add','edit','delete','pin','upgrade','membership','diag-apply'].includes(op))return;
    if(['sensor-add','sensor-edit','sensor-delete'].includes(op)){
      if(!this._canAdmin||!this._safe)return;
      const area=button.dataset.area;
      if(op==='sensor-add'){
        if(!this._doc.areas.some(a=>a.key===area&&a.key!==SABOTAGE_AREA))return;
        this._dialog={kind:'zones',index:null,area,item:this._default('zones')};
      }else{const sensor=this._doc.sensors[idx];if(!sensor)return;this._dialog={kind:op==='sensor-delete'?'delete':'zones',originalKind:'zones',index:idx,item:clone(sensor)};}
      this._render();return;
    }
    if(op==='audit-refresh'){void this._refreshAudit();return;}
    if(op==='diag-apply'){this._applyDiagnostics();return;}
    if(op==='upgrade'){void this._upgrade();return;}
    if(op==='membership'){
      const sensor=this._doc?.sensors?.[idx];
      const areaKey=button.dataset.area;
      if(sensor&&this._doc.areas.some(a=>a.key===areaKey))this._dialog={kind:'membership',index:idx,area:areaKey,item:clone(sensor)};
      this._render();return;
    }
    if(op==='user-activity'){const user=this._doc.users?.[idx];if(!user)return;this._auditFilter={user_key:user.key,source:'',area:'',action:'',device:'',since:'',until:''};this._active='audit';void this._refreshAudit();return;}
    if(op==='edit-mode'){const index=this._doc.modes.findIndex(m=>m.key===button.dataset.mode);if(index<0)return;this._dialog={kind:'modes',index,item:clone(this._doc.modes[index])};this._render();return;}
    if(op==='ha-setup'){
      const account=this._haUsers.find(u=>u.id===button.dataset.haUser);if(!account)return;
      const item=this._default('users');item.name=account.name;item.ha_user_ids=[account.id];item.enabled=true;
      this._dialog={kind:'users',index:null,item};this._render();return;
    }
    if(op==='menu'){this.dispatchEvent(new Event('hass-toggle-menu',{bubbles:true,composed:true}));return;}
    if(op==='reload'){
      if(this._dirty&&!this._confirmDiscard){this._confirmDiscard=true;this._notice='Zum Verwerfen ungespeicherter Änderungen erneut „Aktualisieren“ drücken.';this._render();return;}
      this._confirmDiscard=false;this._notice='';this._loadStarted=false;void this._load();return;
    }
    const kind=this._active,list=this._listFor(kind);if(!list)return;
    if(this._doc.schema!==2&&['areas','modes','zones','actions'].includes(kind)){this._error='Zuerst die geprüfte Übernahme in die neue Struktur durchführen.';this._render();return;}
    if(op==='add')this._dialog={kind,index:null,item:this._default(kind)};
    if(op==='edit'||op==='delete'||op==='pin')this._dialog={kind:op==='pin'?'pin':op==='delete'?'delete':kind,originalKind:kind,index:idx,item:clone(list[idx])};
    this._render();
  }

  _field(key){return this.shadowRoot.querySelector(`[data-field="${CSS.escape(key)}"]`);}
  _val(key){return this._field(key)?.value?.trim()??'';}
  _checked(key){return Boolean(this._field(key)?.checked);}
  _number(key){const str=this._val(key);if(!/^\d+$/.test(str))throw new Error(`Für ${key} bitte eine ganze Zahl eingeben.`);return Number(str);}
  _modeSelect(area, prefix, values){return this._modesForArea(area?.key).filter(m=>this._checked(`${prefix}${m.key}`)).map(m=>m.key);}
  _readGrants() {
    return [...this.shadowRoot.querySelectorAll('.grant-row')].map((row)=>{const i=Number(row.dataset.index);
      const area=this._val(`grant-area-${i}`),operations=PERMS.filter(([op])=>this._checked(`grant-op-${i}-${op}`)).map(([op])=>op);
      const modes=this._checked('grant-allmodes-'+i)?[]:this._modeSelect(this._doc.areas.find(a=>a.key===area),`grant-mode-${i}-${area}:`,[]);
      if(operations.includes('arm')&&!this._checked('grant-allmodes-'+i)&&!modes.length)throw Error('Für eingeschränkte Scharfschaltrechte mindestens einen Modus auswählen.');
      return {area,operations,modes};
    }).filter(g=>g.operations.length);
  }
  _readWindows() {
    return [...this.shadowRoot.querySelectorAll('.window-row')].map((row,i)=>({
      weekdays:[0,1,2,3,4,5,6].filter(j=>this._checked(`win-day-${i}-${j}`)),
      start:this._val(`win-start-${i}`),end:this._val(`win-end-${i}`),
    }));
  }
  _readDialog() {
    if(this._doc.schema===2 && ['areas','modes','zones','actions','membership'].includes(this._dialog?.kind))return this._readV2Dialog();
    const d=this._dialog,item=clone(d.item); item.key=this._val('key');
    if(!/^[a-zA-Z0-9_-]+$/.test(item.key))throw new Error('Interner Schlüssel: nur Buchstaben, Ziffern, _ oder -');
    item.name=this._val('name')||item.name||item.key;
    if(d.kind==='areas'){item.ack_policy=this._val('ack_policy');item.recovery_fault_grace_seconds=this._number('recovery_fault_grace_seconds');}
    if(d.kind==='modes'){
      item.exit_delay_seconds=this._number('exit_delay_seconds');
      item.mute_after_seconds=this._val('mute_after_seconds')===''?null:this._number('mute_after_seconds');
      item.delay_on_mode_change=this._checked('delay_on_mode_change');item.restart_timer_on_retrigger=this._checked('restart_timer_on_retrigger');
    }
    if(d.kind==='zones'){
      item.entity_id=this._val('entity_id');item.active_states=split(this._val('active_states'));item.normal_states=split(this._val('normal_states'));
      item.initial_policy=this._val('initial_policy');item.fault_policy=this._val('fault_policy');
      const area=this._doc.areas.find(x=>x.key===this._area);
      item.mode_policies=area.modes.filter(m=>this._checked(`zone-mode-${m.key}`)).map(m=>({
        mode:m.key,entry_delay_seconds:this._number(`zone-delay-${m.key}`),follow_entry_delay:this._checked(`zone-follow-${m.key}`),
      }));
      if(!item.mode_policies.length)throw new Error('Mindestens ein Modus muss aktiviert sein.');
    }
    if(d.kind==='actions'){
      item.domain=this._val('domain');item.service=this._val('service');item.entity_ids=split(this._val('entity_ids'));
      try{item.data=JSON.parse(this._val('data'));}catch{throw new Error('Service-Daten müssen gültiges JSON sein.');}
      item.repeat_on_retrigger=this._checked('repeat_on_retrigger');item.timed=this._checked('timed');
    }
    if(d.kind==='roles')item.grants=this._readGrants();
    if(d.kind==='users'){
      item.enabled=this._checked('enabled');
      const prior=item.ha_user_ids||[];
      const chosen=this._val('ha_account');
      // Preserve legacy multi-account links when merely editing unrelated fields.
      item.ha_user_ids=this._val('identity_source')==='ha' && chosen
        ?(prior.length>1 && prior.includes(chosen)?prior:[chosen]):[];
      if(this._val('identity_source')==='ha'&&!item.ha_user_ids.length)throw Error('Bitte ein Home-Assistant-Konto auswählen.');
      if(item.ha_user_ids.some(id=>!this._haUsers.some(u=>u.id===id&&u.active)))throw Error('Mindestens ein Home-Assistant-Konto ist nicht aktiv oder nicht verfügbar.');
      item.roles=(this._doc.roles||[]).filter(r=>this._checked(`role:${r.key}`)).map(r=>r.key);
      item.grants=this._readGrants();item.time_windows=this._readWindows();
    }
    if(d.kind==='bindings'){
      item.input_type=this._val('input_type')||'homematic';item.user=this._val('user');item.scope='mode';item.area='mode';item.operation=item.input_type==='switch'?'arm':this._val('operation');item.mode=this._val('mode')||null;
      if(item.operation==='arm'&&!item.mode)throw Error('Bitte einen Zielmodus auswählen.');
      for(const key of ['event_type','device_id','address','event_action'])item[key]=this._val(key);
      item.channel=this._number('channel');item.interface_id=this._val('interface_id')||null;
      item.entity_id=this._val('control-entity');item.trigger_states=this._val('control-match')==='change'?['*']:split(this._val('trigger_states'));
      if(item.input_type==='switch')item.trigger_states=['on','off'];
      item.event_data=item.input_type==='event'?JSON.parse(this._val('event_data')||'{}'):{};
    }
    return item;
  }

  _dialogError(error) {
    const target=this.shadowRoot.getElementById('form-error');
    const message=this._explain(error.message||error);
    if(target){target.hidden=false;target.textContent=message;}
    else {this._error=message;this._render();}
  }
  _candidateForDialog() {
    const d=this._dialog,item=this._readDialog(),candidate=clone(this._doc);
    let list;
    if(d.kind==='actions'&&candidate.schema===2){const targets=this._doc.areas.filter(a=>this._checked('action-area-'+a.key)).map(a=>a.key);if(!targets.length)throw Error('Mindestens einen Bereich für diese Aktion wählen.');for(const area of candidate.areas){area.actions=(area.actions||[]).filter(a=>a.key!==item.key);if(targets.includes(area.key))area.actions.push(clone(item));}return candidate;}
    if(d.kind==='membership')candidate.sensors[d.index]=item;
    else {
      if(d.kind==='actions'){
        const area=candidate.areas.find(a=>a.key===this._area);
        if(!area)throw Error('Bereich nicht mehr vorhanden.');
        list=area.actions ||= [];
      }else list=candidate[d.kind==='zones'?'sensors':d.kind] ||= [];
      if(list.some((x,i)=>x.key===item.key&&i!==d.index))throw Error('Diese Kennung wird bereits verwendet.');
      if(d.index===null)list.push(item);else list[d.index]=item;
      if(d.kind==='areas')this._applyAreaMembers(candidate,item);
      if(d.kind==='modes')this._applyModeRules(candidate,item);
    }
    return candidate;
  }
  async _acceptDialog() {
    if(this._busy)return;
    try {await this._persistCandidate(this._dialog?.kind==='migration'?this._dialog.proposal:this._candidateForDialog(),'Gespeichert. Die Änderung ist aktiv.');}
    catch(error){this._dialogError(error);}
  }
  async _persistCandidate(candidate,notice='Gespeichert.',clearDialog=true) {
    if(!this._canAdmin||!this._safe)throw Error('Zum Speichern müssen alle Bereiche unscharf und quittiert sein.');
    if(this._busy)throw Error('Ein Speichervorgang läuft bereits.');
    this._stopLearning();this._busy=true;
    const controls=[...this.shadowRoot.querySelectorAll('.modal input,.modal select,.modal textarea,.modal button,#pin-policy input,#pin-policy button,#add-radio,[data-action="diag-apply"]')].map(el=>[el,el.disabled]);
    controls.forEach(([el])=>{el.disabled=true;});
    try {
      await this._hass.callWS({type:'ha_alarmzentrale/editor/save',revision:this._revision,configuration:candidate});
      this._doc=clone(candidate);this._original=clone(candidate);this._dirty=false;
      if(clearDialog)this._dialog=null;this._stopLearning();this._notice=notice;this._error='';
      this._busy=false;this._loadStarted=false;await this._load();
    } finally {
      this._busy=false;
      controls.forEach(([el,disabled])=>{el.disabled=disabled;});
    }
  }

  _collectInterim() {
    try{this._dialog.item=this._readDialog();return true;}
    catch(err){const t=this.shadowRoot.getElementById('form-error');if(t)t.textContent=String(err.message||err);return false;}
  }

  _dialogAction(button) {
    const op=button.dataset.dialogAction,d=this._dialog;
    if(this._busy)return;
    if(op==='notification-confirm'){void this._runtimeCommand({dataset:{runtimeOperation:d.operation,...(d.operation==='mute'?{runtimeArea:d.area}:{}),notificationToken:d.token}}).then(success=>{if(success){this._dialog=null;this._render();}else this._dialogError(this._error);});return;}
    if(op==='cancel'){this._stopLearning();this._dialog=null;this._render();return;}
    if(op==='confirm-delete'){
      if(this._busy)return;
      if(this._doc?.schema===2&&d.originalKind==='areas'&&d.item.key===SABOTAGE_AREA){this._dialogError('Der feste Sabotagebereich bleibt erhalten.');return;}
      const candidate=clone(this._doc);
      if(candidate.schema===2&&d.originalKind==='areas'){
        const key=d.item.key;
        candidate.areas=candidate.areas.filter(a=>a.key!==key);
        for(const mode of candidate.modes)mode.area_keys=mode.area_keys.filter(k=>k!==key);
        for(const sensor of candidate.sensors)sensor.memberships=(sensor.memberships||[]).filter(m=>m.area!==key);
        for(const principal of [...(candidate.users||[]),...(candidate.roles||[])])principal.grants=(principal.grants||[]).filter(g=>g.area!==key);
        candidate.bindings=(candidate.bindings||[]).filter(b=>b.scope==='mode'||['arm','disarm'].includes(b.operation)||b.area!==key).map(b=>['arm','disarm'].includes(b.operation)?{...b,scope:'mode',area:'mode'}:b);
        void this._persistCandidate(candidate,'Bereich und Verknüpfungen entfernt.').catch(error=>this._dialogError(error));return;
      }
      if(candidate.schema===2&&d.originalKind==='actions'){for(const area of candidate.areas)area.actions=(area.actions||[]).filter(a=>a.key!==d.item.key);void this._persistCandidate(candidate,'Aktion gelöscht.').catch(error=>this._dialogError(error));return;}
      const list=d.originalKind==='actions'?candidate.areas.find(a=>a.key===this._area)?.actions
        :candidate[d.originalKind==='zones'?'sensors':d.originalKind];
      if(!list)return;
      if(d.originalKind==='modes'&&candidate.schema===2){
        const key=d.item.key;
        for(const p of [...(candidate.users||[]),...(candidate.roles||[])])p.grants=(p.grants||[]).map(g=>{if(!(g.modes||[]).includes(key))return g;const modes=g.modes.filter(m=>m!==key);return {...g,modes,operations:modes.length?g.operations:g.operations.filter(op=>op!=='arm')};}).filter(g=>g.operations.length);
        candidate.bindings=(candidate.bindings||[]).filter(b=>b.mode!==key);
        for(const area of candidate.areas)for(const action of area.actions||[])if(action.mode_keys)action.mode_keys=action.mode_keys.filter(k=>k!==key);
        for(const sensor of candidate.sensors)for(const link of sensor.memberships||[]){if(link.mode_rules)delete link.mode_rules[key];if(link.diagnostic_reactions)delete link.diagnostic_reactions[key];for(const override of Object.values(link.entity_rules||{}))if(override.mode_rules)delete override.mode_rules[key];}
      }
      if(d.originalKind==='roles')for(const user of candidate.users||[])user.roles=(user.roles||[]).filter(k=>k!==d.item.key);
      if(d.originalKind==='users')candidate.bindings=(candidate.bindings||[]).filter(b=>b.user!==d.item.key);
      list.splice(d.index,1);
      void this._persistCandidate(candidate,'Eintrag gelöscht.').catch(error=>this._dialogError(error));return;
    }
    if(op==='remove-pin'){
      if(!button.dataset.confirm){button.dataset.confirm='1';button.textContent='PIN entfernen bestätigen';return;}
      void this._removePin();return;
    }
    if(op==='learn'){void this._learn();return;}
    if(this._doc.schema===2 && ['zones','membership','actions'].includes(d.kind)){
      if(this._dialogActionV2(button))return;
    }
    if(!this._collectInterim())return;
    if(op==='add-grant')d.item.grants.push({area:this._doc.areas[0]?.key||'',operations:[],modes:[]});
    if(op==='remove-grant')d.item.grants.splice(Number(button.dataset.index),1);
    if(op==='add-window')d.item.time_windows.push({weekdays:[0,1,2,3,4],start:'08:00',end:'18:00'});
    if(op==='remove-window')d.item.time_windows.splice(Number(button.dataset.index),1);
    this._render();
  }

  async _setPin(){
    const pin=this._val('pin'),key=this._dialog.item.key;
    if(!/^\d{4,128}$/.test(pin)){this.shadowRoot.getElementById('form-error').textContent='PIN muss 4 bis 128 Ziffern enthalten.';return;}
    // Do not retain PIN in the panel state or draft configuration.
    this._dialog=null;this._busy=true;this._render();
    try{await this._hass.callWS({type:'ha_alarmzentrale/editor/pin',operation:'set',user_key:key,pin,revision:this._revision});this._notice='PIN eingerichtet.';this._loadStarted=false;this._busy=false;await this._load();}
    catch(err){this._error=`PIN nicht gespeichert: ${String(err.message||err)}`;this._busy=false;this._render();}
  }
  async _removePin(){
    const key=this._dialog.item.key;this._dialog=null;this._busy=true;this._render();
    try{await this._hass.callWS({type:'ha_alarmzentrale/editor/pin',operation:'remove',user_key:key,revision:this._revision});this._notice='PIN entfernt.';this._loadStarted=false;this._busy=false;await this._load();}
    catch(err){this._error=`PIN konnte nicht entfernt werden: ${String(err.message||err)}`;this._busy=false;this._render();}
  }

  async _learn() {
    if(this._unsubscribe){this._stopLearning();const status=this.shadowRoot.getElementById('learn-status');if(status)status.textContent='Einlernen beendet.';return;}
    const expectedDevice=this._val('device_id');
    const target=this.shadowRoot.getElementById('learn-status');if(target)target.textContent='Warte auf den nächsten Homematic-Tastendruck …';
    try{
      const un = await this._hass.connection.subscribeMessage(event=>{
        if(this._dialog?.kind!=='bindings'||this._busy)return;
        if(expectedDevice&&event.device_id&&event.device_id!==expectedDevice){const status=this.shadowRoot.getElementById('learn-status');if(status)status.textContent='Signal eines anderen Geräts empfangen. Das gewünschte Gerät prüfen und erneut einlernen.';this._stopLearning();return;}
        const fields={event_type:'event_type',device_id:'device_id',address:'address',event_action:'type',channel:'subtype',interface_id:'interface_id'};
        for(const [targetKey,sourceKey] of Object.entries(fields)){
          const field=this._field(targetKey);if(field&&event[sourceKey]!==undefined)field.value=String(event[sourceKey]);
        }
        const picker=this.shadowRoot.querySelector('[data-native-device="device_id"] ha-selector');if(picker&&event.device_id)picker.value=String(event.device_id);
        const status=this.shadowRoot.getElementById('learn-status');if(status)status.textContent='Ereignis übernommen, bitte Gerät und Kanal physisch prüfen und anschließend speichern.';
        this._stopLearning();
      },{type:'ha_alarmzentrale/editor/learn'});
      if(this._dialog?.kind==='bindings')this._unsubscribe=un;else un();
    }catch(err){const t=this.shadowRoot.getElementById('learn-status');if(t)t.textContent=`Einlernen fehlgeschlagen: ${String(err.message||err)}`;}
  }
  _stopLearning(){if(this._unsubscribe){try{this._unsubscribe();}catch{}this._unsubscribe=null;}}
  _modesForArea(areaKey) {
    if (!areaKey) return [];
    if (this._doc?.schema===2) return (this._doc.modes||[]).filter(m=>m.area_keys?.includes(areaKey));
    return this._doc?.areas.find(a=>a.key===areaKey)?.modes||[];
  }

  async _loadCatalog() {
    if (!this._hass || this._catalogReady) return;
    try {
      const [devices,entities,services] = await Promise.all([
        this._hass.callWS({type:'config/device_registry/list'}),
        this._hass.callWS({type:'config/entity_registry/list'}),
        this._hass.callWS({type:'get_services'}),
      ]);
      if (!Array.isArray(devices) || !Array.isArray(entities) || !services || typeof services!=='object')
        throw Error('Home Assistant hat unerwartete Registerdaten geliefert.');
      this._catalog = {devices,entities,services};
      this._catalogReady=true;
      this._catalogError='';
    } catch (err) {
      this._catalogError=`Geräte-/Entitätsverzeichnis derzeit nicht verfügbar (${String(err.message||err)}). Manuelle Entitäts-ID bleibt möglich.`;
    }
    // Never rerender a partially edited dialog or destroy its keyboard focus.
    if(this._dialog?.kind==='zones')this._syncSensorSelector();
    if (!this._dialog && !this._dirty) this._render();
  }

  async _upgrade() {
    if (this._busy || this._dirty || !this._safe || this._doc?.schema===2) return;
    this._busy=true;this._render();
    try {
      const proposal=await this._hass.callWS({type:'ha_alarmzentrale/editor/upgrade_preview',revision:this._revision});
      if(proposal.revision!==this._revision || proposal.configuration?.schema!==2) throw Error('Keine gültige Migrationsvorschau');
      this._dialog={kind:'migration',proposal:proposal.configuration};
    } catch(err) {this._error=`Übernahme abgebrochen: ${String(err.message||err)}`;}
    finally {this._busy=false;this._render();}
  }

  _deviceLabel(device) {
    return device.name_by_user || device.name || device.model || device.id;
  }
  _entityLabel(entityId) {
    const r=this._catalog.entities.find(x=>x.entity_id===entityId);
    const st=this._hass?.states?.[entityId];
    return r?.name || r?.original_name || st?.attributes?.friendly_name || entityId;
  }
  _updateLiveValues() {
    // Refresh read-only current values from HA without rebuilding an editing form.
    this.shadowRoot.querySelectorAll('[data-sensor-status]').forEach(el=>{
      const sensor=this._doc?.sensors?.find(s=>s.key===el.dataset.sensorStatus);
      if(sensor)el.innerHTML=this._sensorStatusMarkup(sensor);
    });
    this.shadowRoot.querySelectorAll('[data-live-entity]').forEach(span=>{
      const id=span.dataset.liveEntity;
      span.textContent=this._liveValue(id,span.dataset.liveAttribute);
    });
  }
  _entityCandidates() {
    const ids=new Set([
      ...this._catalog.entities.map(e=>e.entity_id),
      ...Object.keys(this._hass?.states||{}),
    ]);
    return [...ids].filter(Boolean).sort((a,b)=>a.localeCompare(b,'de'));
  }
  _entityDevice(entityId) {
    return this._catalog.entities.find(x=>x.entity_id===entityId)?.device_id||'';
  }
  _stateOptions(entityId, attribute='') {
    const st=this._hass?.states?.[entityId];
    const values=new Set();
    if(!attribute && (entityId.startsWith('binary_sensor.') || entityId.startsWith('switch.') || entityId.startsWith('input_boolean.') || ['on','off'].includes(st?.state))){values.add('on');values.add('off');}
    const raw=attribute?st?.attributes?.[attribute]:st?.state;
    if(raw!=null && !['unknown','unavailable'].includes(String(raw))) values.add(String(raw));
    if(!attribute && Array.isArray(st?.attributes?.options))st.attributes.options.forEach(v=>values.add(String(v)));
    if(typeof raw==='boolean'){values.add('true');values.add('false');}
    return [...values].filter(x=>x!=='unknown'&&x!=='unavailable');
  }
  _sectionAudit() {
    const sourceLabels={entity:'Entitätszustand',event:'Tasterereignis',ha_account:'Home Assistant',panel_pin:'Lokale PIN',homematic:'Homematic-Bediengerät',configuration:'Konfiguration',system:'System'};
    const select=(key,label,choices)=>`<label class="field">${E(label)}<select data-audit-filter="${key}"><option value="">Alle</option>${choices.map(([value,text])=>`<option value="${E(value)}" ${this._auditFilter[key]===value?'selected':''}>${E(text)}</option>`).join('')}</select></label>`;
    let events=[...this._audit];
    const f=this._auditFilter;
    if(!f.user_key&&!f.device&&(!f.source||f.source==='system')){
      for(const area of this._statuses)for(const event of area.events||[]){
        if(event.kind.startsWith('access_'))continue;
        const at=new Date(event.at*1000).toISOString();
        if((f.area&&f.area!==area.key)||(f.action&&f.action!==event.kind)||(f.since&&at<new Date(f.since).toISOString())||(f.until&&at>new Date(f.until).toISOString()))continue;
        events.push({at,action:event.kind,outcome:'success',source:'system',area:area.key,detail:area.zones?.[event.info]?.name||event.info,event_id:event.event_id});
      }
    }
    events.sort((a,b)=>b.at.localeCompare(a.at));
    const actions=[...new Set(['arm','disarm','mute','acknowledge','configuration_save','alarm_started','alarm_retriggered','entry_delay_started','diagnostic_battery','diagnostic_offline',...events.map(e=>e.action)])];
    const people=(this._doc.users||[]).map(u=>[u.key,u.name]);
    for(const user of this._haUsers)if(!(this._doc.users||[]).some(u=>u.ha_user_ids?.includes(user.id)))people.push([user.id,user.name]);
    const row=event=>{
        const actor=event.user_name||(event.source==='system'?'System':event.device?(this._doc.bindings||[]).find(b=>b.key===event.device)?.name||event.device:'Nicht eindeutig zugeordnet');
        const area=this._doc.areas.find(a=>a.key===event.area)?.name||event.area||'';
        return `<div class="list-item"><div><div class="item-title"><ha-icon class="${event.outcome==='denied'?'tone-error':event.outcome==='failed'?'tone-warning':'tone-'+this._eventTone(event.action)}" icon="${event.outcome==='success'?this._eventIcon(event.action):'mdi:alert-circle-outline'}"></ha-icon><span class="name">${E(this._eventLabel(event.action))}</span><span class="pill">${({success:'Erfolgreich',denied:'Abgewiesen',failed:'Fehlgeschlagen'})[event.outcome]||E(event.outcome)}</span></div><div class="sub">${E(actor)} · ${E(sourceLabels[event.source]||event.source)}${area?' · '+E(area):''}</div>${event.detail?`<div class="sub">${E(event.detail)}</div>`:''}${event.identity_confidence==='device_assignment_only'?'<div class="sub">Gerätezuordnung; die bedienende Person ist nicht bestätigt.</div>':''}</div><span class="sub">${E(this._localTime(event.at))}</span></div>`;

    };
    const groups=[];
    const byId=new Map();
    for(const event of events.slice(0,250)){
      // Old records have no correlation: keep them separate rather than guess by timestamp.
      let group=event.event_id?byId.get(event.event_id):null;
      if(!group){group=[];groups.push(group);if(event.event_id)byId.set(event.event_id,group);}
      group.push(event);
    }
    const history=groups.map(group=>{
      if(group.length===1)return row(group[0]);
      const event=group.find(e=>e.source!=='system')||group[0];
      return `<details class="history-group" data-event-group="${E(event.event_id)}"><summary><ha-icon class="tone-${this._eventTone(event.action)}" icon="${this._eventIcon(event.action)}"></ha-icon><strong>${E(this._eventLabel(event.action))}</strong><span class="pill">${group.length} Meldungen</span><span class="sub">${E(this._localTime(group[0].at))}</span></summary>${group.map(row).join('')}</details>`;
    }).join('')||'<div class="empty-state">Keine Ereignisse für diese Auswahl.</div>';
    return `<div class="card"><div class="page-heading"><div><h2>Bedienhistorie</h2><p class="sub">Bedienhandlungen, Alarme und Konfigurationsänderungen nachvollziehen.</p></div><button class="btn" data-action="audit-refresh">Aktualisieren</button></div>
      <div class="filter-row">${select('user_key','Person',people)}${select('area','Bereich',[...this._doc.areas].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA)).map(a=>[a.key,a.name]))}${select('action','Ereignis',actions.map(key=>[key,this._eventLabel(key)]))}</div>
      <details class="sensor-advanced"><summary>Weitere Filter</summary><div class="filter-row">${select('source','Bedienweg',Object.entries(sourceLabels))}${select('device','Bediengerät',(this._doc.bindings||[]).map(b=>[b.key,b.name||b.key]))}${['since','until'].map(key=>`<label class="field">${key==='since'?'Von':'Bis'}<input type="datetime-local" data-audit-filter="${key}" value="${E(f[key])}"></label>`).join('')}</div></details>
      <div id="history-event-list">${history}</div><p class="sub">Die Historie ist begrenzt und nicht manipulationssicher. PINs und vertrauliche Aktionsdaten werden nicht angezeigt.</p></div>`;
  }

  async _refreshAudit() {
    try {
      const f=Object.fromEntries(Object.entries(this._auditFilter).filter(([,v])=>v).map(([k,v])=>[k, k==='since'||k==='until'?new Date(v).toISOString():v]));
      const data=await this._hass.callWS({type:'ha_alarmzentrale/editor/audit',...f});
      this._audit=data.events||[];this._render();
    } catch(err) {this._error=String(err.message||err);this._render();}
  }

  _sensorType(sensor) {
    if(sensor.sensor_type && sensor.sensor_type!=='auto')return sensor.sensor_type;
    const id=sensor.entities?.[0]?.entity_id;
    const cls=this._hass?.states?.[id]?.attributes?.device_class;
    return ({door:'door',garage_door:'garage',opening:'door',gate:'gate',window:'window',motion:'motion',occupancy:'motion',smoke:'smoke',moisture:'water',gas:'gas',carbon_monoxide:'co',vibration:'vibration',tamper:'sabotage',heat:'heat',sound:'sound'})[cls]||'generic';
  }
  _sensorIcon(sensor,active=false) {
    const profile=SENSOR_PROFILES[this._sensorType(sensor)]||SENSOR_PROFILES.generic;
    return 'mdi:'+profile[active?'active':'normal'];
  }
  _refreshSensorSymbolPreview() {
    const target=this.shadowRoot.getElementById('sensor-symbol-preview');
    if(!target||this._dialog?.kind!=='zones')return;
    const sensor={...this._dialog.item,sensor_type:this._val('sensor_type')||'auto',icon:undefined};
    target.innerHTML=`<span class="entity-status good"><ha-icon icon="${E(this._sensorIcon(sensor))}" aria-hidden="true"></ha-icon>Normal</span><span class="entity-status active"><ha-icon icon="${E(this._sensorIcon(sensor,true))}" aria-hidden="true"></ha-icon>Ausgelöst</span>`;
  }
  _sensorStatusMarkup(sensor) {
    const sources=(sensor.entities||[]).map(ent=>({...ent,role:'signal'}));
    if(sensor.sabotage)sources.unshift({...sensor.sabotage,role:'sabotage',name:'Sabotage'});
    for(const [role,id] of Object.entries(sensor.diagnostic_entities||{}))if(sensor.connection?.method!=='disabled'||!['connectivity','rssi_device','rssi_peer'].includes(role))sources.push({entity_id:id,role});
    const names={battery:'Batterie',battery_level:'Batteriestand',battery_voltage:'Batteriespannung',connectivity:'Verbindung',rssi_device:'RSSI Gerät',rssi_peer:'RSSI Peer'};
    const rules=sensor.diagnostic_values||{};
    const connection=sensor.connection||{method:'states'};
    const rssiId=sensor.diagnostic_entities?.[connection.rssi_channel];
    if(connection.method==='rssi'){
      const index=sources.findIndex(ent=>ent.role==='connectivity');
      if(index>=0)sources.splice(index,1);
      sources.push({entity_id:rssiId,role:'connectivity'});
    }else if(connection.method==='disabled'){
      for(let i=sources.length-1;i>=0;i--)if(sources[i].role==='connectivity')sources.splice(i,1);
    }
    return sources.map(ent=>{
      const st=this._hass?.states?.[ent.entity_id];
      let raw=ent.attribute?st?.attributes?.[ent.attribute]:st?.state;
      if(typeof raw==='boolean')raw=String(raw);
      else if(typeof raw==='number')raw=String(raw);
      const valid=st&&!['unknown','unavailable'].includes(st.state)&&typeof raw==='string'&&!['unknown','unavailable'].includes(raw);
      const type=this._sensorType(sensor);
      const signalLabels=['door','window','garage','gate'].includes(type)?['Offen','Geschlossen']:type==='motion'?['Bewegung','Frei']:type==='sabotage'?['Sabotage','Normal']:['Ausgelöst','Normal'];
      let tone='unknown',text='Nicht verfügbar',icon='mdi:help-circle-outline';
      if(!valid&&ent.attribute&&st&&!['unknown','unavailable'].includes(st.state))text='Attribut fehlt oder ist ungültig';
      if(valid){
        if(ent.role==='sabotage'&&ent.enabled===false){text='Deaktiviert';icon='mdi:shield-off-outline';}
        else if(ent.role==='signal'||ent.role==='sabotage'){
          if((ent.active_states||[]).includes(raw)){tone='active';text=ent.role==='sabotage'?'Sabotage':signalLabels[0];icon=ent.role==='sabotage'?'mdi:shield-alert':this._sensorIcon(sensor,true);}
          else if((ent.normal_states||[]).includes(raw)){tone='good';text=ent.role==='sabotage'?'Normal':signalLabels[1];icon=ent.role==='sabotage'?'mdi:shield-check':this._sensorIcon(sensor);}
          else {tone='warning';text='Nicht zugeordnet';icon='mdi:help-circle-outline';}
        }else if(ent.role==='battery'||ent.role==='connectivity'){
          const value=raw.toLowerCase();
          const matches=key=>(rules[key]||[]).some(v=>v.toLowerCase()===value);
          if(ent.role==='battery'){
            if(matches('battery_empty_states')){tone='active';text='Leer';icon='mdi:battery-alert';}
            else if(matches('battery_low_states')){tone='warning';text='Schwach';icon='mdi:battery-alert';}
            else if(matches('battery_good_states')){tone='good';text='Normal';icon='mdi:battery-check';}
            else {tone='warning';text='Nicht zugeordnet';}
          }else{
            if(matches('connectivity_online_states')){tone='good';text='Erreichbar';icon='mdi:lan-connect';}
            else if(matches('connectivity_offline_states')){tone='active';text='Nicht erreichbar';icon='mdi:lan-disconnect';}
            else {tone='warning';text='Nicht zugeordnet';}
          }
        }else if(raw.trim()!==''&&Number.isFinite(Number(raw))){tone='info';text='Messwert';icon=['battery_level','battery_voltage'].includes(ent.role)?'mdi:battery':'mdi:wifi';}
        else {tone='warning';text='Ungültiger Messwert';}
      }
      if(ent.role==='connectivity'&&['availability','rssi'].includes(connection.method)){
        const online=connection.method==='availability'?Boolean(st&&!['unknown','unavailable',''].includes(st.state)):valid&&this._signalQuality(raw,connection)!=='active'&&this._signalQuality(raw,connection)!=='unknown';
        tone=online?'good':'active';text=online?'Erreichbar':'Nicht erreichbar';icon=online?'mdi:lan-connect':'mdi:lan-disconnect';
      }
      const name=ent.name||names[ent.role]||this._entityLabel(ent.entity_id);
      const unit=!ent.attribute?st?.attributes?.unit_of_measurement||'':'';
      const display=tone==='info'?raw+(unit?' '+unit:''):text;
      if(ent.role==='sabotage'&&ent.enabled===false){tone='unknown';text='Deaktiviert';icon='mdi:shield-off-outline';}
      return `<div class="entity-status ${tone}" title="${E(ent.entity_id)}" aria-label="${E(name+': '+display)}"><ha-icon icon="${E(icon)}" aria-hidden="true"></ha-icon><div class="status-inline"><span>${E(name)}</span><span class="sub">${E(display)}</span></div></div>`;
    }).join('')+(connection.method!=='disabled'&&rssiId&&connection.direction?(()=>{
      const st=this._hass?.states?.[rssiId];
      const quality=this._signalQuality(st?.state,connection);
      const label={good:'Signal gut',warning:'Signal schwach',active:'Signal kritisch',unknown:'Signal nicht verfügbar'}[quality];
      return `<span class="entity-status ${quality==='unknown'?'active':quality}" title="${E(label)}" aria-label="${E(label)}"><ha-icon icon="${quality==='unknown'?'mdi:wifi-off':'mdi:wifi'}" aria-hidden="true"></ha-icon><span class="sub">${E(label)}</span></span>`;
    })():'');
  }

  _sensorModeSummary(sensor) {
    return (sensor.memberships||[]).map(link=>{
      const area=this._doc.areas.find(a=>a.key===link.area);
      const modes=this._modesForArea(link.area).map(mode=>{
        const labels=(sensor.entities||[]).filter(ent=>(link.entity_keys||sensor.entities.map(e=>e.key)).includes(ent.key)).map(ent=>{
          const rules=link.entity_rules?.[ent.key]?.mode_rules||link.mode_rules,rule=rules?.[mode.key];
          if(rules&&(!rule||rule.enabled===false))return 'Ausgenommen';
          return ({instant:'Sofort',standby:'Standby-Zeit',legacy_delay:'Eigene Zeit'})[rule?.behavior||'instant'];
        });
        const label=[...new Set(labels)].join(' / ')||'Keine Entität';
        return `<button class="btn small" type="button" data-action="edit-mode" data-mode="${E(mode.key)}" ${!this._safe?'disabled':''}>${E(mode.name)}: ${E(label)}</button>`;
      }).join('');
      return `<div class="member-summary"><span class="sub">${E(area?.name||link.area)}</span><div class="actions">${modes||'<span class="sub">Noch keinem Modus zugeordnet</span>'}</div></div>`;
    }).join('');
  }

  _areaSensorRow(sensor, areaKey) {
    const index=this._doc.sensors.indexOf(sensor),multiple=(sensor.memberships||[]).length>1;
    return `<div class="list-item sensor-card" data-sensor-key="${E(sensor.key)}"><div class="sensor-main-row"><div class="sensor-title"><ha-icon icon="${E(this._sensorIcon(sensor))}"></ha-icon><strong>${E(sensor.name)}</strong></div><div class="entity-statuses" data-sensor-status="${E(sensor.key)}">${this._sensorStatusMarkup(sensor)}</div><div class="actions"><button class="btn small" data-action="sensor-edit" data-index="${index}" ${!this._safe?'disabled':''}>Bearbeiten</button><button class="btn small danger" data-action="sensor-delete" data-index="${index}" ${!this._safe?'disabled':''}>Löschen</button></div></div>${multiple?'<p class="tone-warning">Bisher mehreren Bereichen zugeordnet. Beim Bearbeiten genau einen Bereich auswählen.</p>':''}${areaKey&&areaKey!==SABOTAGE_AREA?`<details class="sensor-links" data-detail-key="sensor-${E(areaKey)}-${E(sensor.key)}"><summary>Modusregeln</summary><button class="btn small" data-action="membership" data-area="${E(areaKey)}" data-index="${index}" ${!this._safe?'disabled':''}>Entitäten und Regeln bearbeiten</button>${this._sensorModeSummary(sensor)}</details>`:''}</div>`;
  }
  _sectionAreaSensors() {
    const areas=[...this._doc.areas].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA));
    const unassigned=this._doc.sensors.filter(sensor=>!(sensor.memberships||[]).length);
    return `<div class="card"><div class="row"><h2>Bereiche und Sensoren</h2><button class="btn primary" data-action="add" ${!this._safe?'disabled':''}>+ Bereich hinzufügen</button></div>${this._sectionSearch()}
      ${areas.map(area=>{const fixed=area.key===SABOTAGE_AREA,index=this._doc.areas.indexOf(area),sensors=this._doc.sensors.filter(sensor=>fixed?Boolean(sensor.sabotage):(sensor.memberships||[]).some(link=>link.area===area.key));return `<details class="sensor-advanced area-sensor-group" data-area-key="${E(area.key)}" data-detail-key="area-sensors-${E(area.key)}" data-search="${E(area.name+' '+sensors.map(s=>s.name+' '+s.entities?.map(e=>e.entity_id).join(' ')).join(' '))}"><summary><ha-icon icon="${E(this._areaIcon(area))}"></ha-icon><strong>${E(area.name)}</strong><span class="sub">${sensors.length} Sensoren · ${this._modesForArea(area.key).length} Modi</span></summary><div class="area-sensor-toolbar"><button class="btn small" data-action="edit" data-index="${index}" ${!this._safe?'disabled':''}>${fixed?'Überwachung einstellen':'Bereich bearbeiten'}</button>${fixed?'':`<button class="btn small danger" data-action="delete" data-index="${index}" ${!this._safe?'disabled':''}>Bereich löschen</button><button class="btn primary small" data-action="sensor-add" data-area="${E(area.key)}" ${!this._safe?'disabled':''}>+ Sensor hinzufügen</button>`}</div>${sensors.map(sensor=>this._areaSensorRow(sensor,area.key)).join('')||'<p class="sub">Noch keine Sensoren vorhanden.</p>'}</details>`;}).join('')}
      ${unassigned.length?`<details class="sensor-advanced area-sensor-group" data-detail-key="unassigned-sensors" data-search="${E(unassigned.map(s=>s.name).join(' '))}"><summary><ha-icon icon="mdi:folder-alert-outline"></ha-icon><strong>Ohne Bereich</strong><span class="sub">${unassigned.length} Sensoren</span></summary><p class="sub">Diese vorhandenen Sensoren bleiben erhalten. Über Bearbeiten einem Bereich zuordnen.</p>${unassigned.map(sensor=>this._areaSensorRow(sensor,null)).join('')}</details>`:''}</div>`;
  }
  _sectionV2() {
    const key=this._active, area=this._doc.areas.find(a=>a.key===this._area);
    if(key==='areas'||key==='zones')return this._sectionAreaSensors();
    if(key==='diagnostics')return this._sectionDiagnostics();
    if(key==='overview')return `<div class="card" id="runtime-overview">${this._overviewContent()}</div>`;
    if(!['areas','modes','zones','actions'].includes(key))return this._sectionSharedModern();
    const items=this._listFor(key)||[], title=SECTIONS.find(x=>x[0]===key)?.[1]||key;
    const areaChoice=false?`<select class="area-select" id="area-choice" aria-label="Alarmbereich">${[...this._doc.areas].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA)).map(a=>`<option value="${E(a.key)}" ${a.key===this._area?'selected':''}>${E(a.name)}</option>`).join('')}</select>`:'';
    const catalogWarning=this._catalogError&&['zones','actions'].includes(key)?`<p class="sub" role="status">${E(this._catalogError)}</p>`:'';
    return `<div class="card"><div class="row"><div><h2>${E(title)}</h2>${areaChoice}</div>
       <button class="btn primary" data-action="add" ${!this._safe?'disabled':''}>+ Hinzufügen</button></div>
       ${catalogWarning}${this._sectionSearch()}
       ${items.length?(key==='areas'?[...items].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA)):items).map(item=>{const index=items.indexOf(item);
         const desc=key==='areas'?`${this._modesForArea(item.key).length} Modi · ${this._doc.sensors.filter(s=>item.key===SABOTAGE_AREA?Boolean(s.sabotage):(s.memberships||[]).some(m=>m.area===item.key)).length} Sensoren · ${(item.actions||[]).length} Aktionen`
           :key==='modes'?`Bereiche: ${[...(item.area_keys||[])].sort((a,b)=>Number(b===SABOTAGE_AREA)-Number(a===SABOTAGE_AREA)).map(k=>this._doc.areas.find(a=>a.key===k)?.name||k).join(', ')} · Standby ${item.standby_seconds||0}s`
           :key==='zones'?`${item.entities?.length||0} Entitäten · ${item.device_id?this._deviceLabel(this._catalog.devices.find(d=>d.id===item.device_id)||{id:item.device_id}):'ohne festes Gerät'}`
           :`${item.domain}.${item.service} · ${(item.entity_ids||[]).map(x=>this._entityLabel(x)).join(', ')}`;
         const chips=key==='zones'?`<div class="checkbox-wrap">${(item.memberships||[]).map(m=>`<button type="button" class="btn small" data-action="membership" data-area="${E(m.area)}" data-index="${index}" ${!this._safe?'disabled':''}>${E(this._doc.areas.find(a=>a.key===m.area)?.name||m.area)} · Zuordnung</button>`).join('')||(item.sabotage?'':'<span class="sub">Noch keinem Bereich zugeordnet</span>')}</div>`:'';
         if(key==='zones')return `<div class="list-item sensor-card" data-search="${E((item.name||item.key)+' '+desc)}"><div class="sensor-main-row"><div class="sensor-title"><ha-icon icon="${E(this._sensorIcon(item))}"></ha-icon><strong>${E(item.name)}</strong></div><div class="entity-statuses" data-sensor-status="${E(item.key)}">${this._sensorStatusMarkup(item)}</div><div class="actions"><button class="btn small" data-action="edit" data-index="${index}" ${!this._safe?'disabled':''}>Bearbeiten</button><button class="btn small danger" data-action="delete" data-index="${index}" ${!this._safe?'disabled':''}>Löschen</button></div></div><details class="sensor-links" data-detail-key="sensor-${E(item.key)}"><summary>Bereiche und Modi</summary>${chips}${this._sensorModeSummary(item)}</details></div>`;
         return `<div class="list-item ${key==='zones'?'sensor-card':key==='actions'?'action-card':''}" data-search="${E((item.name||item.key)+' '+desc)}"><div><div class="${key==='zones'?'sensor-title':'list-heading'}">${['zones','modes','areas'].includes(key)?`<ha-icon icon="${E(key==='zones'?this._sensorIcon(item):key==='modes'?this._modeIcon(item):this._areaIcon(item))}" aria-hidden="true"></ha-icon>`:''}<span class="name">${E(item.name||item.key)}</span></div><div class="sub list-description">${E(desc)}</div>${key==='zones'?`<div class="entity-statuses" data-sensor-status="${E(item.key)}">${this._sensorStatusMarkup(item)}</div>`:''}${key==='zones'?'':chips}${key==='zones'?`<details class="sensor-links"><summary>Bereiche und Modi</summary>${chips}${this._sensorModeSummary(item)}</details>`:''}</div><div class="actions"><button class="btn small" data-action="edit" data-index="${index}" ${!this._safe?'disabled':''}>Bearbeiten</button><button class="btn small danger" data-action="delete" data-index="${index}" ${!this._safe||(key==='areas'&&item.key===SABOTAGE_AREA)?'disabled':''}>Löschen</button></div></div>`;
       }).join(''):'<p class="muted">Noch keine Einträge vorhanden.</p>'}</div>`;
  }

  _diagnosticTrend(history,key){
    const label=({duty_cycle:'Duty Cycle',carrier_sense:'Carrier Sense',rssi_device:'RSSI Gerät',rssi_peer:'RSSI Peer'})[key]||key,unit=['duty_cycle','carrier_sense'].includes(key)?'%':'dBm';
    const points=(history||[]).filter(x=>typeof x[key]==='number'&&Number.isFinite(x[key])).slice(-80).map((p,i)=>({value:p[key],at:typeof p.at==='number'?p.at*1000:Date.parse(p.at)||i}));
    if(!points.length)return `<div class="trend-box"><span>${E(label)}</span><span class="sub">Noch kein Verlauf</span></div>`;
    const lo=unit==='%'?0:Math.floor(Math.min(...points.map(p=>p.value))-1),hi=unit==='%'?100:Math.ceil(Math.max(...points.map(p=>p.value))+1),range=Math.max(1,hi-lo),first=points[0].at,last=points.at(-1).at,span=Math.max(1,last-first),x=p=>38+(p.at-first)/span*244,y=p=>62-(p.value-lo)/range*54;
    const path=points.map((p,i)=>`${i?'L':'M'}${x(p).toFixed(1)} ${y(p).toFixed(1)}`).join(' '),clock=t=>new Date(t).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
    return `<div class="trend-box graph-inspect" tabindex="0" aria-label="${E(label)}: Messwerte mit Maus, Berührung oder Pfeiltasten anzeigen" data-graph="${E(JSON.stringify({points,unit}))}"><span>${E(label)} · ${points.at(-1).value} ${unit}</span><svg viewBox="0 0 300 88" role="img" aria-label="${E(label+' Verlauf in '+unit)}" style="width:100%;height:110px;max-width:100%">${[lo,(lo+hi)/2,hi].map(v=>`<line x1="38" x2="282" y1="${62-(v-lo)/range*54}" y2="${62-(v-lo)/range*54}" stroke="var(--divider-color,#777)"/><text x="1" y="${65-(v-lo)/range*54}" fill="var(--secondary-text-color,#888)" font-size="10">${v} ${unit}</text>`).join('')}<path d="${path}" fill="none" stroke="var(--primary-color,#03a9f4)" stroke-width="2"/>${points.length===1?`<circle cx="${x(points[0])}" cy="${y(points[0])}" r="3" fill="var(--primary-color,#03a9f4)"/>`:''}<text x="38" y="82" fill="var(--secondary-text-color,#888)" font-size="10">${clock(first)}</text><text x="282" text-anchor="end" y="82" fill="var(--secondary-text-color,#888)" font-size="10">${clock(last)}</text></svg><span class="sub graph-value" role="tooltip" aria-live="polite" hidden></span></div>`;
  }
  _wireGraphs(){if(!this._graphOutsideHandler){this._graphOutsideHandler=e=>this.shadowRoot.querySelectorAll('[data-graph]').forEach(host=>{if(!host.contains(e.target))host.querySelector('.graph-value').hidden=true;});this.shadowRoot.addEventListener('pointerdown',this._graphOutsideHandler);}this.shadowRoot.querySelectorAll('[data-graph]').forEach(host=>{
    const data=JSON.parse(host.dataset.graph),value=host.querySelector('.graph-value'),svg=host.querySelector('svg');let selected=data.points.length-1;
    const show=i=>{selected=Math.max(0,Math.min(data.points.length-1,i));const p=data.points[selected];value.textContent=new Date(p.at).toLocaleString('de-DE')+' · '+p.value+' '+data.unit;value.hidden=false;};
    host.onpointermove=e=>{const rect=svg.getBoundingClientRect(),scale=Math.min(rect.width/300,rect.height/88),offset=(rect.width-300*scale)/2,fraction=Math.max(0,Math.min(1,((e.clientX-rect.left-offset)/scale-38)/244)),target=data.points[0].at+fraction*(data.points.at(-1).at-data.points[0].at);let closest=0;data.points.forEach((p,i)=>{if(Math.abs(p.at-target)<Math.abs(data.points[closest].at-target))closest=i;});show(closest);};
    host.onpointerdown=host.onpointermove;host.onpointerleave=e=>{if(e.pointerType!=='touch')value.hidden=true;};host.onfocus=()=>show(selected);host.onblur=()=>{value.hidden=true;};host.onkeydown=e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();show(selected+(e.key==='ArrowLeft'?-1:1));}else if(e.key==='Escape')value.hidden=true;};
  });}

  _radioRow(key,index,channels={},name='') {
    const source=(field,label,value)=>`<div data-native-source="${field}" data-source-label="${E(label)}">${this._input(field,label,value)}</div>`;
    return `<fieldset data-radio-index="${index}" data-radio-key="${E(key)}"><div class="row">${this._input('radio-name-'+index,'Funkmodul',name||key)}<button type="button" class="btn small danger" data-remove-radio="${index}">Entfernen</button></div><div class="form-grid">${source('radio-duty-'+index,'Duty Cycle',channels.duty_cycle||'')}${source('radio-carrier-'+index,'Carrier Sense',channels.carrier_sense||'')}</div></fieldset>`;
  }
  _sectionDiagnostics() {
    const current=this._doc.diagnostics||{},report=this._diagnostics||{};
    const specs=[['offline_grace_seconds','Verbindungsverlust bewerten nach (s)'],['flap_window_seconds','Zeitfenster für wiederholte Ausfälle (s)'],['flap_count','Ausfälle bis Verbindung als instabil gilt'],['recovery_seconds','Stabile Verbindung bis Entwarnung (s)'],['common_outage_window_seconds','Zeitfenster gemeinsamer Ausfall (s)'],['common_outage_count','Geräte bis gemeinsame Störung vermutet wird'],['history_limit','Verlaufseinträge je Quelle'],['battery_freshness_seconds','Gültigkeit der Batteriemeldung (s)']];
    const proposed={offline_grace_seconds:30,flap_window_seconds:600,flap_count:5,recovery_seconds:300,common_outage_window_seconds:15,common_outage_count:3,history_limit:250,battery_freshness_seconds:86400};
    const sourceName=id=>this._doc.sensors.filter(s=>(s.device_id||s.key)===id).map(s=>s.name).join(', ')||id;
    const devices=(report.devices||[]).map(row=>`<section class="status-panel"><h3><ha-icon icon="mdi:access-point"></ha-icon>${E(sourceName(row.id))}</h3><span class="pill">${E(({online:'Erreichbar',offline:'Nicht erreichbar',unknown:'Unbekannt'})[row.connectivity]||row.connectivity)}</span><span class="pill">Batterie: ${E(({good:'Normal',low:'Schwach',empty:'Leer',unknown:'Unbekannt'})[row.battery]||row.battery)}</span>${row.unstable?'<p class="tone-warning">Verbindung instabil – weitere Offline-Alarme unterdrückt.</p>':''}<div class="device-facts">${row.battery_level==null?'':`<span>${E(row.battery_level)} %</span>`}${row.battery_voltage==null?'':`<span>${E(row.battery_voltage)} V</span>`}<span>RSSI Gerät: ${E(row.rssi?.rssi_device??'—')}</span><span>RSSI Peer: ${E(row.rssi?.rssi_peer??'—')}</span></div><div class="row flex-start">${this._diagnosticTrend(row.history,'rssi_device')}${this._diagnosticTrend(row.history,'rssi_peer')}</div><div class="sub">Letzte Batterieinformation: ${row.battery_at?E(this._localTime(row.battery_at)):'Nicht verfügbar'}</div></section>`).join('');
    return `<div class="card"><div class="page-heading"><div><h2>Diagnose</h2><p class="sub">Gerätegesundheit, Verbindungsverlauf und Homematic-Funkmodule.</p></div><span class="pill">${report.enabled?'Überwachung aktiv':'Überwachung deaktiviert'}</span></div>
      <div id="diagnostic-device-readout" class="diagnostic-grid">${devices||'<p class="sub">Noch keine Diagnosewerte. Quellen werden beim Sensor zugeordnet.</p>'}</div>
      ${this._testControls()}<h3 style="margin-top:24px">Zentralen und Funkmodule</h3><div id="diagnostic-radio-readout" class="diagnostic-grid">${(report.radios||[]).map(row=>`<section class="status-panel"><h3>${E(current.radio_names?.[row.id]||row.id)}</h3><div class="row flex-start">${this._diagnosticTrend(row.history,'duty_cycle')}${this._diagnosticTrend(row.history,'carrier_sense')}</div></section>`).join('')||'<p class="sub">Funkmodule lassen sich unten über ihre vorhandenen HA-Entitäten zuordnen.</p>'}</div>
      <details id="diagnostic-settings" class="sensor-advanced"><summary>Überwachung und Funkquellen einstellen</summary>${this._checkbox('diag-enabled','Verbindungs- und Batteriediagnose aktivieren',current.enabled)}<div class="form-grid">${specs.map(([key,label])=>this._input('diag-'+key,label,current[key]??proposed[key],'number')).join('')}</div>
      <p class="sub">Die Zeiten sind Vorschläge. Sie werden erst mit Speichern übernommen.</p><h3>Homematic-Funkquellen</h3><div id="radio-editors">${Object.entries(current.radio_interfaces||{}).map(([key,channels],i)=>this._radioRow(key,i,channels,current.radio_names?.[key])).join('')}</div><button type="button" class="btn small" id="add-radio" ${!this._safe?'disabled':''}>+ Funkmodul</button><div class="detail-actions"><button class="btn primary" data-action="diag-apply" ${!this._safe?'disabled':''}>Speichern</button></div></details>
      <details class="sensor-advanced"><summary>Diagnoseereignisse</summary>${[...(report.events||[])].reverse().slice(0,50).map(row=>`<div class="list-item"><div><span class="name">${E(({offline:'Verbindung ausgefallen',unstable:'Verbindung instabil',battery:'Batteriemeldung',online:'Verbindung wiederhergestellt',common_outage_suspected:'Gemeinsame Störung vermutet'})[row.kind]||row.kind)}</span><div class="sub">${E(sourceName(row.source))} · ${E(row.detail)}</div></div><span class="sub">${E(this._localTime(row.at))}</span></div>`).join('')||'<p class="sub">Noch keine Diagnoseereignisse.</p>'}</details><section class="diagnostic-alarm-launch"><h3>Alarmansicht testen</h3><a class="btn small" href="/ha-alarmzentrale?alarm=1">Vollbild-Alarmansicht</a></section></div>`;
  }
  _refreshDiagnosticReadouts() {
    const current=this._doc.diagnostics||{},report=this._diagnostics||{};
    const name=id=>this._doc.sensors.filter(s=>(s.device_id||s.key)===id).map(s=>s.name).join(', ')||id;
    const target=this.shadowRoot.getElementById('diagnostic-device-readout');
    if(target)target.innerHTML=(report.devices||[]).map(row=>`<section class="status-panel"><h3><ha-icon icon="mdi:access-point"></ha-icon>${E(name(row.id))}</h3><span class="pill">${E(({online:'Erreichbar',offline:'Nicht erreichbar',unknown:'Unbekannt'})[row.connectivity]||row.connectivity)}</span><span class="pill">Batterie: ${E(({good:'Normal',low:'Schwach',empty:'Leer',unknown:'Unbekannt'})[row.battery]||row.battery)}</span>${row.unstable?'<p class="tone-warning">Verbindung instabil</p>':''}<div class="device-facts">${row.battery_level==null?'':`<span>${E(row.battery_level)} %</span>`}${row.battery_voltage==null?'':`<span>${E(row.battery_voltage)} V</span>`}<span>RSSI Gerät: ${E(row.rssi?.rssi_device??'—')}</span><span>RSSI Peer: ${E(row.rssi?.rssi_peer??'—')}</span></div><div class="row flex-start">${this._diagnosticTrend(row.history,'rssi_device')}${this._diagnosticTrend(row.history,'rssi_peer')}</div><div class="sub">Letzte Batterieinformation: ${row.battery_at?E(this._localTime(row.battery_at)):'Nicht verfügbar'}</div></section>`).join('')||'<p class="sub">Noch keine Diagnosewerte. Quellen beim Sensor zuordnen.</p>';
    const radio=this.shadowRoot.getElementById('diagnostic-radio-readout');
    if(radio)radio.innerHTML=(report.radios||[]).map(row=>`<section class="status-panel"><h3>${E(current.radio_names?.[row.id]||row.id)}</h3><div class="row flex-start">${this._diagnosticTrend(row.history,'duty_cycle')}${this._diagnosticTrend(row.history,'carrier_sense')}</div></section>`).join('')||'<p class="sub">Funkmodule unten über ihre HA-Entitäten zuordnen.</p>';
    this._wireGraphs();
  }

  async _mountSimpleSources() {
    try {
      if(!customElements.get('ha-selector')){
        if(!window.loadCardHelpers)return;
        const helpers=await window.loadCardHelpers(),card=helpers.createCardElement({type:'tile',entity:'sun.sun'});await card.constructor.getConfigElement?.();
      }
      if(!customElements.get('ha-selector'))return;
      this.shadowRoot.querySelectorAll('[data-native-source],[data-native-device],[data-native-icon]').forEach(slot=>{
        if(slot.querySelector('ha-selector'))return;
        const key=slot.dataset.nativeSource||slot.dataset.nativeDevice||slot.dataset.nativeIcon,input=this._field(key);if(!input)return;
        const picker=document.createElement('ha-selector');picker.hass=this._hass;picker.selector=slot.dataset.nativeIcon?{icon:{}}:slot.dataset.nativeDevice?{device:{}}:{entity:{}};picker.label=slot.dataset.sourceLabel||'Entität';picker.required=false;picker.value=input.value||undefined;
        picker.addEventListener('value-changed',event=>{event.stopPropagation();const value=event.detail?.value;if(value!=null&&typeof value!=='string')return;input.value=value||'';input.dispatchEvent(new Event('change'));});
        input.closest('label').hidden=true;slot.append(picker);
      });
    }catch{/* Manual fields remain usable. */}
  }
  _wireRadioEditors() {
    this.shadowRoot.querySelectorAll('[data-remove-radio]').forEach(button=>{button.onclick=()=>button.closest('[data-radio-index]').remove();});
    void this._mountSimpleSources();
  }
  _applyDiagnostics() {
    if(!this._safe||this._busy)return;
    try {
      const current=clone(this._doc.diagnostics||{}),enabled=this._checked('diag-enabled');
      for(const key of ['offline_grace_seconds','flap_window_seconds','flap_count','recovery_seconds','common_outage_window_seconds','common_outage_count','history_limit','battery_freshness_seconds']){
        const value=this._val('diag-'+key);
        if(value&&!/^\d+$/.test(value))throw Error('Bitte für alle Diagnosezeiten und Zähler gültige ganze Zahlen eingeben.');
        if(enabled&&!value)throw Error('Bei aktiver Diagnose alle Zeiten und Zähler festlegen.');
        if(value)current[key]=Number(value);
      }
      current.enabled=enabled;current.radio_interfaces={};current.radio_names={};
      for(const row of this.shadowRoot.querySelectorAll('[data-radio-index]')){
        const i=row.dataset.radioIndex,key=row.dataset.radioKey;
        const duty=this._val('radio-duty-'+i),carrier=this._val('radio-carrier-'+i);
        if(!duty&&!carrier)continue;
        for(const id of [duty,carrier].filter(Boolean))if(!/^[a-z0-9_]+\.[a-z0-9_]+$/.test(id))throw Error('Bitte gültige Funkdiagnose-Entitäten auswählen.');
        current.radio_interfaces[key]={...(duty?{duty_cycle:duty}:{}),...(carrier?{carrier_sense:carrier}:{})};
        current.radio_names[key]=this._val('radio-name-'+i)||key;
      }
      const candidate=clone(this._doc);candidate.diagnostics=current;
      void this._persistCandidate(candidate,'Diagnoseeinstellungen gespeichert.').catch(error=>{this._error=String(error.message||error);this._render();});
    }catch(error){this._error=String(error.message||error);const target=this.shadowRoot.getElementById('diagnostic-error');if(target)target.textContent=this._error;else{const notice=document.createElement('div');notice.id='diagnostic-error';notice.className='notice error';notice.textContent=this._error;this.shadowRoot.getElementById('radio-editors')?.before(notice);}}
  }

  _sectionLegacyForShared() {
    // Reuse the existing user/role/binding UI, but with v2-compatible mode lists.
    const key=this._active, items=this._doc[key]||[];
    return `<div class="card"><div class="row"><h2>${E(SECTIONS.find(x=>x[0]===key)?.[1]||key)}</h2><button class="btn primary" data-action="add" ${!this._safe?'disabled':''}>+ Hinzufügen</button></div>
    ${items.map((item,index)=>`<div class="list-item"><div><div class="name">${E(item.name||item.key)}</div><div class="sub">${E(this._describe(key,item))}</div></div><div class="actions"><button class="btn small" data-action="edit" data-index="${index}" ${!this._safe?'disabled':''}>Bearbeiten</button><button class="btn small danger" data-action="delete" data-index="${index}" ${!this._safe?'disabled':''}>Löschen</button>${key==='users'?`<button class="btn small" data-action="pin" data-index="${index}" ${!this._safe||this._dirty?'disabled':''}>${this._pinUsers.includes(item.key)?'PIN ändern':'PIN setzen'}</button>`:''}</div></div>`).join('')||'<p class="muted">Noch keine Einträge vorhanden.</p>'}</div>`;
  }

  _defaultV2(kind) {
    const id=`neu_${Math.random().toString(36).slice(2,10)}`;
    if(kind==='areas')return {key:id,name:'Neuer Bereich',actions:[],ack_policy:'manual',recovery_fault_grace_seconds:30};
    if(kind==='modes')return {key:id,name:'Neuer Modus',area_keys:[SABOTAGE_AREA],standby_seconds:0,exit_delay_seconds:0,delay_on_mode_change:false,mute_after_seconds:1800,restart_timer_on_retrigger:true};
    if(kind==='zones')return {key:id,name:'Neuer Sensor',device_id:null,entities:[],memberships:[]};
    if(kind==='actions')return {key:id,name:'Neue Aktion',domain:'light',service:'turn_on',entity_ids:[],data:{},repeat_on_retrigger:false,timed:false};
  }
  _v2KeyField(dlg) {return `<input type="hidden" data-field="key" value="${E(dlg.item.key)}">`;}
  _v2Modal(title, fields, extra='') {
    return `<div class="modal-bg"><div class="modal" role="dialog" aria-modal="true" aria-label="${E(title)}">
      <div class="modal-head"><h2>${E(title)}</h2><div class="sub">Speichern übernimmt die geprüften Änderungen direkt.</div></div>
      <form id="editor-form"><div class="modal-content">${fields}${extra}<div id="form-error" class="notice error" role="alert" hidden></div></div>
      <footer class="modal-foot"><button class="btn" type="button" data-dialog-action="cancel">Abbrechen</button><button class="btn primary" type="submit">Speichern</button></footer></form></div></div>`;
  }

  _renderDialogV2() {
    const d=this._dialog, item=d.item,kind=d.kind;
    if(kind==='membership')return this._v2MembershipDialog();
    const nameInput=this._input('name','Anzeigename',item.name);
    if(kind==='areas')return this._v2AreaDialog();
    if(kind==='modes')return this._v2ModeDialog();
    if(kind==='zones')return this._v2SensorDialog();
    if(kind==='actions')return this._v2ActionDialog();
    return '';
  }

  _v2AreaDialog() {
    const d=this._dialog,item=d.item,fixed=item.key===SABOTAGE_AREA;
    const sensors=this._doc.sensors.filter(s=>fixed?Boolean(s.sabotage):(s.memberships||[]).some(m=>m.area===item.key));
    return this._v2Modal(fixed?'Sabotagebereich':'Alarmbereich bearbeiten',`
      ${this._v2KeyField(d)}${fixed?'<h3>Sabotage</h3><p class="sub">Sabotagekontakte werden automatisch aus den Sensoren übernommen.</p>':this._input('name','Bereichsname',item.name)}<div data-native-icon="icon" data-source-label="Bereichssymbol">${this._input('icon','Bereichssymbol',this._areaIcon(item))}</div>
      <h3>Zugeordnete Sensoren</h3>
      ${sensors.map(sensor=>{
        const index=this._doc.sensors.indexOf(sensor),link=sensor.memberships?.find(m=>m.area===item.key);
        if(fixed)return `<div class="list-item sabotage-sensor-row"><div class="sabotage-sensor-content"><div class="item-title"><ha-icon icon="mdi:shield-check"></ha-icon><strong>${E(sensor.name)}</strong></div><div class="entity-statuses" data-sensor-status="${E(sensor.key)}">${this._sensorStatusMarkup(sensor)}</div></div>${this._switch('area-sabotage-'+index,'Überwachen',sensor.sabotage.enabled!==false)}</div>`;
        return `<div class="list-item"><div class="item-title"><ha-icon icon="${E(this._sensorIcon(sensor))}"></ha-icon><strong>${E(sensor.name)}</strong></div><div class="entity-statuses" data-sensor-status="${E(sensor.key)}">${this._sensorStatusMarkup(sensor)}</div></div>`;
      }).join('')||'<div class="empty-state">Sensoren nach dem Speichern direkt im Bereich hinzufügen.</div>'}
      <details class="sensor-advanced"><summary>Weitere Einstellungen</summary><div class="form-grid area-advanced-settings">${this._select('ack_policy','Alarm quittieren',item.ack_policy||'manual',[['manual','Manuell'],['auto_clear','Nach Normalisierung automatisch']])}${this._input('recovery_fault_grace_seconds','Schutzzeit nach Neustart (s)',item.recovery_fault_grace_seconds??30,'number')}</div></details>
      <p class="sub">Neue Bereiche benötigen zusätzlich die passenden Benutzer- oder Rollenrechte.</p><p class="sub">Welche Bereiche aktiv sind und ob Kontakte sofort oder nach Standby auslösen, wird im jeweiligen Modus eingestellt.</p>`);
  }
  _applyAreaMembers(candidate,area) {
    if(area.key===SABOTAGE_AREA){candidate.sensors.forEach((sensor,index)=>{if(sensor.sabotage)sensor.sabotage.enabled=this._checked('area-sabotage-'+index);});return;}
    // Regular sensors are created or moved in their own area-scoped dialog.
  }
  _v2ModeDialog() {
    const d=this._dialog,item=d.item;
    return this._v2Modal('Modus bearbeiten',`${this._v2KeyField(d)}${this._input('name','Modusname',item.name)}<div data-native-icon="icon" data-source-label="Modussymbol">${this._input('icon','Modussymbol',this._modeIcon(item))}</div>
      <div class="form-grid mode-times">${this._input('exit_delay_seconds','Ausgangszeit (s)',item.exit_delay_seconds??0,'number')}${this._input('standby_seconds','Standby-Zeit (s)',item.standby_seconds??0,'number')}${this._duration('mute_after_seconds','Alarmaktionsdauer',item.mute_after_seconds,true)}</div>
      <details class="sensor-advanced"><summary>Weitere Zeitregeln</summary>${this._checkbox('delay_on_mode_change','Ausgangszeit auch bei Moduswechsel verwenden',item.delay_on_mode_change)}${this._checkbox('restart_timer_on_retrigger','Alarmaktionsdauer bei erneutem Auslösen neu starten',item.restart_timer_on_retrigger)}</details>
      <h3>Bereiche und Sensorverhalten</h3>
      ${[...this._doc.areas].sort((a,b)=>Number(b.key===SABOTAGE_AREA)-Number(a.key===SABOTAGE_AREA)).map(area=>{
        const fixed=area.key===SABOTAGE_AREA;
        const sensors=this._doc.sensors.filter(s=>fixed?s.sabotage:s.memberships?.some(m=>m.area===area.key));
        if(fixed)return this._checkbox('mode-area-'+area.key,'Sabotage überwachen',(item.area_keys||[]).includes(area.key));
        return `<details class="sensor-advanced"><summary>${E(area.name)}</summary><section class="rule-group">${this._checkbox('mode-area-'+area.key,'Bereich aktiv',(item.area_keys||[]).includes(area.key))}<div data-mode-area="${E(area.key)}" ${!(item.area_keys||[]).includes(area.key)?'hidden':''}>
          ${fixed?`<p class="sub">${sensors.length} Sabotagekontakte · sofort nach abgeschlossener Scharfschaltung.</p>`:`<div class="table-wrap"><table class="rule-table"><thead><tr><th>Sensor / Entität</th><th>Verhalten in diesem Modus</th></tr></thead><tbody>${sensors.map(sensor=>{
            const si=this._doc.sensors.indexOf(sensor),link=sensor.memberships.find(m=>m.area===area.key);
            return sensor.entities.map((ent,ei)=>{
              if(!(link.entity_keys||sensor.entities.map(e=>e.key)).includes(ent.key))return '';
              const rules=link.entity_rules?.[ent.key]?.mode_rules||link.mode_rules;
              const rule=rules?.[item.key];
              const value=rule?.enabled===false||(d.index!==null&&(item.area_keys||[]).includes(area.key)&&rules&&!rule)?'disabled':rule?.behavior||'instant';
              return `<tr><td>${E(sensor.name)}<span class="sub">${sensor.entities.length>1?E(ent.name||this._entityLabel(ent.entity_id)):''}</span></td><td><div class="sensor-mode-settings">${this._select(`mode-rule-${area.key}-${si}-${ei}`,'Auslösung',value,[['instant','Sofort'],['standby','Nach Standby-Zeit'],['disabled','Nicht überwachen'],...(value==='legacy_delay'?[['legacy_delay',`Bestehende eigene Zeit (${rule.delay_seconds}s)`]]:[])])}${this._select(`mode-initial-${area.key}-${si}-${ei}`,'Bei bereits ausgelöstem Kontakt',rule?.initial_policy||link.entity_rules?.[ent.key]?.initial_policy||link.initial_policy||'block',[['block','Scharfschalten blockieren'],['defer','Nach Normalisierung mit überwachen'],['ignore','Bis zum nächsten Scharfschalten ignorieren']])}</div>${this._switch(`mode-close-${area.key}-${si}-${ei}`,'Beim Schließen sofort scharfschalten',rule?.arm_on_close===true)}</td></tr>`;
            }).join('');
          }).join('')||'<tr><td colspan="2">Diesem Bereich sind noch keine Sensoren zugeordnet.</td></tr>'}</tbody></table></div>`}
        </div></section></details>`;
      }).join('')}
`);
  }
  _applyModeRules(candidate,mode) {
    const old=this._dialog.item;
    for(const [si,sensor] of candidate.sensors.entries())for(const link of sensor.memberships||[]){
      if(!mode.area_keys.includes(link.area)){
        if(link.mode_rules)delete link.mode_rules[mode.key];
        if(link.diagnostic_reactions)delete link.diagnostic_reactions[mode.key];
        for(const override of Object.values(link.entity_rules||{}))if(override.mode_rules)delete override.mode_rules[mode.key];
        continue;
      }
      link.entity_rules ||= {};
      for(const [ei,ent] of sensor.entities.entries()){
        if(!(link.entity_keys||sensor.entities.map(e=>e.key)).includes(ent.key))continue;
        const field=this._field(`mode-rule-${link.area}-${si}-${ei}`);
        if(!field)continue;
        const value=field.value;
        const override=link.entity_rules[ent.key] ||= {};
        // Materialize inherited policies once so editing one mode cannot disable another.
        override.mode_rules ||= clone(link.mode_rules||Object.fromEntries(candidate.modes.filter(m=>m.area_keys.includes(link.area)&&m.key!==mode.key).map(m=>[m.key,{}])));
        const prior=override.mode_rules[mode.key]||{};
        const arm_on_close=this._checked(`mode-close-${link.area}-${si}-${ei}`);
        const initial_policy=this._val(`mode-initial-${link.area}-${si}-${ei}`)||prior.initial_policy||link.initial_policy||'block';
        override.mode_rules[mode.key]=value==='disabled'?{enabled:false}:value==='legacy_delay'?{...prior,initial_policy,arm_on_close}:{behavior:value,initial_policy,arm_on_close,follow_entry_delay:prior.follow_entry_delay||false};
      }
    }
  }

  _v2SensorDialog() {
    const d=this._dialog,item=d.item;
    if(!d.selectedIds){d.selectedIds=new Set((item.entities||[]).map(x=>x.entity_id));d.entityConfig={};
      for(const ent of item.entities||[])d.entityConfig[ent.entity_id]=clone(ent);
      d.search='';d.showAll=false;
    }
    const devices=[...this._catalog.devices].sort((a,b)=>this._deviceLabel(a).localeCompare(this._deviceLabel(b),'de'));
    const deviceOptions=[['','Ohne Gerät / nur Entitäten'],...devices.map(x=>[x.id,this._deviceLabel(x)])];
    if(item.device_id && !devices.some(d=>d.id===item.device_id))deviceOptions.push([item.device_id,'Bisheriges Gerät (nicht im Register)']);
    const assigned=d.area??((item.memberships||[]).length===1?item.memberships[0].area:'');
    const members=this._select('sensor-area','Bereich',assigned,[['','Bereich wählen'],...this._doc.areas.filter(a=>a.key!==SABOTAGE_AREA).map(a=>[a.key,a.name])]);
    const sources=(fields)=>'<div class="form-grid">'+fields.map(([key,label])=>`<div data-diagnostic-picker="${E(key)}" data-picker-label="${E(label)}">${this._input('diagnostic-'+key,label,(key==='sabotage'?item.sabotage?.entity_id:(item.diagnostic_entities||{})[key])||'','text')}</div>`).join('')+'</div>';
    const values=(fields)=>'<div class="form-grid">'+fields.map(([key,label])=>this._input('diagvalue-'+key,label,(item.diagnostic_values||{})[key]?.join(', ')??'','text')).join('')+'</div>';
    return this._v2Modal('Sensor bearbeiten',`<div class="form-grid sensor-header">${this._input('name','Sensorname',item.name)}${this._select('sensor_type','Sensortyp',item.sensor_type||'auto',SENSOR_TYPES)}<div id="sensor-device-picker">${this._select('device_id','Home-Assistant-Gerät',item.device_id||'',deviceOptions)}</div></div>
      <div class="row"><div id="sensor-symbol-preview" class="entity-statuses"></div><button type="button" class="btn small" data-dialog-action="suggest-device">Gerätefunktionen vorschlagen</button></div><p id="device-suggestions" class="sub" hidden></p>
      ${this._group('Überwachte Entitäten',`<div id="sensor-native-picker"></div><div id="sensor-picker-fallback"><div class="row-grid">${this._input('entity-search','Entitäten suchen',d.search||'','search')}</div>
        <div id="sensor-entity-picker" class="entity-picker">${this._sensorChoices()}</div>
        </div>${this._checkbox('entity-show-all','Auch Entitäten anderer Geräte anzeigen',d.showAll)}
        <details><summary>Entitäts-ID manuell eingeben</summary><div class="row flex-start">${this._input('manual-entity','Entität manuell hinzufügen','','text')}<button type="button" class="btn small" data-dialog-action="add-manual-entity">Hinzufügen</button></div></details>`)}
      <div id="sensor-selected-config">${this._sensorEntityConfig()}</div>
      <details class="sensor-advanced"><summary>Sabotagekontakt (optional)</summary><div><div class="sub">Live: <span id="sabotage-live" data-live-entity="${E(item.sabotage?.entity_id||'')}" data-live-attribute="${E(item.sabotage?.attribute||'')}">${E(this._liveValue(item.sabotage?.entity_id,item.sabotage?.attribute))}</span></div>${sources([['sabotage','Sabotagekontakt']])}
        ${this._input('sabotage-attribute','Attribut (optional)',item.sabotage?.attribute||'')}
        <datalist id="sabotage-attributes"></datalist>
        <div class="form-grid">${this._input('sabotage-active','Sabotage bei',(item.sabotage?.active_states||[]).join(', '))}${this._input('sabotage-normal','Normal bei',(item.sabotage?.normal_states||[]).join(', '))}</div></div></details>
      <details class="sensor-advanced"><summary>Batterie (optional)</summary>
        ${sources([['battery','Batteriewarnung / LOW_BAT'],['battery_level','Batteriestand in % (falls vorhanden)'],['battery_voltage','Batteriespannung (V)']])}
        ${values([['battery_low_states','Warnung bei','low'],['battery_good_states','Keine Warnung bei','good, ok'],['battery_empty_states','Leer bei (optional)','empty']])}
      </details>
      <details class="sensor-advanced"><summary>Verbindung und Funkdiagnose (optional)</summary>
        ${this._select('connection-method','Erreichbarkeit ermitteln',item.connection?.method||(item.diagnostic_entities?.connectivity?'states':'disabled'),[['disabled','Nicht überwachen'],['availability','Verfügbarkeit einer Geräteentität'],['rssi','RSSI-Grenzwert'],['states','Zustände einer Entität']])}
        <div id="connection-reference">${sources([['connectivity','Referenzentität des Geräts']])}</div>
        <div id="connection-state-values">${values([['connectivity_online_states','Verbindung vorhanden bei',''],['connectivity_offline_states','Verbindung ausgefallen bei','']])}</div>
        <div id="connection-rssi-sources">${sources([['rssi_device','RSSI Gerät'],['rssi_peer','RSSI Peer']])}</div>
        <div id="connection-signal-settings"><div class="form-grid">
          ${this._select('connection-rssi-channel','Signalstärke verwenden',item.connection?.rssi_channel||'rssi_device',[['rssi_device','RSSI Gerät'],['rssi_peer','RSSI Peer']])}
          ${this._select('connection-direction','Signal wird schlechter bei',item.connection?.direction||'below',[['below','Kleineren Werten (z. B. negative dBm)'],['above','Größeren Werten (z. B. positive RSSI-Angaben)']])}
          ${this._input('connection-warning','Grenze Grün / Gelb',item.connection?.warning_threshold??-75,'number')}
          ${this._input('connection-offline','Grenze Gelb / Rot (Verbindung)',item.connection?.offline_threshold??-90,'number')}
        </div></div>
      </details>
      <datalist id="diagnostic-entity-list">${this._entityCandidates().map(id=>`<option value="${E(id)}"></option>`).join('')}</datalist>
      ${this._group('Bereichszuordnung',`${members}${(item.memberships||[]).length>1?'<p class="tone-warning">Der Sensor hat bisher mehrere Bereiche. Mit dem Speichern wird nur der gewählte Bereich beibehalten.</p>':''}<p class="sub">Ein Sensor gehört zu einem Bereich. Beim Verschieben gelten zunächst die Standardregeln des Zielbereichs; das Verhalten je Modus wird in den Modi eingestellt.</p>`)}`);
  }

  _sensorChoices() {
    const d=this._dialog,device=d.item.device_id||'',search=(d.search||'').toLocaleLowerCase('de');
    const all=this._entityCandidates().filter(id=>(d.showAll || !device || this._entityDevice(id)===device || d.selectedIds.has(id))
      && (!search || id.toLocaleLowerCase('de').includes(search) || this._entityLabel(id).toLocaleLowerCase('de').includes(search)));
    const hits=all.slice(0,120),more=all.length>hits.length;
    for(const selected of d.selectedIds)if(!hits.includes(selected))hits.push(selected);
    return `${hits.map(id=>`<label class="check entity-choice" data-entity-choice="${E(id)}"><input type="checkbox" data-choose-entity="${E(id)}" ${d.selectedIds.has(id)?'checked':''}><span>${E(this._entityLabel(id))} <span class="sub">${E(id)} · <span data-live-entity="${E(id)}">${E(this._hass?.states?.[id]?.state??'nicht verfügbar')}</span></span></span></label>`).join('')||'<p class="sub">Keine passenden Entitäten. Bitte Filter ausschalten oder Kennung manuell hinzufügen.</p>'}
     ${more?'<p class="sub">Weitere Entitäten vorhanden: Suche eingrenzen.</p>':''}`;
  }

  _sensorEntityConfig() {
    const d=this._dialog;
    return [...d.selectedIds].map((id,i)=>{
      const ent=d.entityConfig[id] ||= {entity_id:id,...this._suggestedStatePair(id)},attr=ent.attribute||'';
      const attributes=Object.entries(this._hass?.states?.[id]?.attributes||{}).filter(([key,value])=>typeof value==='string'||typeof value==='number'||typeof value==='boolean').map(([k])=>k);
      const optionList=this._stateOptions(id,attr);
      return `<details class="sensor-advanced" data-entity-editor="${E(id)}" ${d.entityOpen?.has(id)?'open':''}><summary>${E(this._entityLabel(id))}</summary>
      <div class="sub">${E(id)} · Live: <span data-live-entity="${E(id)}" data-live-attribute="${E(attr)}">${E(this._liveValue(id,attr))}</span></div>
      <div class="form-grid">${this._input(`entity-name-${i}`,'Eigener Entitätsname (optional)',ent.name||'')}
      <label class="field">Attribut (optional)<input data-entity-attribute="${E(id)}" list="attribute-suggestions-${i}" value="${E(attr)}" placeholder="Leer = Entitätszustand"><datalist id="attribute-suggestions-${i}">${attributes.map(x=>`<option value="${E(x)}"></option>`).join('')}</datalist></label>
      <label class="field">Auslösewerte<input data-entity-active="${E(id)}" list="state-suggestions-${i}" value="${E((ent.active_states||[]).join(', '))}"></label>
      <label class="field">Normalwerte<input data-entity-normal="${E(id)}" list="state-suggestions-${i}" value="${E((ent.normal_states||[]).join(', '))}"></label>
      <datalist id="state-suggestions-${i}">${optionList.map(x=>`<option value="${E(x)}"></option>`).join('')}</datalist></div>
      <div class="sub">Werte aus Home Assistant werden vorgeschlagen. Freie Eingabe ist jederzeit möglich.</div></details>`;
    }).join('') || '<p class="sub">Noch keine überwachte Entität ausgewählt.</p>';
  }

  _v2MembershipDialog() {
    const d=this._dialog,sensor=d.item,area=this._doc.areas.find(a=>a.key===d.area),members=sensor.memberships||[];
    const link=members.find(m=>m.area===d.area)||{area:d.area,entity_keys:[],entity_rules:{}};
    const modes=this._modesForArea(d.area);
    const rows=(sensor.entities||[]).map((ent,i)=>{
      const chosen=(link.entity_keys||sensor.entities.map(e=>e.key)).includes(ent.key),override=link.entity_rules?.[ent.key]||{};
      return `<div class="list-item"><div>${this._checkbox(`mem-use-${i}`,ent.name||this._entityLabel(ent.entity_id),chosen)}<details class="sensor-advanced"><summary>Abweichende Zustandszuordnung (optional)</summary><div class="form-grid">${this._input(`mem-active-${i}`,'Auslösewerte',(override.active_states||ent.active_states||['on']).join(', '))}${this._input(`mem-normal-${i}`,'Normalwerte',(override.normal_states||ent.normal_states||['off']).join(', '))}</div></details></div></div>`;
    }).join('');
    return this._v2Modal(`Zuordnung: ${sensor.name} · ${area?.name||d.area}`,
      `<p class="sub">Sofortalarm, Standby und Ausnahmen werden zentral im jeweiligen Modus eingestellt.</p>${rows}
      <details class="sensor-advanced"><summary>Scharfschalt- und Störungsregeln</summary><div class="form-grid">${this._select('initial_policy','Beim Aktivieren bereits ausgelöst',link.initial_policy||'block',[['block','Scharfschaltung blockieren'],['defer','Bis zur Normalmeldung ausnehmen']])}
      ${this._select('fault_policy','Bei Sensorstörung',link.fault_policy||'block',[['block','Scharfschaltung blockieren'],['warn','Mit Warnung erlauben'],['alarm','Alarmaktionen auslösen']])}
      ${this._select('diagnostic_arm_policy','Bei Erreichbarkeitsstörung vor Scharfschaltung',link.diagnostic_arm_policy||link.fault_policy||'block',[['block','Scharfschaltung blockieren'],['warn','Mit Warnung scharf schalten'],['alarm','Beim Scharfschalten Alarm auslösen']])}</div>
      ${this._group('Diagnose-Reaktionen in diesem Alarmbereich',`<p class="sub">Nur bei aktivem, fertig scharfem Modus. Standard: ausschließlich protokollieren. „HA-Ereignis“ kann über eine eigene Automation eine Benachrichtigung auslösen. Alarmaktionen nur nach ausdrücklicher Auswahl und nur bei bestätigtem Offline-Ereignis nach Ablauf der Wartezeit.</p>
        ${modes.map(mode=>{const old=link.diagnostic_reactions?.[mode.key]||{};
          return `<fieldset><legend>${E(mode.name)}</legend><div class="form-grid">
          ${this._select('mem-diag-offline-'+mode.key,'Verbindungsausfall',old.offline||'log',[['log','Protokollieren'],['event','HA-Ereignis'],['alarm','Alarmaktionen auslösen']])}
          ${this._select('mem-diag-low-'+mode.key,'Offline bei zuvor schwacher Batterie',old.offline_low_battery||'log',[['log','Protokollieren'],['event','HA-Ereignis'],['alarm','Alarmaktionen auslösen']])}
          ${this._select('mem-diag-battery-'+mode.key,'Schwache Batterie',old.battery||'log',[['log','Protokollieren'],['event','HA-Ereignis']])}
          ${this._select('mem-diag-unstable-'+mode.key,'Instabile Funkverbindung',old.unstable||'log',[['log','Protokollieren'],['event','HA-Ereignis']])}
          </div></fieldset>`;}).join('')}`)}</details>`);
  }

  _v2ActionDialog() {
    const d=this._dialog,item=d.item;
    if(!d.selectedTargets)d.selectedTargets=new Set(item.entity_ids||[]);
    const domain=item.domain||'light',kind=['siren','light','switch','notify','script'].includes(domain)?domain:'custom';
    const svcs=this._catalog.services?.[domain]||{};
    const notify=Object.entries(this._catalog.services?.notify||{}).map(([key,value])=>[key,value.name||key]);
    if(domain==='notify'&&!notify.some(([key])=>key===item.service))notify.push([item.service||'notify',item.service||'notify']);
    return this._v2Modal('Aktion bearbeiten',`${this._v2KeyField(d)}${this._input('name','Aktionsname',item.name)}${this._group('Gilt für Bereiche',this._doc.areas.map(a=>this._switch('action-area-'+a.key,a.name,(d.index===null?a.key===this._area:(a.actions||[]).some(x=>x.key===item.key)))).join(''))}${this._group('Gilt für Modi',this._doc.modes.map(m=>this._switch('action-mode-'+m.key,m.name,item.mode_keys==null||item.mode_keys.includes(m.key))).join(''))}
      <div class="form-grid">${this._select('action-kind','Was soll passieren?',kind,[['siren','Sirene einschalten'],['light','Licht einschalten'],['switch','Schalter einschalten'],['notify','Benachrichtigung senden'],['script','Home-Assistant-Skript starten'],['custom','Andere Home-Assistant-Aktion']])}
      ${this._select('action-when','Ereignis',item.when||'alarm',[['alarm','Alarm'],['armed','Scharfschalten'],['disarmed','Unscharfschalten'],['battery','Batteriewarnung'],['offline','Verbindungsverlust'],['unstable','Instabile Verbindung']])}${this._select('action-timing','Zeitpunkt',item.timing||(['armed','disarmed'].includes(item.when)?'after':'during'),[['before','Vor'],['during','Während'],['after','Nach']])}</div><p class="sub" id="action-timing-hint"></p>
      <div id="action-notification" ${kind==='notify'?'':'hidden'}>${this._select('notify-service','Empfänger / Benachrichtigungsdienst',item.service||'',notify)}${this._input('notify-title','Titel (optional)',item.data?.title||'')}${this._input('notify-message','Nachricht',item.data?.message||'')}<div class="actions token-buttons" style="margin-top:10px">${[['alarm_message','Alarmmeldung'],['sensor','Sensor'],['area','Bereich'],['mode','Modus'],['time','Zeitpunkt']].map(([key,label])=>`<button class="btn small" type="button" data-insert-token="${key}">+ ${label}</button>`).join('')}</div><div class="notification-options">${this._select('notification-platform','Benachrichtigungsprofil',item.notification?.platform||'standard',[['standard','Standard'],['ios','iOS Alarmmeldung'],['android','Android Alarmmeldung']])}${this._switch('notification-critical','Kritischer Alarmton',item.notification?.critical??true)}${this._switch('notification-persistent','Dauerhaft anzeigen (Android)',item.notification?.persistent??true)}${this._switch('notification-buttons','Aktionsbuttons mit Bestätigung',item.notification?.buttons??true)}${this._switch('notification-tts','Android-TTS zusätzlich sprechen',item.notification?.tts??false)}<div id="tts-text-editor"><label class="field">TTS-Ansagetext<textarea data-field="notification-tts-text" rows="3" maxlength="10000" placeholder="Leer = Benachrichtigungstext">${E(item.notification?.tts_text||'')}</textarea></label><div class="actions token-buttons">${[['alarm_message','Alarmmeldung'],['sensor','Sensor'],['area','Bereich'],['mode','Modus'],['time','Zeitpunkt']].map(([key,label])=>`<button class="btn small" type="button" data-insert-token="${key}" data-token-target="notification-tts-text">+ ${label}</button>`).join('')}</div></div>${this._switch('notification-tts-max','TTS vorübergehend mit maximaler Alarmlautstärke',item.notification?.tts_max??false)}${this._duration('notification-tts-repeat','TTS-Wiederholungsabstand (0 = einmalig)',item.notification?.tts_repeat_seconds||0)}${this._switch('notification-alarm-stream','Android-Alarmton wie im getesteten Skript',item.notification?.alarm_stream??false)}${this._switch('notification-screen-on','Bildschirm bei Alarm einschalten',item.notification?.screen_on??false)}${this._switch('notification-open-alarm','Vollbild-Alarmansicht automatisch öffnen',item.notification?.open_alarm??false)}<details class="sensor-advanced"><summary>Smartphone für kritische Alarme einrichten</summary><p><strong>Android:</strong> Bei aktiviertem Alarmton wie im Test wird der Kanal alarm_stream verwendet. Sonst nach der ersten kritischen Testmeldung den Kanal „Alarmzentrale Alarm“ unter Home Assistant → Benachrichtigungen öffnen. Hohe Wichtigkeit, Ton und „Nicht stören ignorieren“ erlauben. Auch die Ausnahmen im verwendeten Schlaf-/Arbeitsmodus prüfen. Auf Samsung gegebenenfalls zuerst Benachrichtigungskategorien für Apps aktivieren. Die Alarm-Lautstärke und die TTS-Ausgabe am Gerät prüfen. TTS kann alle 10 Sekunden bis 60 Minuten wiederholt werden; 0 bedeutet einmalig. Wiederholungen enden beim Stummschalten, Deaktivieren, Quittieren oder Ablauf der Alarmaktionsdauer. Bildschirm einschalten und automatisches Öffnen sind geräteabhängig; der Sperrbildschirm wird nicht entsperrt.</p><p><strong>iOS:</strong> Einstellungen → Mitteilungen → Home Assistant → Kritische Hinweise und Töne erlauben. Kritische Alarme kommen getrennt von der leisen, aktualisierbaren Statusmeldung. Auf die Meldung tippen öffnet den Alarm.</p><p class="sub">Eine Freigabe am Smartphone kann nicht aus Home Assistant erzwungen werden. Benachrichtigungen direkt in diesem Aktionsdialog testen.</p></details><p class="sub">Smartphone-Aktionen öffnen Wachturm zur Bestätigung. iOS erlaubt keine garantiert unentfernbare Meldung. Kritischer Ton benötigt passende Smartphone-Berechtigungen.</p></div></div>
      <div id="action-targets" ${kind==='notify'?'hidden':''}><h3>Zielgeräte / Entitäten</h3><div id="action-native-picker"></div><div id="action-target-fallback">${this._input('action-target-search','Entitäten suchen','','search')}<div class="entity-picker" id="action-entity-picker">${this._actionChoices()}</div></div>
      <details class="sensor-advanced"><summary>Entitäts-ID manuell eingeben</summary><div class="row flex-start">${this._input('manual-target','Zielentität','','text')}<button type="button" class="btn small" data-dialog-action="add-manual-target">Hinzufügen</button></div></details></div>
      <p id="action-preview" class="notice"></p>${this._actionTestMarkup()}
      <details class="sensor-advanced" ${kind==='custom'?'open':''}><summary>Erweiterte Home-Assistant-Einstellungen</summary><div class="form-grid">${this._input('domain','Domain',domain)}<label class="field">Aktion<input data-field="service" list="ha-services" value="${E(item.service||'turn_on')}"><datalist id="ha-services">${Object.keys(svcs).map(key=>`<option value="${E(key)}"></option>`).join('')}</datalist></label></div>
      <label class="field">Zusätzliche Daten (JSON)<textarea data-field="data" rows="4">${E(JSON.stringify(item.data||{},null,2))}</textarea></label></details>
      ${this._checkbox('repeat_on_retrigger','Bei erneutem Alarm wiederholen',item.repeat_on_retrigger)}${this._checkbox('timed','Nach Alarmaktionsdauer automatisch beenden',item.timed)}<p class="sub">Automatisches Beenden gilt nur für unterstützte Alarm-Ausgänge, die Wachturm selbst eingeschaltet hat.</p>`);
  }
  _actionTestMarkup(){
    const d=this._dialog,item=d.item;
    return `<section class="action-test-panel"><h3>Aktion testen / Alarm simulieren</h3><p class="sub">Test mit den aktuellen Eingaben, ohne Speichern. Benachrichtigungen sind als TEST markiert. Gewählte Ausgänge und Skripte werden tatsächlich ausgeführt. Automatisches Testende nach 60 Sekunden; selbst eingeschaltete unterstützte Ausgänge werden wieder beendet.</p><div class="form-grid">${this._select('test-action-area','Testbereich',this._area||this._doc.areas[0]?.key,this._doc.areas.map(a=>[a.key,a.name]))}${this._select('test-action-mode','Testmodus',this._doc.modes.find(m=>(item.mode_keys==null||item.mode_keys.includes(m.key))&&m.area_keys?.includes(this._area))?.key||this._doc.modes[0]?.key,this._doc.modes.map(m=>[m.key,m.name]))}</div><div class="actions"><button type="button" class="btn" data-action-test="start">Testen / Simulieren</button><button type="button" class="btn" data-action-test="mute">Test stumm</button><button type="button" class="btn" data-action-test="stop">Test beenden</button></div><div role="status" id="action-test-status"></div></section>`;
  }
  _actionTestFeedback(text,error=false){const node=this.shadowRoot.getElementById('action-test-status');if(node){node.textContent=text;node.className=error?'tone-error':'tone-good';}else{this._notice=text;if(error)this._error=text;this._render();}}
  async _startActionTest(){
    if(this._testingAction)return;this._testingAction=true;
    try{
      const action=this._readDialog(),area=this._val('test-action-area'),mode=this._val('test-action-mode');
      if(!this._checked('action-area-'+area))throw Error('Testbereich ist für diese Aktion nicht ausgewählt.');
      if(!action.mode_keys.includes(mode))throw Error('Testmodus ist für diese Aktion nicht ausgewählt.');
      const result=await this._hass.callWS({type:'ha_alarmzentrale/runtime/action_test',operation:'start',action,area,mode});
      this._runtime.simulation=result.simulation;this._actionTestFeedback('Test läuft · '+action.name+' → '+action.domain+'.'+action.service+' · Automatisches Ende nach 60 Sekunden.');
    }catch(e){this._actionTestFeedback(this._explain(e.message||e),true);}finally{this._testingAction=false;}
  }
  _syncActionTestChoices(){
    const area=this._field('test-action-area'),mode=this._field('test-action-mode');if(!area||!mode)return;
    const previous=area.value,areas=this._doc.areas.filter(a=>this._checked('action-area-'+a.key));
    area.innerHTML=areas.map(a=>`<option value="${E(a.key)}">${E(a.name)}</option>`).join('');if(areas.some(a=>a.key===previous))area.value=previous;
    const previousMode=mode.value,modes=this._doc.modes.filter(m=>this._checked('action-mode-'+m.key)&&m.area_keys?.includes(area.value));
    mode.innerHTML=modes.map(m=>`<option value="${E(m.key)}">${E(m.name)}</option>`).join('');if(modes.some(m=>m.key===previousMode))mode.value=previousMode;
  }
  async _controlActionTest(operation,token){
    if(this._testingAction)return;this._testingAction=true;
    try{
      const result=await this._hass.callWS({type:'ha_alarmzentrale/runtime/action_test',operation,token:token||this._runtime?.simulation?.token||''});
      if(this._runtime)this._runtime.simulation=result.simulation;
      this._actionTestFeedback(operation==='mute'?'Test stummgeschaltet.':'Test beendet.');
    }catch(e){this._actionTestFeedback(this._explain(e.message||e),true);}finally{this._testingAction=false;}
  }
  _actionTimings(when) {
    return when==='armed'?['before','during','after']:when==='disarmed'?['after']:when==='alarm'?['before','during','after']:['during','after'];
  }
  _updateActionControls() {
    const kind=this._val('action-kind'),when=this._val('action-when');let platform=this._val('notification-platform');
    const timingField=this._field('action-timing'),allowed=this._actionTimings(when);
    if(timingField){for(const option of timingField.options)option.disabled=!allowed.includes(option.value);if(!allowed.includes(timingField.value))timingField.value=['armed','disarmed'].includes(when)?'after':'during';}
    const timing=timingField?.value||(['armed','disarmed'].includes(when)?'after':'during');
    const profile=this._field('notification-platform');
    if(profile){profile.disabled=when!=='alarm'||timing!=='during';if(profile.disabled){profile.value='standard';platform='standard';}}
    const hint=this.shadowRoot.getElementById('action-timing-hint');
    if(hint)hint.textContent=when==='armed'?(timing==='before'?'Beim angenommenen Schaltauftrag, nach erfolgreicher Bereitschaftsprüfung. Aktionen laufen asynchron und verzögern das Scharfschalten nicht.':timing==='during'?'Einmal zu Beginn der Ausgangszeit; ohne Ausgangszeit entfällt dieser Zeitpunkt.':'Nach abgeschlossener Ausgangszeit, sobald die Überwachung scharf ist.'):when==='alarm'?(timing==='before'?'Zu Beginn der Eingangszeit; bei sofortigem Alarm gibt es keinen Vorlauf.':timing==='after'?'Nach Quittierung oder Unscharfschalten eines aktiven Alarms.':'Bei Alarmauslösung; Wiederholung gemäß Einstellung.'):when==='disarmed'?'Unscharfschalten erfolgt sofort. Aktionen werden anschließend ausgeführt.':timing==='after'?(when==='battery'?'Sobald die Batterie wieder als normal gemeldet wird.':when==='unstable'?'Sobald die Verbindung über die eingestellte Erholungszeit stabil bleibt.':'Sobald das Gerät wieder erreichbar ist.'):'Einmal beim Erkennen der Störung. Ein Zeitpunkt davor ist nicht vorhersehbar.';
    const text=this.shadowRoot.getElementById('tts-text-editor');if(text)text.hidden=platform!=='android'||!this._checked('notification-tts');
    const repeat=this.shadowRoot.querySelector('[data-duration=notification-tts-repeat]');if(repeat)repeat.hidden=platform!=='android';
    for(const key of ['notification-critical','notification-persistent','notification-buttons','notification-tts','notification-tts-max','notification-alarm-stream','notification-screen-on','notification-open-alarm']){const field=this._field(key);if(field)field.closest('label').hidden=platform==='standard'||(['notification-persistent','notification-tts','notification-tts-max','notification-alarm-stream','notification-screen-on','notification-open-alarm'].includes(key)&&platform!=='android');}
    const message=this.shadowRoot.getElementById('action-notification'),targets=this.shadowRoot.getElementById('action-targets');
    if(message)message.hidden=kind!=='notify';if(targets)targets.hidden=kind==='notify';
    const timed=this._field('timed');if(timed){timed.disabled=when!=='alarm'||timing!=='during'||!['siren','switch','light'].includes(kind);if(timed.disabled)timed.checked=false;}
    const preview=this.shadowRoot.getElementById('action-preview');
    if(preview)preview.textContent=(this._field('action-when')?.selectedOptions?.[0]?.textContent||'Bei Alarm')+' · '+(this._field('action-timing')?.selectedOptions?.[0]?.textContent||'')+' → '+(this._val('name')||'Aktion')+(kind==='notify'?' · '+(this._val('notify-message')||'Nachricht ergänzen'):` · ${this._dialog.selectedTargets?.size||0} Zielentitäten`);
  }
  async _mountActionSelector() {
    const d=this._dialog;
    try {
      if(!customElements.get('ha-selector')){
        if(!window.loadCardHelpers)return;
        const helpers=await window.loadCardHelpers(),card=helpers.createCardElement({type:'tile',entity:'sun.sun'});
        await card.constructor.getConfigElement?.();
      }
      if(this._dialog!==d||!customElements.get('ha-selector'))return;
      const host=this.shadowRoot.getElementById('action-native-picker');if(!host||host.firstElementChild)return;
      const picker=document.createElement('ha-selector');picker.hass=this._hass;picker.selector={entity:{multiple:true}};picker.required=false;picker.value=[...d.selectedTargets];
      picker.addEventListener('value-changed',event=>{event.stopPropagation();if(this._dialog!==d)return;const ids=event.detail?.value||[];if(!Array.isArray(ids)||ids.some(id=>typeof id!=='string'||!/^[a-z0-9_]+\.[a-z0-9_]+$/.test(id)))return;d.selectedTargets=new Set(ids);this._refreshActionChoices();this._updateActionControls();});
      host.append(picker);this._refreshActionChoices();const fallback=this.shadowRoot.getElementById('action-target-fallback');if(fallback)fallback.hidden=true;
    }catch{/* Searchable fallback stays available. */}
  }
  _actionChoices() {
    const d=this._dialog,domain=d.item.domain||'',search=(d.search||'').toLocaleLowerCase('de');
    const ids=this._entityCandidates().filter(id=>(!domain||id.startsWith(domain+'.')||d.selectedTargets.has(id))
      && (!search||id.includes(search)||this._entityLabel(id).toLocaleLowerCase('de').includes(search)));
    const shown=ids.slice(0,120);
    for(const id of d.selectedTargets)if(!shown.includes(id))shown.push(id);
    return `${shown.map(id=>`<label class="check"><input type="checkbox" data-choose-target="${E(id)}" ${d.selectedTargets.has(id)?'checked':''}><span>${E(this._entityLabel(id))} <span class="sub">${E(id)}</span></span></label>`).join('')||'<p class="sub">Keine passenden Entitäten. Manuelle Eingabe ist weiterhin möglich.</p>'}${ids.length>120?'<p class="sub">Weitere Einträge vorhanden: Suche eingrenzen.</p>':''}`;
  }

  async _mountSensorSelectors() {
    const dialog=this._dialog;
    try {
      // HA loads form controls lazily. Use its bundled editor to load them,
      // retaining the searchable list on frontends without these controls.
      if(!customElements.get('ha-selector')) {
        this._selectorLoader ||= (async()=>{
          if(!window.loadCardHelpers)return;
          const helpers=await window.loadCardHelpers();
          const card=helpers.createCardElement({type:'tile',entity:'sun.sun'});
          await card.constructor.getConfigElement?.();
        })();
        await this._selectorLoader;
      }
      if(this._dialog!==dialog || dialog?.kind!=='zones' || !customElements.get('ha-selector'))return;
      const deviceHost=this.shadowRoot.getElementById('sensor-device-picker');
      if(deviceHost && !deviceHost.querySelector('ha-selector')) {
        const input=this._field('device_id');
        const devicePicker=document.createElement('ha-selector');
        devicePicker.hass=this._hass;
        devicePicker.selector={device:{}};
        devicePicker.label='';
        devicePicker.required=false;
        devicePicker.value=input.value||undefined;
        devicePicker.addEventListener('value-changed',ev=>{
          ev.stopPropagation();
          if(this._dialog!==dialog)return;
          const value=ev.detail?.value;
          if(value!=null && typeof value!=='string')return;
          if(input.value===(value||''))return;
          // Preserve the existing capture, name suggestion and filter logic.
          if(value && ![...input.options].some(option=>option.value===value)) {
            const option=document.createElement('option');
            option.value=value;option.textContent=value;input.append(option);
          }
          input.value=value||'';
          input.dispatchEvent(new Event('change'));
        });
        input.closest('label').hidden=true;
        const caption=document.createElement('span');
        caption.textContent='Home-Assistant-Gerät';
        deviceHost.append(caption,devicePicker);
      }
      this._refreshSensorSymbolPreview();
      const host=this.shadowRoot.getElementById('sensor-native-picker');
      if(!host || host.firstElementChild)return;
      const picker=document.createElement('ha-selector');
      picker.hass=this._hass;
      picker.label='';
      picker.required=false;
      // Assign properties before connecting the custom element.
      picker.selector={entity:{multiple:true}};
      picker.value=[...dialog.selectedIds];
      host.append(picker);
      this._syncSensorSelector();
      picker.addEventListener('value-changed',ev=>{
        ev.stopPropagation();
        if(this._dialog!==dialog)return;
        const value=ev.detail?.value;
        const ids=Array.isArray(value)?value:(value?[value]:[]);
        if(ids.some(id=>typeof id!=='string'||!/^[a-z0-9_]+\.[a-z0-9_]+$/.test(id)))return;
        if(ids.length===dialog.selectedIds.size && ids.every(id=>dialog.selectedIds.has(id)))return;
        this._sensorCaptureDraft();
        dialog.selectedIds=new Set(ids);
        this._refreshSensorChoices();
      });
      this.shadowRoot.getElementById('sensor-picker-fallback').hidden=true;
      this.shadowRoot.querySelectorAll('[data-diagnostic-picker]').forEach(slot=>{
        const input=slot.querySelector('input');
        const selector=document.createElement('ha-selector');
        selector.hass=this._hass;
        selector.selector={entity:this._diagnosticPickerConfig(slot,input.value)};
        selector.label=slot.dataset.pickerLabel;
        selector.required=false;
        selector.value=input.value||undefined;
        selector.addEventListener('value-changed',ev=>{
          ev.stopPropagation();
          if(this._dialog!==dialog)return;
          const value=ev.detail?.value;
          if(value!=null && typeof value!=='string')return;
          const changed=input.value!==(value||'');
          input.value=value||'';
          this._refreshSourceStates(slot.dataset.diagnosticPicker,changed);
          this._updateConnectionControls();
          selector.selector={entity:this._diagnosticPickerConfig(slot,input.value)};
        });
        const manual=document.createElement('details');
        const summary=document.createElement('summary');
        summary.textContent='Entitäts-ID manuell eingeben';
        manual.append(summary,slot.firstElementChild);
        const filter=document.createElement('label');
        filter.className='check';
        const toggle=document.createElement('input');
        toggle.type='checkbox';
        toggle.checked=Boolean(dialog.diagnosticShowAll?.[slot.dataset.diagnosticPicker]);
        toggle.addEventListener('change',()=>{
          dialog.diagnosticShowAll ||= {};
          dialog.diagnosticShowAll[slot.dataset.diagnosticPicker]=toggle.checked;
          selector.selector={entity:this._diagnosticPickerConfig(slot,input.value)};
        });
        const caption=document.createElement('span');
        caption.textContent='Auch Entitäten anderer Geräte anzeigen';
        filter.append(toggle,caption);
        slot.append(selector,filter,manual);
        // Synchronize manual edits back into the visible HA control as well.
        input.addEventListener('input',()=>{selector.value=input.value||undefined;selector.selector={entity:this._diagnosticPickerConfig(slot,input.value)};this._refreshSourceStates(slot.dataset.diagnosticPicker,true);this._updateConnectionControls();});
        this._refreshSourceStates(slot.dataset.diagnosticPicker);
      });
    } catch {
      this._selectorLoader=null;
    }
  }

  _updateConnectionControls() {
    const method=this._val('connection-method');
    const show=(id,visible)=>{const el=this.shadowRoot.getElementById(id);if(el)el.hidden=!visible;};
    show('connection-reference',['availability','states'].includes(method));
    show('connection-state-values',method==='states');
    show('connection-rssi-sources',method!=='disabled');
    show('connection-signal-settings',method!=='disabled'&&(method==='rssi'||Boolean(this._val('diagnostic-rssi_device')||this._val('diagnostic-rssi_peer'))));
    const channel=this._field('connection-rssi-channel');
    if(channel&&!this._val('diagnostic-'+channel.value)){
      if(this._val('diagnostic-rssi_device'))channel.value='rssi_device';
      else if(this._val('diagnostic-rssi_peer'))channel.value='rssi_peer';
    }
  }
  _signalQuality(raw,rule) {
    if(raw==null||String(raw).trim()===''||!Number.isFinite(Number(raw)))return 'unknown';
    const value=Number(raw);
    if(rule.direction==='below')return value<rule.offline_threshold?'active':value<rule.warning_threshold?'warning':'good';
    return value>rule.offline_threshold?'active':value>rule.warning_threshold?'warning':'good';
  }

  _liveValue(id,attribute='') {
    if(!id)return 'Nicht ausgewählt';
    const st=this._hass?.states?.[id];
    if(!st||['unknown','unavailable'].includes(st.state))return st?.state||'nicht verfügbar';
    const value=attribute?st.attributes?.[attribute]:st.state;
    return ['string','number','boolean'].includes(typeof value)?String(value):'nicht verfügbar';
  }
  _suggestedStatePair(id,attribute='',source='signal') {
    const st=this._hass?.states?.[id],cls=st?.attributes?.device_class;
    const raw=attribute?st?.attributes?.[attribute]:null;
    if(attribute)return typeof raw==='boolean'?{active_states:['true'],normal_states:['false']}:{active_states:[],normal_states:[]};
    if(id?.startsWith('binary_sensor.')||id?.startsWith('switch.')||id?.startsWith('input_boolean.')||['on','off'].includes(st?.state)){
      if(source==='connectivity'&&cls!=='connectivity'&&cls!=='problem')return {active_states:[],normal_states:[]};
      return {active_states:['on'],normal_states:['off']};
    }
    const options=this._stateOptions(id||'');
    for(const [active,normal] of [['low','good'],['offline','online'],['disconnected','connected']]){
      if(options.includes(active)&&options.includes(normal))return {active_states:[active],normal_states:[normal]};
    }
    return {active_states:[],normal_states:[]};
  }

  _refreshSourceStates(source,reset=false) {
    const groups={battery:['diagvalue-battery_low_states','diagvalue-battery_empty_states','diagvalue-battery_good_states'],
      connectivity:['diagvalue-connectivity_online_states','diagvalue-connectivity_offline_states'],
      sabotage:['sabotage-active','sabotage-normal']};
    const fields=groups[source];if(!fields)return;
    const id=this._val('diagnostic-'+source);
    const attribute=source==='sabotage'?this._val('sabotage-attribute'):'';
    const options=id?this._stateOptions(id,attribute):[];
    for(const key of fields){
      const input=this._field(key);if(!input)continue;
      if(reset)input.value='';
      const listId='suggestions-'+key;
      input.setAttribute('list',listId);
      let list=this.shadowRoot.getElementById(listId);
      if(!list){list=document.createElement('datalist');list.id=listId;input.parentElement.append(list);}
      list.innerHTML=options.map(value=>`<option value="${E(value)}"></option>`).join('');
    }
    const primary=source==='battery'?[fields[0],fields[2]]:fields;
    const empty=primary.every(key=>!this._val(key));
    if(id&&(reset||empty)){
      const pair=this._suggestedStatePair(id,attribute,source);
      const set=(key,values)=>{const field=this._field(key);if(field)field.value=values.join(', ');};
      if(source==='battery'){set(fields[0],pair.active_states);set(fields[2],pair.normal_states);}
      if(source==='sabotage'){set(fields[0],pair.active_states);set(fields[1],pair.normal_states);}
      if(source==='connectivity'){
        const isConnectivity=this._hass?.states?.[id]?.attributes?.device_class==='connectivity';
        set(fields[0],isConnectivity?pair.active_states:pair.normal_states);
        set(fields[1],isConnectivity?pair.normal_states:pair.active_states);
      }
    }
    if(source==='sabotage'){
      const live=this.shadowRoot.getElementById('sabotage-live');
      if(live){live.dataset.liveEntity=id;live.dataset.liveAttribute=attribute;live.textContent=this._liveValue(id,attribute);}
      this._field('sabotage-attribute')?.setAttribute('list','sabotage-attributes');
      const list=this.shadowRoot.getElementById('sabotage-attributes');
      if(list)list.innerHTML=Object.entries(this._hass?.states?.[id]?.attributes||{})
        .filter(([,v])=>['string','number','boolean'].includes(typeof v))
        .map(([key])=>`<option value="${E(key)}"></option>`).join('');
    }
  }

  _diagnosticPickerConfig(slot,value) {
    const d=this._dialog,device=d?.item.device_id;
    if(!device || d.diagnosticShowAll?.[slot.dataset.diagnosticPicker])return {};
    return {include_entities:[...new Set([
      ...this._entityCandidates().filter(id=>this._entityDevice(id)===device),
      ...(value?[value]:[]),
    ])]};
  }

  _syncSensorSelector() {
    const d=this._dialog;
    const picker=this.shadowRoot.querySelector('#sensor-native-picker ha-selector');
    if(!picker || d?.kind!=='zones')return;
    this.shadowRoot.querySelectorAll('[data-diagnostic-picker]').forEach(slot=>{
      const selector=slot.querySelector('ha-selector');
      if(selector)selector.selector={entity:this._diagnosticPickerConfig(slot,slot.querySelector('input').value)};
    });
    const device=d.item.device_id;
    const config={multiple:true};
    if(device && !d.showAll)config.include_entities=[...new Set([
      ...this._entityCandidates().filter(id=>this._entityDevice(id)===device),...d.selectedIds,
    ])];
    picker.selector={entity:config};
    picker.value=[...d.selectedIds];
  }

  _suggestDeviceSources() {
    const d=this._dialog;if(d?.kind!=='zones')return;
    const device=this._val('device_id');if(!device)return;
    const entities=this._catalog.entities.filter(e=>e.device_id===device&&this._hass?.states?.[e.entity_id]);
    const state=e=>this._hass.states[e.entity_id];
    const token=e=>(e.entity_id+' '+(e.original_name||'')).toLowerCase();
    const isBinary=e=>e.entity_id.startsWith('binary_sensor.');
    const signal=entities.find(e=>isBinary(e)&&['door','window','opening','garage_door','motion','occupancy','smoke','moisture','gas','carbon_monoxide','vibration','heat','sound'].includes(state(e).attributes?.device_class));
    const proposals={
      battery:entities.find(e=>isBinary(e)&&state(e).attributes?.device_class==='battery'),
      battery_level:entities.find(e=>!isBinary(e)&&state(e).attributes?.device_class==='battery'&&state(e).attributes?.unit_of_measurement==='%'),
      battery_voltage:entities.find(e=>!isBinary(e)&&state(e).attributes?.device_class==='voltage'&&/batter|betriebsspannung|operating_voltage/.test(token(e))),
      sabotage:entities.find(e=>isBinary(e)&&state(e).attributes?.device_class==='tamper'),
      rssi_device:entities.find(e=>/rssi/.test(token(e))&&!/peer/.test(token(e))),
      rssi_peer:entities.find(e=>/rssi/.test(token(e))&&/peer/.test(token(e))),
    };
    let count=0;
    if(signal&&!d.selectedIds.size){d.selectedIds.add(signal.entity_id);count++;}
    const setSource=(key,id)=>{
      const field=this._field('diagnostic-'+key);if(!field||field.value||!id)return;
      field.value=id;
      const picker=this.shadowRoot.querySelector(`[data-diagnostic-picker="${CSS.escape(key)}"] ha-selector`);if(picker)picker.value=id;
      this._refreshSourceStates(key,true);count++;
    };
    for(const [key,entity] of Object.entries(proposals))if(entity)setSource(key,entity.entity_id);
    if(signal&&!this._val('diagnostic-connectivity')&&(!d.item.connection||d.item.connection.method==='disabled')){
      setSource('connectivity',signal.entity_id);const method=this._field('connection-method');if(method)method.value='availability';
    }
    this._updateConnectionControls();
    const note=this.shadowRoot.getElementById('device-suggestions');
    if(note){note.hidden=false;note.textContent=count?`${count} passende Gerätefunktionen ergänzt. Zuordnungen können angepasst werden.`:'Keine weiteren eindeutig zuordenbaren Gerätefunktionen gefunden. Manuelle Auswahl bleibt möglich.';}
  }

  _sensorCaptureDraft() {
    const d=this._dialog;if(d?.kind!=='zones')return;
    d.item.name=this._val('name')||d.item.name;
    d.item.device_id=this._val('device_id')||null;
    if(this._field('sensor-area'))d.area=this._val('sensor-area');
    const search=this._field('entity-search');if(search)d.search=search.value;
    const show=this._field('entity-show-all');if(show)d.showAll=show.checked;
    for(const el of this.shadowRoot.querySelectorAll('[data-entity-editor]')){
      const id=el.dataset.entityEditor;
      d.entityOpen ||= new Set();if(el.open)d.entityOpen.add(id);else d.entityOpen.delete(id);
      const prev=d.entityConfig[id]||{};
      const i=[...d.selectedIds].indexOf(id);
      d.entityConfig[id]={...prev,entity_id:id,
        name:this._val(`entity-name-${i}`)||undefined,
        attribute:this._field(`entity-name-${i}`)?(el.querySelector('[data-entity-attribute]')?.value?.trim()||null):prev.attribute,
        active_states:split(el.querySelector('[data-entity-active]')?.value),
        normal_states:split(el.querySelector('[data-entity-normal]')?.value)};
    }
  }
  _refreshSensorChoices() {
    const d=this._dialog;if(d?.kind!=='zones')return;
    const target=this.shadowRoot.getElementById('sensor-entity-picker');
    if(target)target.innerHTML=this._sensorChoices();
    const config=this.shadowRoot.getElementById('sensor-selected-config');
    if(config)config.innerHTML=this._sensorEntityConfig();
    this._wireV2EntityChoices();
    this._syncSensorSelector();
    this._refreshSensorSymbolPreview();
    this.shadowRoot.querySelectorAll('[data-entity-attribute]').forEach(el=>el.addEventListener('change',()=>{
      this._sensorCaptureDraft();
      Object.assign(this._dialog.entityConfig[el.dataset.entityAttribute],this._suggestedStatePair(el.dataset.entityAttribute,el.value));
      this._refreshSensorChoices();
    }));
  }
  _wireV2EntityChoices() {
    this.shadowRoot.querySelectorAll('[data-choose-entity]').forEach(el=>el.addEventListener('change',()=>{
      this._sensorCaptureDraft();
      if(el.checked){this._dialog.selectedIds.add(el.dataset.chooseEntity);}
      else this._dialog.selectedIds.delete(el.dataset.chooseEntity);
      this._refreshSensorChoices();
    }));
  }
  _refreshActionChoices() {
    const target=this.shadowRoot.getElementById('action-entity-picker');
    if(target)target.innerHTML=this._actionChoices();
    this._wireV2TargetChoices();
    const picker=this.shadowRoot.querySelector('#action-native-picker ha-selector');if(picker){const domain=this._dialog.item.domain;picker.selector={entity:{multiple:true,...(['light','switch','siren','script'].includes(domain)?{include_entities:this._entityCandidates().filter(id=>id.startsWith(domain+'.')||this._dialog.selectedTargets.has(id))}:{})}};picker.value=[...this._dialog.selectedTargets];}
    this._updateActionControls();
  }
  _wireV2TargetChoices() {
    this.shadowRoot.querySelectorAll('[data-choose-target]').forEach(el=>el.addEventListener('change',()=>{
      if(el.checked)this._dialog.selectedTargets.add(el.dataset.chooseTarget);
      else this._dialog.selectedTargets.delete(el.dataset.chooseTarget);
      this._refreshActionChoices();
    }));
  }
  _wireV2Dialog() {
    if(this._dialog?.kind==='modes')this.shadowRoot.querySelectorAll('[data-field^="mode-area-"]').forEach(el=>el.addEventListener('change',()=>{
      const section=this.shadowRoot.querySelector(`[data-mode-area="${CSS.escape(el.dataset.field.slice(10))}"]`);if(section)section.hidden=!el.checked;
    }));
    if(this._dialog?.kind==='membership'){
      this.shadowRoot.querySelectorAll('[data-field^="mem-use-"]').forEach(el=>el.addEventListener('change',()=>{
        if(!el.checked)return;
        const index=el.dataset.field.split('-')[2];
        const modeToggles=[...this.shadowRoot.querySelectorAll(`[data-field^="mem-enabled-${index}-"]`)];
        if(!modeToggles.some(x=>x.checked))modeToggles.forEach(x=>{x.checked=true;});
      }));
    }
    if(this._dialog?.kind==='zones'){
      this._refreshSensorSymbolPreview();
      this._field('sensor_type')?.addEventListener('change',()=>this._refreshSensorSymbolPreview());
      this._field('sensor-area')?.addEventListener('change',()=>{this._dialog.area=this._val('sensor-area');});
      for(const source of ['battery','connectivity','sabotage']){
        this._refreshSourceStates(source);
        this._field('diagnostic-'+source)?.addEventListener('input',()=>this._refreshSourceStates(source,true));
      }
      void this._mountSensorSelectors();
      this._wireV2EntityChoices();
      this._updateConnectionControls();
      this._field('connection-method')?.addEventListener('change',()=>this._updateConnectionControls());
      this._field('sabotage-attribute')?.addEventListener('change',()=>this._refreshSourceStates('sabotage',true));
      this._field('device_id')?.addEventListener('change',()=>{
        // In creation only, propose the device name, never overwrite a user-given name.
        const d=this._dialog,initial=this._val('name');
        if(d.index===null && initial==='Neuer Sensor'){
          const device=this._catalog.devices.find(x=>x.id===this._val('device_id'));
          if(device)this._field('name').value=this._deviceLabel(device);
        }
        this._sensorCaptureDraft();if(this._dialog.index===null)this._suggestDeviceSources();this._refreshSensorChoices();
      });
      this._field('entity-show-all')?.addEventListener('change',()=>{this._sensorCaptureDraft();this._refreshSensorChoices();});
      this._field('entity-search')?.addEventListener('input',()=>{this._sensorCaptureDraft();this._refreshSensorChoices();});
      this.shadowRoot.querySelectorAll('[data-entity-attribute]').forEach(el=>el.addEventListener('change',()=>{
        this._sensorCaptureDraft();
        Object.assign(this._dialog.entityConfig[el.dataset.entityAttribute],this._suggestedStatePair(el.dataset.entityAttribute,el.value));
        this._refreshSensorChoices();
      }));
    }
    if(this._dialog?.kind==='actions'){
      this._syncActionTestChoices();
      this.shadowRoot.querySelectorAll('[data-field^=action-area-],[data-field^=action-mode-],[data-field=test-action-area]').forEach(el=>el.addEventListener('change',()=>this._syncActionTestChoices()));
      this.shadowRoot.querySelector('[data-action-test=start]')?.addEventListener('click',()=>this._startActionTest());
      this.shadowRoot.querySelector('[data-action-test=stop]')?.addEventListener('click',()=>this._controlActionTest('stop'));
      this.shadowRoot.querySelector('[data-action-test=mute]')?.addEventListener('click',()=>this._controlActionTest('mute'));
      this.shadowRoot.querySelectorAll('[data-insert-token]').forEach(button=>button.addEventListener('click',()=>{const input=this._field(button.dataset.tokenTarget||'notify-message');input.setRangeText('{'+button.dataset.insertToken+'}',input.selectionStart??input.value.length,input.selectionEnd??input.value.length,'end');input.focus();this._updateActionControls();}));
      this._wireV2TargetChoices();void this._mountActionSelector();this._updateActionControls();
      this._field('notification-platform')?.addEventListener('change',()=>this._updateActionControls());
      this._field('notification-tts')?.addEventListener('change',()=>this._updateActionControls());
      this._field('action-kind')?.addEventListener('change',()=>{const kind=this._val('action-kind');if(kind!=='custom'){this._field('domain').value=kind;this._field('service').value=kind==='notify'?(this._val('notify-service')||'notify'):'turn_on';this._dialog.item.domain=kind;this._dialog.selectedTargets.clear();this._refreshActionChoices();}this._updateActionControls();});
      for(const key of ['action-when','action-timing','name','notify-message','notify-service','notification-platform'])this._field(key)?.addEventListener('input',()=>this._updateActionControls());
      this._field('domain')?.addEventListener('change',()=>{
        this._dialog.item.domain=this._val('domain');
        this._refreshActionChoices();
        const list=this.shadowRoot.querySelector('#ha-services');
        if(list)list.innerHTML=Object.keys(this._catalog.services?.[this._dialog.item.domain]||{}).map(x=>`<option value="${E(x)}"></option>`).join('');
      });
      this._field('action-target-search')?.addEventListener('input',()=>{
        this._dialog.search=this._val('action-target-search');this._refreshActionChoices();
      });
    }
  }

  _dialogActionV2(button) {
    const op=button.dataset.dialogAction,d=this._dialog;
    if(op==='suggest-device'&&d.kind==='zones'){this._sensorCaptureDraft();this._suggestDeviceSources();this._refreshSensorChoices();return true;}
    if(op==='add-manual-entity'&&d.kind==='zones'){
      this._sensorCaptureDraft();
      const id=this._val('manual-entity');
      if(!/^[a-z0-9_]+\.[a-z0-9_]+$/.test(id)){this._error='Bitte gültige Entitäts-ID eingeben.';const status=this.shadowRoot.getElementById('form-error');if(status){status.hidden=false;status.textContent=this._error;}return true;}
      d.selectedIds.add(id);this._refreshSensorChoices();this._field('manual-entity').value='';return true;
    }
    if(op==='add-manual-target'&&d.kind==='actions'){
      const id=this._val('manual-target');
      if(!/^[a-z0-9_]+\.[a-z0-9_]+$/.test(id)){const status=this.shadowRoot.getElementById('form-error');if(status){status.hidden=false;status.textContent='Bitte gültige Entitäts-ID eingeben.';}return true;}
      d.selectedTargets.add(id);this._refreshActionChoices();this._field('manual-target').value='';return true;
    }
    return false;
  }

  _readV2Dialog() {
    const d=this._dialog,item=clone(d.item),kind=d.kind;
    if(kind==='membership')return this._readV2Membership();
    if(d.index===null){item.key=this._val('key')||item.key;}
    if(!/^[a-z][a-z0-9_]*$/.test(item.key))throw Error('Interner Schlüssel: nur Kleinbuchstaben, Ziffern und Unterstriche (beginnend mit Buchstabe).');
    item.name=kind==='areas'&&d.item.key===SABOTAGE_AREA?'Sabotage':this._val('name');if(!item.name)throw Error('Bitte einen Anzeigenamen eingeben.');
    if(['areas','modes'].includes(kind)){item.icon=this._val('icon')|| (kind==='areas'?this._areaIcon(item):this._modeIcon(item));if(!/^mdi:[a-z0-9-]+$/.test(item.icon))throw Error('Ungültiges Symbol.');}
    if(kind==='areas'){
      if(d.item.key===SABOTAGE_AREA){item.key=SABOTAGE_AREA;item.name='Sabotage';}
      item.ack_policy=this._val('ack_policy');item.recovery_fault_grace_seconds=this._number('recovery_fault_grace_seconds');
    }
    if(kind==='modes'){
      item.area_keys=this._doc.areas.filter(a=>this._checked(`mode-area-${a.key}`)).map(a=>a.key);

      item.standby_seconds=this._number('standby_seconds');item.exit_delay_seconds=this._number('exit_delay_seconds');
      item.mute_after_seconds=this.shadowRoot.querySelector('[data-duration="mute_after_seconds"] [data-duration-enable]')?.checked?this._number('mute_after_seconds'):null;
      item.delay_on_mode_change=this._checked('delay_on_mode_change');item.restart_timer_on_retrigger=this._checked('restart_timer_on_retrigger');
    }
    if(kind==='zones'){
      this._sensorCaptureDraft();item.name=this._val('name');item.device_id=this._val('device_id')||null;
      item.sensor_type=this._val('sensor_type')||'auto';
      if(!SENSOR_TYPES.some(([key])=>key===item.sensor_type))throw Error('Ungültiger Sensortyp.');
      delete item.icon;
      item.diagnostic_entities=Object.fromEntries(["battery","battery_level","battery_voltage","connectivity","rssi_device","rssi_peer"]
        .map(key=>[key,this._val('diagnostic-'+key)]).filter(([,value])=>value));
      item.diagnostic_values=Object.fromEntries(["battery_low_states","battery_empty_states","battery_good_states",
        "connectivity_online_states","connectivity_offline_states"].map(key=>[key,split(this._val('diagvalue-'+key))]));
      const method=this._val('connection-method')||'disabled';
      item.connection=method==='disabled'?{...(d.item.connection||{}),method}:{method};
      if(method==='availability'){
        const id=item.diagnostic_entities.connectivity;
        if(!item.device_id||!id||this._entityDevice(id)!==item.device_id)throw Error('Für die automatische Erreichbarkeit eine Entität des gewählten Geräts auswählen.');
      }
      if(method!=='disabled'&&(method==='rssi'||item.diagnostic_entities.rssi_device||item.diagnostic_entities.rssi_peer)){
        let channel=this._val('connection-rssi-channel')||'rssi_device';
        if(!item.diagnostic_entities[channel]&&method!=='rssi')channel=item.diagnostic_entities.rssi_device?'rssi_device':'rssi_peer';
        if(!item.diagnostic_entities[channel])throw Error('Bitte die gewünschte RSSI-Quelle auswählen.');
        const direction=this._val('connection-direction')||'below';
        const warning=this._val('connection-warning'),offline=this._val('connection-offline');
        const warning_threshold=Number(warning),offline_threshold=Number(offline);
        if(!warning||!offline||!Number.isFinite(warning_threshold)||!Number.isFinite(offline_threshold)||(direction==='below'?warning_threshold<=offline_threshold:warning_threshold>=offline_threshold))throw Error('Gültige RSSI-Grenzwerte festlegen: Gelb muss vor Rot liegen.');
        Object.assign(item.connection,{rssi_channel:channel,direction,warning_threshold,offline_threshold});
      }
      const sabotageId=this._val('diagnostic-sabotage');
      if(sabotageId){
        const active_states=split(this._val('sabotage-active')),normal_states=split(this._val('sabotage-normal'));
        if(!/^[a-z0-9_]+\.[a-z0-9_]+$/.test(sabotageId)||!active_states.length||!normal_states.length||active_states.some(v=>normal_states.includes(v)||['unknown','unavailable'].includes(v))||normal_states.some(v=>['unknown','unavailable'].includes(v)))throw Error('Für den Sabotagekontakt gültige, getrennte Sabotage- und Normalwerte festlegen.');
        item.sabotage={enabled:d.item.sabotage?.enabled!==false,entity_id:sabotageId,active_states,normal_states,...(this._val('sabotage-attribute')?{attribute:this._val('sabotage-attribute')}:{})};
      }else delete item.sabotage;
      if(!d.selectedIds?.size&&!item.sabotage)throw Error('Bitte mindestens eine Entität oder einen Sabotagekontakt auswählen.');
      const known=new Set();
      item.entities=[...d.selectedIds].map(id=>{
        const e=d.entityConfig[id]||{};
        const existing=(d.item.entities||[]).find(x=>x.entity_id===id);
        let key=existing?.key||('ent_'+slug(id).slice(0,48));
        let serial=2;while(known.has(key))key=`ent_${slug(id).slice(0,40)}_${serial++}`;
        known.add(key);
        const active_states=e.active_states??[];
        const normal_states=e.normal_states??[];
        if(!active_states.length||!normal_states.length)throw Error(`Für ${id} müssen Auslöse- und Normalwerte angegeben werden.`);
        if(active_states.some(x=>normal_states.includes(x)||['unknown','unavailable'].includes(x))||normal_states.some(x=>['unknown','unavailable'].includes(x)))throw Error(`Ungültige oder überlappende Werte für ${id}.`);
        return {key,entity_id:id,...(e.name?{name:e.name}:{}),...(e.attribute?{attribute:e.attribute}:{}),active_states,normal_states};
      });
      const keys=new Set(item.entities.map(e=>e.key));
      const areaKey=this._val('sensor-area');
      if(keys.size&&!this._doc.areas.some(a=>a.key===areaKey&&a.key!==SABOTAGE_AREA))throw Error('Bitte genau einen Bereich für den Sensor auswählen.');
      const prior=(item.memberships||[]).find(m=>m.area===areaKey);
      item.memberships=keys.size?[{...(prior||{initial_policy:'block',fault_policy:'block'}),area:areaKey,
        entity_keys:prior?.entity_keys?.filter(k=>keys.has(k)).length?prior.entity_keys.filter(k=>keys.has(k)):item.entities.map(ent=>ent.key),
        ...(prior?.entity_rules?{entity_rules:Object.fromEntries(Object.entries(prior.entity_rules).filter(([key])=>keys.has(key)))}:{})}]:[];
    }
    if(kind==='actions'){
      item.domain=this._val('domain');item.service=this._val('service');
      item.entity_ids=[...d.selectedTargets];
      try{item.data=JSON.parse(this._val('data'));}catch{throw Error('Service-Daten müssen gültiges JSON sein.');}
      item.mode_keys=this._doc.modes.filter(m=>this._checked('action-mode-'+m.key)).map(m=>m.key);
      item.when=this._val('action-when')||'alarm';item.timing=this._val('action-timing')||(['armed','disarmed'].includes(item.when)?'after':'during');if(this._val('action-kind')!=='notify')delete item.notification;
      if(this._val('action-kind')==='notify'){item.notification={platform:this._val('notification-platform')||'standard',critical:this._checked('notification-critical'),persistent:this._checked('notification-persistent'),buttons:this._checked('notification-buttons'),tts:this._checked('notification-tts'),tts_max:this._checked('notification-tts-max'),tts_text:this._val('notification-tts-text'),tts_repeat_seconds:this._number('notification-tts-repeat'),screen_on:this._checked('notification-screen-on'),open_alarm:this._checked('notification-open-alarm'),alarm_stream:this._checked('notification-alarm-stream')};item.domain='notify';item.service=this._val('notify-service')||item.service;item.entity_ids=[];item.data.message=this._val('notify-message');if(!item.data.message)throw Error('Bitte eine Nachricht eingeben.');if(this._val('notify-title'))item.data.title=this._val('notify-title');else delete item.data.title;}
      item.repeat_on_retrigger=this._checked('repeat_on_retrigger');item.timed=this._checked('timed');
    }
    return item;
  }

  _readV2Membership() {
    const d=this._dialog,sensor=clone(d.item),areaKey=d.area,link=clone(sensor.memberships.find(m=>m.area===areaKey)||{area:areaKey});
    const selected=[],entity_rules={};
    for(const [i,ent] of sensor.entities.entries()){
      if(!this._checked(`mem-use-${i}`))continue;
      selected.push(ent.key);
      const active=split(this._val(`mem-active-${i}`));const normal=split(this._val(`mem-normal-${i}`));
      if(!active.length||!normal.length||active.some(x=>normal.includes(x)||['unknown','unavailable'].includes(x))||normal.some(x=>['unknown','unavailable'].includes(x)))throw Error(`Ungültige Auslöse- oder Normalwerte für ${ent.entity_id}.`);
      const override=clone(link.entity_rules?.[ent.key]||{});
      if(JSON.stringify(active)!==JSON.stringify(ent.active_states||['on']))override.active_states=active;
      else delete override.active_states;
      if(JSON.stringify(normal)!==JSON.stringify(ent.normal_states||['off']))override.normal_states=normal;
      else delete override.normal_states;
      entity_rules[ent.key]=override;
    }
    if(!selected.length)throw Error('Bitte mindestens eine Entität dieses Sensors im Bereich überwachen.');
    link.entity_keys=selected;link.entity_rules=entity_rules;
    link.initial_policy=this._val('initial_policy');link.fault_policy=this._val('fault_policy');
    link.diagnostic_arm_policy=this._val('diagnostic_arm_policy');
    link.diagnostic_reactions=Object.fromEntries(this._modesForArea(areaKey).map(mode=>{
      const values={offline:this._val('mem-diag-offline-'+mode.key),
        offline_low_battery:this._val('mem-diag-low-'+mode.key),
        battery:this._val('mem-diag-battery-'+mode.key),unstable:this._val('mem-diag-unstable-'+mode.key)};
      return [mode.key,Object.fromEntries(Object.entries(values).filter(([,v])=>v&&v!=='log'))];
    }).filter(([,values])=>Object.keys(values).length));
    sensor.memberships=sensor.memberships.map(m=>m.area===areaKey?link:m);
    return sensor;
  }
}
if(!customElements.get('ha-alarmzentrale-panel'))customElements.define('ha-alarmzentrale-panel',HaAlarmzentralePanel);
