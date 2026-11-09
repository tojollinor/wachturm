const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../custom_components/ha_alarmzentrale/frontend/alarmzentrale-panel.js'),'utf8');

function setup(native=true) {
  class Element {
    constructor(){this.children=[];this.listeners={};this.dataset={};}
    attachShadow(){this.shadowRoot={};}
    append(...items){for(const item of items){this.children.push(item);}}
    get firstElementChild(){return this.children[0];}
    addEventListener(name,fn){this.listeners[name]=fn;}
    emit(value){this.listeners['value-changed']({detail:{value},stopPropagation(){}});}
  }
  const registry=new Map(native?[['ha-selector',Element]]:[]);
  const context=vm.createContext({setTimeout,clearTimeout,Event,HTMLElement:Element,customElements:{get:k=>registry.get(k),define:(k,v)=>registry.set(k,v)},document:{createElement:()=>new Element()},window:{},CSS:{escape:x=>x}});
  vm.runInContext(source,context);
  const Panel=registry.get('ha-alarmzentrale-panel'), panel=new Panel();
  const host=new Element(), fallback=new Element(), pickerInputs={};
  const slot=new Element();slot.dataset.pickerLabel='Batteriewarnung';
  const label=new Element(), input=new Element();input.value='binary_sensor.battery';label.append(input);slot.append(label);
  slot.dataset.diagnosticPicker='battery';
  slot.querySelector=query=>query==='ha-selector'?slot.children.find(x=>x.selector):input;
  panel.shadowRoot={
    getElementById:id=>({'sensor-native-picker':host,'sensor-picker-fallback':fallback}[id]),
    querySelector:query=>query==='#sensor-native-picker ha-selector'?host.firstElementChild:null,
    querySelectorAll:query=>query==='[data-diagnostic-picker]'?[slot]:[],
  };
  panel._hass={states:{'binary_sensor.door':{state:'off',attributes:{friendly_name:'Tür'}}}};
  panel._catalog.entities=[{entity_id:'binary_sensor.door',device_id:'door'},{entity_id:'binary_sensor.other',device_id:'other'}];
  panel._dialog={kind:'zones',item:{device_id:'door'},selectedIds:new Set(['binary_sensor.external']),entityConfig:{}};
  return {panel,host,fallback,slot,input,context,registry,Element};
}
const plain=value=>JSON.parse(JSON.stringify(value));

test('native multi-selection keeps external selections while filtering to device',async()=>{
  const {panel,host,fallback}=setup();await panel._mountSensorSelectors();
  assert.equal(fallback.hidden,true);
  assert.deepEqual(plain(host.firstElementChild.selector),{entity:{multiple:true,include_entities:['binary_sensor.door','binary_sensor.external']}});
  assert.deepEqual(plain(host.firstElementChild.value),['binary_sensor.external']);
  panel._dialog.showAll=true;panel._syncSensorSelector();
  assert.deepEqual(plain(host.firstElementChild.selector),{entity:{multiple:true}});
});
test('device switch retains chosen entities',async()=>{
  const {panel,host}=setup();await panel._mountSensorSelectors();
  panel._dialog.item.device_id='other';panel._syncSensorSelector();
  assert.deepEqual(plain(host.firstElementChild.selector.entity.include_entities),['binary_sensor.other','binary_sensor.external']);
});
test('selection captures unsaved rules before replacing selected IDs; supports clearing',async()=>{
  const {panel,host}=setup();await panel._mountSensorSelectors();
  let captured,refreshes=0;
  panel._sensorCaptureDraft=()=>{captured=[...panel._dialog.selectedIds];};
  panel._refreshSensorChoices=()=>{refreshes++;};
  host.firstElementChild.emit(['binary_sensor.door']);
  assert.deepEqual(captured,['binary_sensor.external']);
  assert.deepEqual([...panel._dialog.selectedIds],['binary_sensor.door']);
  host.firstElementChild.emit([]);assert.equal(panel._dialog.selectedIds.size,0);
  assert.equal(refreshes,2);
});
test('invalid native event does not discard selections',async()=>{
  const {panel,host}=setup();await panel._mountSensorSelectors();
  host.firstElementChild.emit(['invalid']);
  assert.deepEqual([...panel._dialog.selectedIds],['binary_sensor.external']);
});
test('diagnostic picker and manual input share stored value; clear is persisted',async()=>{
  const {panel,slot,input}=setup();await panel._mountSensorSelectors();
  const selector=slot.children.find(x=>x.selector);
  assert.equal(selector.value,input.value);
  selector.emit('sensor.level');assert.equal(input.value,'sensor.level');
  selector.emit(undefined);assert.equal(input.value,'');
  input.value='sensor.manual';input.listeners.input();assert.equal(selector.value,'sensor.manual');
});
test('missing HA controls retain functional fallback',async()=>{
  const {panel,host,fallback}=setup(false);await panel._mountSensorSelectors();
  assert.equal(host.children.length,0);assert.notEqual(fallback.hidden,true);
});
test('closing dialog during lazy loading does not mount stale selectors',async()=>{
  const {panel,host,context,registry,Element}=setup(false);
  let finish;
  context.window.loadCardHelpers=()=>new Promise(resolve=>{finish=resolve;});
  const pending=panel._mountSensorSelectors();panel._dialog=null;
  finish({createCardElement:()=>({constructor:{getConfigElement:async()=>registry.set('ha-selector',Element)}})});
  await pending;assert.equal(host.children.length,0);
});
test('render escapes entity names and keeps diagnostics collapsed',()=>{
  const {panel}=setup();panel._doc={areas:[]};
  panel._dialog={kind:'zones',item:{name:'Tür',entities:[],memberships:[]}};
  panel._hass.states['binary_sensor.door'].attributes.friendly_name='<script>bad()</script>';
  const html=panel._v2SensorDialog();
  assert.ok(html.includes('sensor-native-picker'));
  assert.ok(html.includes('<details class="sensor-advanced">'));
  assert.ok(html.includes('&lt;script&gt;bad()&lt;/script&gt;'));
  assert.ok(!html.includes('<script>bad()'));
  assert.ok(html.includes('<summary>Batterie (optional)</summary>'));
  assert.ok(html.includes('<summary>Verbindung und Funkdiagnose (optional)</summary>'));
  assert.ok(html.includes('sensor-device-picker'));
});

test('native device selection forwards changes and clearing to existing filter logic',async()=>{
  const {panel,Element}=setup();
  const host=new Element(), input=new Element(), label=new Element();
  input.value='door';input.options=[{value:'door'}];
  input.closest=()=>label;
  let changes=0;
  input.dispatchEvent=()=>{changes++;panel._dialog.item.device_id=input.value||null;};
  panel._field=()=>input;
  host.querySelector=()=>host.firstElementChild;
  const previous=panel.shadowRoot.getElementById;
  panel.shadowRoot.getElementById=id=>id==='sensor-device-picker'?host:previous(id);
  await panel._mountSensorSelectors();
  const picker=host.children.find(x=>x.selector);
  assert.deepEqual(plain(picker.selector),{device:{}});
  assert.equal(picker.value,'door');assert.equal(label.hidden,true);
  picker.emit('other');assert.equal(panel._dialog.item.device_id,'other');
  picker.emit(undefined);assert.equal(panel._dialog.item.device_id,null);
  assert.equal(changes,2);
});


test('diagnostic device filter can be disabled independently and follows device switches',async()=>{
  const {panel,slot,input}=setup();await panel._mountSensorSelectors();
  const selector=slot.children.find(x=>x.selector);
  assert.deepEqual(plain(selector.selector.entity.include_entities),['binary_sensor.door','binary_sensor.battery']);
  const filter=slot.children.find(x=>x.className==='check');
  const toggle=filter.children[0];toggle.checked=true;toggle.listeners.change();
  assert.deepEqual(plain(selector.selector),{entity:{}});
  assert.equal(panel._dialog.showAll,undefined);
  toggle.checked=false;toggle.listeners.change();
  panel._dialog.item.device_id='other';panel._syncSensorSelector();
  assert.deepEqual(plain(selector.selector.entity.include_entities),['binary_sensor.other','binary_sensor.battery']);
  selector.emit(undefined);
  assert.deepEqual(plain(selector.selector.entity.include_entities),['binary_sensor.other']);
});


test('source state lists start blank, offer entity values and preserve manual values',()=>{
  const {panel,Element}=setup();
  const lists={};
  const fields=Object.fromEntries(['diagnostic-battery','diagvalue-battery_low_states','diagvalue-battery_empty_states','diagvalue-battery_good_states'].map(key=>{
    const el=new Element();el.value='';el.setAttribute=(name,value)=>{el[name]=value;};
    el.parentElement={append:list=>{lists[list.id]=list;}};
    return [key,el];
  }));
  panel._field=key=>fields[key];
  panel.shadowRoot.getElementById=id=>lists[id];
  panel._refreshSourceStates('battery');
  assert.equal(lists['suggestions-diagvalue-battery_low_states'].innerHTML,'');
  fields['diagnostic-battery'].value='binary_sensor.door';
  panel._refreshSourceStates('battery',true);
  assert.ok(lists['suggestions-diagvalue-battery_low_states'].innerHTML.includes('value="on"'));
  assert.ok(lists['suggestions-diagvalue-battery_low_states'].innerHTML.includes('value="off"'));
  assert.equal(fields['diagvalue-battery_low_states'].value,'on');
  fields['diagvalue-battery_low_states'].value='custom';
  panel._refreshSourceStates('battery');
  assert.equal(fields['diagvalue-battery_low_states'].value,'custom');
  panel._refreshSourceStates('battery',true);
  assert.equal(fields['diagvalue-battery_low_states'].value,'on');
});

test('boolean attribute offers true/false without unrelated binary entity states',()=>{
  const {panel}=setup();panel._hass.states['binary_sensor.door'].attributes.tamper=true;
  assert.deepEqual(plain(panel._stateOptions('binary_sensor.door','tamper')).sort(),['false','true']);
});

test('sabotage-only sensor saves separately, can be cleared, and requires explicit states',()=>{
  const {panel}=setup();
  const values={name:'Sabo',device_id:'door','diagnostic-sabotage':'binary_sensor.tamper','sabotage-active':'on','sabotage-normal':'off'};
  panel._dialog={kind:'zones',index:null,item:{key:'sensor',name:'Sabo',entities:[],memberships:[]},selectedIds:new Set(),entityConfig:{}};
  panel._doc={areas:[{key:'home'},{key:'system_sabotage'}]};
  panel._val=key=>values[key]||'';panel._checked=()=>false;panel._sensorCaptureDraft=()=>{};
  const saved=panel._readV2Dialog();
  assert.equal(saved.entities.length,0);
  assert.deepEqual(plain(saved.sabotage),{enabled:true,entity_id:'binary_sensor.tamper',active_states:['on'],normal_states:['off']});
  assert.deepEqual(plain(saved.diagnostic_values.battery_low_states),[]);
  values['sabotage-active']='';
  assert.throws(()=>panel._readV2Dialog(),/Sabotagekontakt/);
  values['diagnostic-sabotage']='';
  assert.throws(()=>panel._readV2Dialog(),/mindestens/);
});


test('entity status distinguishes normal, active, unmapped and unavailable values',()=>{
  const {panel}=setup();
  const sensor={key:'door',sensor_type:'door',entities:[{entity_id:'binary_sensor.door',active_states:['on'],normal_states:['off']}]};
  assert.match(panel._sensorStatusMarkup(sensor),/entity-status good/);
  panel._hass.states['binary_sensor.door'].state='on';
  assert.match(panel._sensorStatusMarkup(sensor),/entity-status active/);
  panel._hass.states['binary_sensor.door'].state='other';
  assert.match(panel._sensorStatusMarkup(sensor),/Nicht zugeordnet/);
  panel._hass.states['binary_sensor.door'].state='unavailable';
  const html=panel._sensorStatusMarkup(sensor);
  assert.match(html,/entity-status unknown/);assert.doesNotMatch(html,/entity-status good/);
});

test('status includes tamper and diagnostic sources without claiming numeric values are healthy',()=>{
  const {panel}=setup();
  panel._hass.states['binary_sensor.tamper']={state:'on',attributes:{}};
  panel._hass.states['sensor.rssi']={state:'-89',attributes:{unit_of_measurement:'dBm'}};
  const sensor={entities:[],sabotage:{entity_id:'binary_sensor.tamper',active_states:['on'],normal_states:['off']},diagnostic_entities:{rssi_device:'sensor.rssi'}};
  const html=panel._sensorStatusMarkup(sensor);
  assert.match(html,/Sabotage/);assert.match(html,/entity-status active/);
  assert.match(html,/entity-status info/);assert.match(html,/-89 dBm/);
  panel._hass.states['sensor.rssi'].state='invalid';
  assert.match(panel._sensorStatusMarkup(sensor),/Ungültiger Messwert/);
});

test('attribute status is fault when entity unavailable or attribute missing',()=>{
  const {panel}=setup();
  const sensor={entities:[{entity_id:'binary_sensor.door',attribute:'open',active_states:['true'],normal_states:['false']}]};
  assert.match(panel._sensorStatusMarkup(sensor),/Attribut fehlt/);
  panel._hass.states['binary_sensor.door'].attributes.open=false;
  assert.match(panel._sensorStatusMarkup(sensor),/entity-status good/);
  panel._hass.states['binary_sensor.door'].state='unavailable';
  assert.match(panel._sensorStatusMarkup(sensor),/entity-status unknown/);
});

test('type inference and explicit icons respect user settings and escape entity names',()=>{
  const {panel}=setup();
  const sensor={entities:[{entity_id:'binary_sensor.door',active_states:['on'],normal_states:['off']}]};
  panel._hass.states['binary_sensor.door'].attributes.device_class='window';
  assert.equal(panel._sensorIcon(sensor),'mdi:window-closed-variant');
  sensor.sensor_type='door';assert.equal(panel._sensorIcon(sensor),'mdi:door-closed');
  sensor.icon='mdi:home';assert.equal(panel._sensorIcon(sensor),'mdi:door-closed');
  sensor.icon='bad';assert.equal(panel._sensorIcon(sensor),'mdi:door-closed');
  sensor.entities[0].name='<script>bad</script>';
  const html=panel._sensorStatusMarkup(sensor);
  assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));
});

test('HA updates refresh status tiles without replacing sensor cards or edit forms',()=>{
  const {panel}=setup();
  const sensor={key:'door',entities:[{entity_id:'binary_sensor.door',active_states:['on'],normal_states:['off']}]};
  panel._doc={sensors:[sensor]};const tile={dataset:{sensorStatus:'door'}};
  panel.shadowRoot.querySelectorAll=query=>query==='[data-sensor-status]'?[tile]:[];
  panel._updateLiveValues();assert.match(tile.innerHTML,/entity-status good/);
  panel._hass.states['binary_sensor.door'].state='on';
  panel._updateLiveValues();assert.match(tile.innerHTML,/entity-status active/);
});


test('every sensor profile switches symbols between normal and triggered states',()=>{
  const {panel,context}=setup();
  const profiles=vm.runInContext('SENSOR_PROFILES',context);
  assert.equal(Object.keys(profiles).length,17);
  for(const [type,profile] of Object.entries(profiles)){
    const sensor={sensor_type:type,entities:[{entity_id:'binary_sensor.door',active_states:['on'],normal_states:['off']}]};
    assert.notEqual(panel._sensorIcon(sensor),panel._sensorIcon(sensor,true),type);
    panel._hass.states['binary_sensor.door'].state='off';
    assert.ok(panel._sensorStatusMarkup(sensor).includes('mdi:'+profile.normal),type);
    panel._hass.states['binary_sensor.door'].state='on';
    assert.ok(panel._sensorStatusMarkup(sensor).includes('mdi:'+profile.active),type);
  }
});

test('sensor dialog keeps the three header fields in one aligned grid and previews both icons',()=>{
  const {panel}=setup();panel._doc={areas:[]};
  panel._dialog={kind:'zones',item:{name:'Tür',entities:[],memberships:[]}};
  const html=panel._v2SensorDialog();
  const header=html.slice(html.indexOf('sensor-header'),html.indexOf('sensor-symbol-preview'));
  assert.ok(header.includes('data-field="name"'));
  assert.ok(header.includes('sensor-device-picker'));
  assert.ok(header.includes('data-field="sensor_type"'));
  assert.ok(!header.includes('sensor-icon-picker'));
  assert.ok(!header.includes('class="form-grid"'));
  const target={};panel.shadowRoot.getElementById=id=>id==='sensor-symbol-preview'?target:null;
  panel._val=key=>key==='sensor_type'?'window':'';
  panel._refreshSensorSymbolPreview();
  assert.match(target.innerHTML,/mdi:window-closed/);assert.match(target.innerHTML,/mdi:window-open/);
});


test('selected entity settings and sabotage start collapsed with live values inside',()=>{
  const {panel}=setup();
  panel._doc={areas:[]};
  panel._dialog={kind:'zones',item:{name:'Tür',entities:[{key:'door',entity_id:'binary_sensor.door',active_states:['on'],normal_states:['off']}],memberships:[],sabotage:{entity_id:'binary_sensor.door',active_states:['on'],normal_states:['off']}}};
  const html=panel._v2SensorDialog();
  assert.ok(html.includes('<details class="sensor-advanced" data-entity-editor="binary_sensor.door" ><summary>Tür</summary>'));
  assert.ok(html.includes('<div class="sub">binary_sensor.door · Live: <span data-live-entity="binary_sensor.door"'));
  assert.match(html,/<details class="sensor-advanced"><summary>Sabotagekontakt/);
  assert.match(html,/id="sabotage-live"/);
  assert.ok(!html.includes('kommagetrennt'));
  assert.ok(!html.includes('sensor-icon-picker'));
});

test('new binary entities preselect on/off while manual and existing values remain intact',()=>{
  const {panel}=setup();
  panel._dialog={kind:'zones',selectedIds:new Set(['binary_sensor.door']),entityConfig:{}};
  panel._sensorEntityConfig();
  assert.deepEqual(plain(panel._dialog.entityConfig['binary_sensor.door'].active_states),['on']);
  assert.deepEqual(plain(panel._dialog.entityConfig['binary_sensor.door'].normal_states),['off']);
  panel._dialog.entityConfig['binary_sensor.door'].active_states=['custom'];
  panel._sensorEntityConfig();
  assert.deepEqual(plain(panel._dialog.entityConfig['binary_sensor.door'].active_states),['custom']);
});

test('connectivity defaults distinguish connection contacts from problem contacts',()=>{
  const {panel}=setup();
  const st=panel._hass.states['binary_sensor.door'];
  st.attributes.device_class='connectivity';
  assert.deepEqual(plain(panel._suggestedStatePair('binary_sensor.door','','connectivity')),{active_states:['on'],normal_states:['off']});
  st.attributes.device_class='problem';
  assert.deepEqual(plain(panel._suggestedStatePair('binary_sensor.door','','connectivity')),{active_states:['on'],normal_states:['off']});
  delete st.attributes.device_class;
  assert.deepEqual(plain(panel._suggestedStatePair('binary_sensor.door','','connectivity')),{active_states:[],normal_states:[]});
});


test('sabotage helper offers and preselects both on/off even if only on was observed',()=>{
  const {panel,Element}=setup();
  panel._hass.states['input_boolean.sabo']={state:'on',attributes:{}};
  assert.deepEqual(plain(panel._stateOptions('input_boolean.sabo')),['on','off']);
  assert.deepEqual(plain(panel._suggestedStatePair('input_boolean.sabo','','sabotage')),{active_states:['on'],normal_states:['off']});
  const lists={},fields={};
  for(const key of ['diagnostic-sabotage','sabotage-active','sabotage-normal','sabotage-attribute']){
    const input=new Element();input.value=key==='diagnostic-sabotage'?'input_boolean.sabo':'';
    input.setAttribute=()=>{};input.parentElement={append:list=>{lists[list.id]=list;}};fields[key]=input;
  }
  panel._field=key=>fields[key];panel.shadowRoot.getElementById=id=>lists[id];
  panel._refreshSourceStates('sabotage');
  assert.equal(fields['sabotage-active'].value,'on');assert.equal(fields['sabotage-normal'].value,'off');
  fields['sabotage-active'].value='custom';panel._refreshSourceStates('sabotage');
  assert.equal(fields['sabotage-active'].value,'custom');
  panel._hass.states['sensor.sabo_helper']={state:'on',attributes:{}};
  assert.deepEqual(plain(panel._stateOptions('sensor.sabo_helper')),['on','off']);
});


test('availability status treats on and off as reachable and unavailable as offline',()=>{
  const {panel}=setup();
  const sensor={entities:[],connection:{method:'availability'},diagnostic_entities:{connectivity:'binary_sensor.door'}};
  for(const state of ['on','off']){
    panel._hass.states['binary_sensor.door'].state=state;
    assert.match(panel._sensorStatusMarkup(sensor),/entity-status good/);
    assert.ok(!panel._sensorStatusMarkup(sensor).includes('Nicht erreichbar'));
  }
  panel._hass.states['binary_sensor.door'].state='unavailable';
  assert.match(panel._sensorStatusMarkup(sensor),/Nicht erreichbar/);
});

test('RSSI thresholds drive green yellow red and unavailable indicators',()=>{
  const {panel}=setup();
  const sensor={entities:[],connection:{method:'rssi',rssi_channel:'rssi_device',direction:'below',warning_threshold:-75,offline_threshold:-90},diagnostic_entities:{rssi_device:'sensor.rssi'}};
  for(const [value,label] of [['-60','Signal gut'],['-80','Signal schwach'],['-99','Signal kritisch'],['unavailable','Signal nicht verfügbar']]){
    panel._hass.states['sensor.rssi']={state:value,attributes:{unit_of_measurement:'dBm'}};
    assert.ok(panel._sensorStatusMarkup(sensor).includes(label));
    if(['-99','unavailable'].includes(value))assert.ok(panel._sensorStatusMarkup(sensor).includes('Nicht erreichbar'));
  }
  sensor.connection.direction='above';sensor.connection.warning_threshold=75;sensor.connection.offline_threshold=90;
  assert.equal(panel._signalQuality('80',sensor.connection),'warning');
  assert.equal(panel._signalQuality('99',sensor.connection),'active');
});

test('RSSI thresholds accept negative fractional numbers without the nonnegative limit used for times',()=>{
  const {panel}=setup();panel._doc={areas:[]};panel._dialog={kind:'zones',item:{name:'Test',entities:[],memberships:[]}};
  const html=panel._v2SensorDialog();
  assert.match(html,/data-field="connection-warning"[^>]*step="any"/);
  assert.ok(!/data-field="connection-warning"[^>]*min="0"/.test(html));
});


test('area editor leaves sensor assignment to the area-scoped sensor dialog',()=>{
  const {panel}=setup();
  panel._doc={sensors:[{key:'door',name:'Haustür',entities:[{key:'opening',entity_id:'binary_sensor.door'}],memberships:[]}]};
  panel._dialog={kind:'areas',index:null,item:{key:'home',name:'Wohnung'}};
  const html=panel._v2AreaDialog();
  assert.doesNotMatch(html,/area-sensor-0/);assert.doesNotMatch(html,/area-entity-0-0/);
  panel._checked=key=>key==='area-sensor-0';
  const candidate=plain(panel._doc);panel._applyAreaMembers(candidate,{key:'home'});
  assert.deepEqual(candidate.sensors[0].memberships,[]);
});

test('newly assigned area enables sources and mode editor offers initial-state policies',()=>{
  const {panel}=setup();
  panel._doc={areas:[{key:'home',name:'Wohnung'},{key:'system_sabotage',name:'Sabotage'}],sensors:[{name:'Tür',entities:[{key:'opening',entity_id:'binary_sensor.door'}],memberships:[{area:'home',mode_rules:{}}]}]};
  panel._dialog={kind:'modes',index:0,item:{key:'night',name:'Nacht',area_keys:['system_sabotage']}};
  const html=panel._v2ModeDialog();
  assert.ok(html.indexOf('Sabotage überwachen')<html.indexOf('<summary>Wohnung'));
  assert.match(html,/value="instant" selected/);assert.match(html,/mode-initial-home-0-0/);
  assert.match(html,/Nach Normalisierung mit überwachen/);assert.match(html,/Bis zum nächsten Scharfschalten ignorieren/);
  assert.deepEqual(plain(panel._defaultV2('modes').area_keys),['system_sabotage']);
});

test('disabled sabotage keeps mapping but never displays active warning',()=>{
  const {panel}=setup();
  panel._hass.states['binary_sensor.tamper']={state:'on',attributes:{}};
  const html=panel._sensorStatusMarkup({entities:[],sabotage:{entity_id:'binary_sensor.tamper',enabled:false,active_states:['on'],normal_states:['off']}});
  assert.match(html,/Deaktiviert/);assert.doesNotMatch(html,/entity-status active/);
});


test('sabotage enable controls belong to system area and preserve sensor mapping',()=>{
  const {panel}=setup();
  const sensor={name:'Tür',entities:[],sabotage:{entity_id:'binary_sensor.tamper',enabled:false,active_states:['on'],normal_states:['off']}};
  panel._doc={sensors:[sensor]};panel._dialog={item:{key:'system_sabotage',name:'Sabotage'}};
  assert.doesNotMatch(panel._v2AreaDialog(),/area-sabotage-0|Zugeordnete Sensoren/);
  const candidate=plain(panel._doc);panel._checked=key=>key==='area-sabotage-0';
  panel._applyAreaMembers(candidate,{key:'system_sabotage'});
  assert.equal(candidate.sensors[0].sabotage.enabled,false);
  assert.equal(candidate.sensors[0].sabotage.entity_id,'binary_sensor.tamper');
});

test('history groups only matching correlation IDs and includes normalizations',()=>{
  const {panel}=setup();panel._doc={areas:[{key:'home',name:'Wohnung'}],users:[],bindings:[]};panel._haUsers=[];panel._auditFilter={};
  panel._audit=[{at:'2026-11-08T12:00:00Z',action:'disarm',outcome:'success',event_id:'same',source:'ha_account'}];
  panel._statuses=[{key:'home',events:[{at:1794139200,kind:'disarmed',event_id:'same'},{at:1794139200,kind:'zone_normal',info:'Tür'}]}];
  const html=panel._sectionAudit();
  assert.equal((html.match(/class="history-group"/g)||[]).length,1);
  assert.match(html,/2 Meldungen/);assert.match(html,/Kontakt normal/);assert.match(html,/mdi:shield-off-outline/);
  assert.doesNotMatch(html,/class="history-group"[^>]* open/);
});

test('mode areas collapse initially and time rules precede areas',()=>{
 const {panel}=setup();panel._doc={areas:[{key:'home',name:'Wohnung'}],sensors:[]};panel._dialog={index:0,item:{key:'night',name:'Nacht',area_keys:['home']}};
 const html=panel._v2ModeDialog();assert.doesNotMatch(html,/<details[^>]* open/);
 assert.ok(html.indexOf('Weitere Zeitregeln')<html.indexOf('Bereiche und Sensorverhalten'));
});


test('overview stacks areas, keeps deactivate disabled and shows normalized event',()=>{
 const {panel}=setup();panel._doc={schema:2,modes:[]};panel._runtime={};
 panel._statuses=[{key:'home',name:'Wohnung',phase:'disarmed',permissions:[],checks:{night:{ready:false,reason:'Zone already active: Haustür'}},zones:{door:{name:'Haustür',entity_id:'binary_sensor.door',observation:'active',active_in_mode:false}},events:[{at:1,kind:'zone_normal',info:'Haustür'}]}];
 let html=panel._overviewContent();assert.match(html,/area-stack/);assert.match(html,/area-details/);assert.doesNotMatch(html,/area-controls/);
 assert.match(html,/data-runtime-operation="disarm" disabled/);assert.match(html,/tone-error">Ausgelöst: Haustür/);assert.match(html,/Kontakt normal/);
 panel._statuses[0].phase='armed';panel._statuses[0].zones.door.deferred=true;
 html=panel._overviewContent();assert.match(html,/tone-warning">Ignoriert: Haustür/);
});

test('success notice expires after five seconds and newer notices replace timer',()=>{
 const {panel,context}=setup();let callback,delay,cancelled=0,removed=0;
 context.setTimeout=(fn,ms)=>{callback=fn;delay=ms;return 42;};context.clearTimeout=()=>{cancelled++;};
 panel.shadowRoot.getElementById=()=>({remove(){removed++;}});
 panel._notice='Gespeichert';assert.equal(delay,5000);assert.equal(panel._notice,'Gespeichert');
 panel._notice='Aktiviert';assert.ok(cancelled>=2);callback();assert.equal(panel._notice,'');assert.equal(removed,1);
});


test('duration controls expose minutes and seconds and preserve unlimited option',()=>{
 const {panel}=setup();
 const html=panel._duration('mute_after_seconds','Alarmaktionsdauer',135,true);
 assert.match(html,/data-duration-min value="2"/);assert.match(html,/data-duration-sec value="15"/);assert.match(html,/data-duration-enable checked/);
 assert.match(panel._duration('mute_after_seconds','Alarmaktionsdauer',null,true),/data-duration-min value="0"[^>]* disabled/);
});

test('duration changes normalize to seconds and reject out-of-range seconds',()=>{
 const {panel}=setup();const listeners=[];
 const min={value:'2',addEventListener(_,fn){listeners.push(fn);}},sec={value:'15',addEventListener(_,fn){listeners.push(fn);}},enabled={checked:true,addEventListener(_,fn){listeners.push(fn);}},hidden={};
 panel.shadowRoot.querySelectorAll=()=>[{querySelector:q=>({'[data-duration-min]':min,'[data-duration-sec]':sec,'[data-duration-enable]':enabled,'[data-field]':hidden}[q])}];
 panel._wireDurations();listeners[0]();assert.equal(hidden.value,'135');
 sec.value='60';listeners[1]();assert.equal(hidden.value,'');
 enabled.checked=false;listeners[2]();assert.equal(hidden.value,'0');assert.equal(min.disabled,true);
});

test('mode and area icons are configurable and default by meaning',()=>{
 const {panel}=setup();assert.equal(panel._modeIcon({name:'Nacht'}),'mdi:weather-night');assert.equal(panel._modeIcon({name:'Zuhause'}),'mdi:home-account');
 assert.equal(panel._modeIcon({name:'Nacht',icon:'mdi:star'}),'mdi:star');assert.equal(panel._areaIcon({key:'a',icon:'mdi:garage'}),'mdi:garage');
 assert.equal(panel._tone('armed'),'good');assert.equal(panel._tone('pending'),'warning');assert.equal(panel._tone('triggered'),'error');
});

test('radio graph displays a first data point and distinguishes empty history',()=>{
 const {panel}=setup();assert.match(panel._diagnosticTrend([{at:'2026-11-08T12:00:00Z',duty_cycle:26}],'duty_cycle'),/<circle/);
 assert.match(panel._diagnosticTrend([{at:'2026-11-08T12:00:00Z',duty_cycle:26}],'duty_cycle'),/Duty Cycle/);
 assert.match(panel._diagnosticTrend([],'carrier_sense'),/Noch kein Verlauf/);
});


test('sensor live tiles place value beside label and graphs expose scale/time inspection',()=>{
 const {panel}=setup();const html=panel._sensorStatusMarkup({key:'d',sensor_type:'door',entities:[{entity_id:'binary_sensor.door',active_states:['on'],normal_states:['off']}]});
 assert.match(html,/status-inline/);assert.match(html,/Geschlossen/);
 const graph=panel._diagnosticTrend([{at:'2026-11-08T10:00:00Z',duty_cycle:25},{at:'2026-11-08T10:01:00Z',duty_cycle:35}],'duty_cycle');
 assert.match(graph,/100 %/);assert.doesNotMatch(graph,/type="range"/);assert.match(graph,/role="tooltip"/);assert.match(graph,/Pfeiltasten/);
});

test('mode rule can shorten exit delay on close without changing initial-state policy',()=>{
 const {panel}=setup();panel._doc={areas:[{key:'home',name:'Wohnung'}],sensors:[{name:'Tür',entities:[{key:'opening',entity_id:'binary_sensor.door'}],memberships:[{area:'home'}]}]};panel._dialog={index:0,item:{key:'night',name:'Nacht',area_keys:['home']}};
 const html=panel._v2ModeDialog();assert.match(html,/mode-close-home-0-0/);assert.match(html,/Beim Schließen sofort scharfschalten/);
});

test('alarm overview deduplicates a source and records its normal return in one hint',()=>{
 const {panel}=setup();panel._doc={modes:[{key:'night',name:'Nacht',area_keys:['home']}],areas:[]};panel._runtime={summary:{phase:'triggered',mode:'night'}};panel._statuses=[{key:'home',name:'Wohnung',phase:'triggered',mode:'night',alarm:true,alarm_sources:['door'],zones:{door:{entity_id:'binary_sensor.door',name:'Haustür',observation:'normal',active_in_mode:true}},events:[]}];
 const html=panel._overviewContent();assert.doesNotMatch(html,/Auslöser:/);assert.match(html,/Wieder geschlossen/);assert.match(html,/area-heading/);assert.doesNotMatch(html,/<details class="sensor-advanced area-details" open/);
});


test('details retain both open and closed user choices and stay scoped to their view',()=>{
 const {panel}=setup();const a={dataset:{detailKey:'readiness'},open:true},b={dataset:{detailKey:'areas'},open:false};const root={querySelectorAll:()=>[a,b]};
 panel._captureDetails(root,'overview');a.open=false;b.open=true;panel._restoreDetails(root,'overview');assert.equal(a.open,true);assert.equal(b.open,false);
 a.open=false;panel._captureDetails(root,'overview');a.open=true;panel._restoreDetails(root,'overview');assert.equal(a.open,false);
 a.open=true;panel._restoreDetails(root,'other');assert.equal(a.open,true);
});


test('new areas inherit mode-wide actions but retain existing restricted actions',()=>{
  const {panel}=setup();
  const global={key:'push',domain:'notify',service:'phone',data:{message:'Alarm'}};
  panel._doc={schema:2,areas:[{key:'home',actions:[global]},{key:'system_sabotage',actions:[global,{key:'restricted'}]}]};
  assert.deepEqual(plain(panel._defaultV2('areas').actions),[global]);
});


test('HA service picker sorts names A-Z, searches IDs and escapes service labels',()=>{
  const {panel}=setup();panel._catalog.services={z:{z:{name:'Zebra'}},a:{b:{name:'Alpha'},c:{name:'<img src=x>'}}};
  panel._dialog={item:{domain:'a',service:'b'}};
  const entries=plain(panel._actionServiceEntries());
  assert.ok(entries.findIndex(e=>e.name==='Alpha')<entries.findIndex(e=>e.name==='Zebra'));
  assert.match(panel._actionServiceOptions('a.b'),/data-ha-service="a.b"/);
  assert.doesNotMatch(panel._actionServiceOptions('a.b'),/Zebra/);
  assert.match(panel._actionServiceOptions('a.c'),/&lt;img src=x&gt;/);
});

test('history fault observations use a warning instead of a successful outcome',()=>{
  const {panel}=setup();panel._doc={areas:[{key:'home',name:'Home'}],users:[],bindings:[]};panel._haUsers=[];panel._auditFilter={};panel._audit=[];
  panel._statuses=[{key:'home',events:[{at:1794139200,kind:'sensor_fault',info:'Door'}]}];
  const html=panel._sectionAudit();
  assert.match(html,/history-entry/);assert.match(html,/Warnung/);assert.match(html,/tone-warning/);
  assert.doesNotMatch(html,/Erfolgreich/);assert.doesNotMatch(html,/System · System/);
});

test('direct sabotage switch preserves contact configuration and rejects locked changes',async()=>{
  const {panel}=setup();const sabotage={entity_id:'binary_sensor.tamper',active_states:['on'],normal_states:['off']};
  panel._doc={sensors:[{key:'door',name:'Door',entities:[],memberships:[],sabotage}]};panel._safe=true;panel._canAdmin=true;
  let saved;panel._persistCandidate=async doc=>{saved=doc;};
  const input={dataset:{sabotageToggle:'0'},checked:false};await panel._toggleSabotage(input);
  assert.deepEqual(plain(saved.sensors[0].sabotage),{...sabotage,enabled:false});assert.equal(panel._doc.sensors[0].sabotage.enabled,undefined);
  saved=null;panel._safe=false;await panel._toggleSabotage(input);assert.equal(saved,null);assert.equal(input.checked,true);
});
