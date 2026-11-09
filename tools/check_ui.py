"""Headless layout/workflow checks with real pure backend validation, no HA I/O."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_sabotage import config, fixture, editor, engine
from importlib import import_module

security = import_module("alarm_core_tests.security")
commands = import_module("alarm_core_tests.commands")
presentation = import_module("alarm_core_tests.presentation")


class Backend:
    def __init__(self):
        self.admin = True
        self.fail_next = False
        self.calls = []
        raw = fixture(True)
        raw["modes"][0]["name"] = "Nacht"
        raw["modes"][1]["name"] = "Abwesend"
        raw["sensors"][0].update(device_id="door-device", sensor_type="door")
        raw["sensors"][0]["connection"] = {"method": "disabled"}
        raw["sensors"].append({"key": "kitchen", "name": "Fenster Küche", "device_id": "window-device", "sensor_type": "window",
                               "entities": [{"key": "opening", "entity_id": "binary_sensor.window", "active_states": ["on"], "normal_states": ["off"]}],
                               "memberships": [{"area": "home", "entity_keys": ["opening"]}]})
        raw["roles"] = [{"key": "resident", "name": "Bewohner", "grants": [
            {"area": key, "operations": ["arm", "disarm", "mute", "acknowledge"]} for key in ("home", "system_sabotage")]}]
        raw["users"] = [{"key": "max", "name": "Max", "enabled": True, "ha_user_ids": ["ha-admin"], "roles": ["resident"], "grants": []}]
        raw["bindings"] = []
        self.raw = json.dumps(raw)
        self.states = {"binary_sensor.door": {"state": "off", "attributes": {"friendly_name": "Haustür", "device_class": "door"}},
                       "binary_sensor.tamper": {"state": "off", "attributes": {"friendly_name": "Sabotage", "device_class": "tamper"}},
                       "binary_sensor.window": {"state": "off", "attributes": {"friendly_name": "Fenster Küche", "device_class": "window"}},
                       "binary_sensor.battery": {"state": "off", "attributes": {"friendly_name": "Batterie", "device_class": "battery"}},
                       "sensor.rssi": {"state": "-67", "attributes": {"friendly_name": "RSSI Gerät", "unit_of_measurement": "dBm"}}}
        self.devices = [{"id": "door-device", "name": "Haustür-Magnetkontakt", "manufacturer": "eQ-3", "model": "HM-Sec-SCo"},
                        {"id": "window-device", "name": "Fenster Küche", "manufacturer": "eQ-3", "model": "HmIP-SWDO"}]
        self.entities = [{"entity_id": key, "device_id": "window-device" if "window" in key else "door-device", "platform": "homematicip_local"}
                         for key in self.states]
        self.rebuild()

    def rebuild(self):
        specs, access = config.parse_configuration(json.loads(self.raw))
        machine = engine.AlarmEngine(specs)
        for key, value in self.states.items():
            machine.update_sensor(key, value["state"], attributes=value["attributes"], initial=True)
        machine.finish_recovery()
        auth = security.Authorization(access)
        self.runtime = SimpleNamespace(engine=machine, authorization=auth,
                                       actions=SimpleNamespace(in_flight=set(), queues={}))
        self.runtime.commands = commands.AlarmCommands(machine, auth)

    def view(self):
        data = presentation.runtime_view(self.runtime, "ha-admin", administrator=self.admin)
        if self.admin:
            data.update(diagnostics={"enabled": False}, audit=self.runtime.commands.audit.query(limit=100))
        return data

    def call(self, source, message):
        kind = message["type"]
        self.calls.append(kind)
        if kind.endswith("/editor/get"):
            if not self.admin:
                raise ValueError("Administrator required")
            data = editor.visible_document(self.raw)
            data.update(self.view(), ha_users=[{"id": "ha-admin", "name": "Max", "active": True}, {"id": "ha-anna", "name": "Anna", "active": True}])
            return data
        if kind.endswith("/runtime/get"):
            return self.view()
        if kind.endswith("/editor/save"):
            if self.fail_next:
                self.fail_next = False
                raise ValueError("Absichtlich verweigert: Eingaben müssen erhalten bleiben")
            self.raw = editor.prepare_configuration(self.raw, message["configuration"], message["revision"])
            self.rebuild()
            return {"saved": True}
        if kind.endswith("/runtime/command"):
            if message.get("area"):
                self.runtime.commands.from_ha_user("ha-admin", message["area"], message["operation"], message.get("mode"))
            elif message.get("mode"):
                self.runtime.commands.mode_from_ha_user("ha-admin", message["mode"], message["operation"])
            else:
                self.runtime.commands.disarm_all_from_ha_user("ha-admin")
            return self.view()
        if kind == "config/device_registry/list":
            return self.devices
        if kind == "config/entity_registry/list":
            return self.entities
        if kind == "get_services":
            return {"notify": {"mobile_app_telefon": {"name": "Telefon"}}, "light": {"turn_on": {}}, "siren": {"turn_on": {}}, "script": {"turn_on": {}}}
        if kind.endswith("/editor/audit"):
            return {"events": self.runtime.commands.audit.query(limit=250)}
        raise ValueError(f"Unexpected call: {kind}")


def install_brand_assets(page):
    page.evaluate("()=>{if(!document.querySelector('base')){const b=document.createElement('base');b.href='http://alarm.test/';document.head.append(b);}}")
    page.route('**/ha_alarmzentrale_assets/wachturm-logo.png*',lambda route:route.fulfill(status=200,body=(ROOT/'custom_components/ha_alarmzentrale/frontend/wachturm-logo.png').read_bytes(),content_type='image/png'))


NATIVE_STUBS = """
class HaIcon extends HTMLElement {
 static get observedAttributes(){return ['icon'];}
 connectedCallback(){this.draw();}attributeChangedCallback(){if(this.isConnected)this.draw();}
 async draw(){const icon=this.getAttribute('icon')||'';if(icon.startsWith('wachturm:')&&window.customIconsets?.wachturm){const item=await window.customIconsets.wachturm(icon.split(':')[1]);if(this.getAttribute('icon')!==icon)return;this.style.display='inline-flex';this.style.width='var(--mdc-icon-size,24px)';this.style.height='var(--mdc-icon-size,24px)';const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',item.viewBox);svg.style.width=svg.style.height='100%';const p=document.createElementNS(svg.namespaceURI,'path');p.setAttribute('fill','currentColor');p.setAttribute('d',item.path);svg.append(p);this.replaceChildren(svg);}else{this.textContent='◇';this.style.display='inline-block';this.style.width='1.2em';}}
}
customElements.define('ha-icon',HaIcon);
class HaSelector extends HTMLElement {
 constructor(){super();this.attachShadow({mode:'open'});this._value=undefined;this._selector={entity:{}};}
 set hass(v){this._hass=v;this.draw();} set selector(v){this._selector=v;this.draw();}
 set value(v){this._value=v;this.draw();} get value(){return this._value;}
 connectedCallback(){this.draw();}
 draw(){if(!this.isConnected||!this._hass)return;const device=!!this._selector.device;const multi=!!this._selector.entity?.multiple;
 let rows=this._selector.icon?['mdi:shield-lock-outline','mdi:home-account','mdi:weather-night','mdi:home-outline','mdi:shield-alert-outline'].map(id=>[id,id]):device?window.demoDevices.map(d=>[d.id,d.name]):Object.entries(this._hass.states).map(([id,s])=>[id,s.attributes.friendly_name||id]);
 const included=this._selector.entity?.include_entities;if(included)rows=rows.filter(([id])=>included.includes(id));
 this.shadowRoot.innerHTML='<style>:host{display:block}select{width:100%;min-height:56px;background:var(--secondary-background-color);color:var(--primary-text-color);font:inherit;border:1px solid var(--divider-color);border-radius:8px;padding:12px}</style>';
 const select=document.createElement('select');select.multiple=multi;select.setAttribute('aria-label',this.label||'Auswahl');
 if(!multi){const p=document.createElement('option');p.value='';p.textContent=device?'Gerät auswählen':'Entität auswählen';select.append(p);}
 for(const [id,name] of rows){const option=document.createElement('option');option.value=id;option.textContent=name;option.selected=multi?(this._value||[]).includes(id):this._value===id;select.append(option);}
 select.addEventListener('change',()=>{this._value=multi?[...select.selectedOptions].map(o=>o.value):select.value;this.dispatchEvent(new CustomEvent('value-changed',{detail:{value:this._value},bubbles:true,composed:true}));});this.shadowRoot.append(select);
 }
}
customElements.define('ha-selector',HaSelector);
"""


def run():
    backend = Backend()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**({'channel':'msedge'} if __import__('sys').platform=='win32' else {}), headless=True)
        page = browser.new_page(viewport={"width": 1180, "height": 1000}, color_scheme="dark")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.expose_binding("backend", backend.call)
        page.set_content('<style>body{margin:0;--primary-text-color:#e8edf2;--secondary-text-color:#a1acb7;--primary-background-color:#101419;--secondary-background-color:#202830;--card-background-color:#181e25;--divider-color:#34404c;--primary-color:#15b5cc;--text-primary-color:#06191c;--success-color:#5bce9a;--error-color:#ff7979;--warning-color:#edb65a;background:#101419}</style><ha-alarmzentrale-panel></ha-alarmzentrale-panel>')
        page.evaluate("data=>{window.demoDevices=data.devices;window.hassMock={user:{id:'ha-admin',is_admin:true},states:data.states,callWS:message=>window.backend(message),connection:{subscribeMessage:async()=>()=>{}}};}", {"devices": backend.devices, "states": backend.states})
        install_brand_assets(page)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/wachturm-icons.js'))
        page.add_script_tag(content=NATIVE_STUBS)
        page.add_script_tag(path=str(ROOT / "custom_components/ha_alarmzentrale/frontend/alarmzentrale-panel.js"))
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').hass=window.hassMock")
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel')._doc && !document.querySelector('ha-alarmzentrale-panel')._busy")
        root = page.locator("ha-alarmzentrale-panel")
        assert root.get_by_role("button", name="Konfiguration speichern", exact=True).count() == 0
        page.screenshot(path=str(ROOT / "qa/overview-desktop.png"), full_page=True)
        for label in ("Bereiche", "Modi", "Aktionen", "Diagnose", "Benutzer", "Rollen", "Bediengeräte", "Bedienhistorie"):
            root.get_by_role("button", name=label, exact=True).click()
            page.wait_for_timeout(80)
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"), label
        root.get_by_role("button", name="Modi", exact=True).click()
        root.locator('[data-action="edit"]').first.click()
        page.screenshot(path=str(ROOT / "qa/mode-desktop.png"), full_page=True)
        assert not root.locator('.modal details').first.evaluate("el=>el.open")
        root.get_by_text("Wohnung", exact=True).last.click()
        page.screenshot(path=str(ROOT / "qa/mode-expanded-desktop.png"), full_page=True)
        fields = root.locator('.sensor-mode-settings .field').all()
        assert abs(fields[0].bounding_box()["y"] - fields[1].bounding_box()["y"]) < 45
        root.locator('[data-field="mode-rule-home-0-0"]').select_option("instant")
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        saved = json.loads(backend.raw)
        rules = saved["sensors"][0]["memberships"][0]["entity_rules"]["opening"]["mode_rules"]
        assert rules["night"]["behavior"] == "instant"
        assert rules["away"]["behavior"] == "standby"
        root.get_by_role("button", name="Bereiche", exact=True).click()
        root.locator('[data-action="add"]').click()
        root.locator('[data-field="name"]').fill("Eingang")
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        assert any(a["name"] == "Eingang" for a in json.loads(backend.raw)["areas"])
        assert root.get_by_role("button", name="Sensoren", exact=True).count() == 0
        entrance = next(a['key'] for a in json.loads(backend.raw)['areas'] if a['name']=='Eingang')
        group = root.locator('[data-area-key="'+entrance+'"]')
        group.locator('summary').first.click()
        group.locator('[data-action="sensor-add"]').click()
        assert root.locator('[data-field="sensor-area"]').input_value() == entrance
        root.locator('[data-field="name"]').fill('Zusatzkontakt')
        root.locator('#sensor-native-picker select').select_option(['binary_sensor.battery'])
        root.get_by_role('button', name='Speichern', exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        created = next(s for s in json.loads(backend.raw)['sensors'] if s['name']=='Zusatzkontakt')
        assert [m['area'] for m in created['memberships']] == [entrance]
        group.locator('[data-action="sensor-edit"]').click()
        root.locator('[data-field="sensor-area"]').select_option('')
        root.get_by_role('button', name='Speichern', exact=True).click()
        assert 'genau einen Bereich' in root.locator('#form-error').inner_text()
        assert next(s for s in json.loads(backend.raw)['sensors'] if s['key']==created['key'])['memberships'][0]['area']==entrance
        root.locator('[data-field="sensor-area"]').select_option('home')
        root.get_by_role('button', name='Speichern', exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert root.locator('#form-error').count()==0 or root.locator('#form-error').is_hidden(),root.locator('#form-error').inner_text()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        moved = next(s for s in json.loads(backend.raw)['sensors'] if s['key']==created['key'])
        assert [m['area'] for m in moved['memberships']] == ['home']
        assert group.locator('[data-sensor-key="'+created['key']+'"]').count() == 0

        root.get_by_role("button", name="Modi", exact=True).click()
        root.locator('[data-action="edit"]').first.click()
        root.locator('[data-field="name"]').fill("Eingabe bleibt erhalten")
        backend.fail_next = True
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert root.locator('[data-field="name"]').input_value() == "Eingabe bleibt erhalten"
        assert root.locator('#form-error').is_visible()
        assert json.loads(backend.raw)["modes"][0]["name"] == "Nacht"
        root.get_by_role("button", name="Abbrechen", exact=True).click()
        root.get_by_role("button", name="Bereiche", exact=True).click()
        root.locator('[data-area-key="home"]').evaluate('(el)=>el.open=true')
        page.screenshot(path=str(ROOT / "qa/sensors-desktop.png"), full_page=True)
        root.locator('[data-area-key="home"] [data-action="sensor-edit"]').first.click()
        root.get_by_text('Verbindung und Funkdiagnose (optional)', exact=True).click()
        assert not root.locator('#connection-rssi-sources').is_visible()
        assert not root.locator('#connection-signal-settings').is_visible()
        page.screenshot(path=str(ROOT / "qa/sensor-dialog.png"), full_page=True)
        root.get_by_role("button", name="Abbrechen", exact=True).click()
        # Custom HA actions expose a service chooser and persist its real service.
        root.get_by_role("button", name="Aktionen", exact=True).click()
        root.locator('[data-action="add"]').click()
        assert root.locator('[data-field^="action-area-"]').count() == 0
        assert root.locator('[data-field="test-action-area"]').count() == 0
        root.locator('[data-field="name"]').fill('Eigenes Skript')
        root.locator('#action-service-picker > summary').click()
        search = root.locator('#action-service-search')
        assert root.locator('#action-service-options').evaluate("el=>{const names=[...el.querySelectorAll('strong')].map(x=>x.textContent);return names.every((x,i)=>!i||names[i-1].localeCompare(x,'de',{sensitivity:'base',numeric:true})<=0)}")
        search.fill('script.turn_on')
        page.evaluate("document.querySelector('ha-alarmzentrale-panel')._pollRuntime()")
        assert search.input_value() == 'script.turn_on'
        assert root.locator('#action-service-picker').evaluate('(el)=>el.open')
        page.screenshot(path=str(ROOT/'qa/action-service-search-0.8.5.png'),full_page=True)
        search.press('Enter')
        assert root.locator('[data-field="action-kind"]').input_value() == 'script'
        root.locator('#action-advanced > summary').click()
        assert root.locator('[data-field="domain"]').input_value() == 'script'
        assert root.locator('[data-field="service"]').input_value() == 'turn_on'
        root.locator('#action-targets summary').click()
        root.locator('[data-field="manual-target"]').fill('script.alarm')
        root.locator('[data-dialog-action="add-manual-target"]').click()
        root.locator('[data-field="data"]').fill('{"variables":{"message":"Alarm"}}')
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        custom = json.loads(backend.raw)
        assert all(any(a['name']=='Eigenes Skript' and a['domain']=='script' and a['service']=='turn_on' and a['entity_ids']==['script.alarm'] for a in area['actions']) for area in custom['areas'])
        row=root.locator('.action-card').filter(has_text='Eigenes Skript')
        toggle=row.locator('[data-action-toggle]')
        assert 'Noch nicht ausgeführt' in row.inner_text()
        toggle.uncheck()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert all(next(a for a in area['actions'] if a['name']=='Eigenes Skript')['enabled'] is False for area in json.loads(backend.raw)['areas'])
        toggle.check()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        home=backend.runtime.engine.areas['home']
        key=next(a.key for a in home.spec.actions if a.name=='Eigenes Skript')
        home.record_action_result(key,success=True,info='start')
        page.evaluate("document.querySelector('ha-alarmzentrale-panel')._pollRuntime()")
        assert 'Zuletzt ausgeführt' in row.inner_text()
        root.locator('[data-action="add"]').click()
        root.locator('[data-field="name"]').fill('Licht nach Aktivierung')
        root.locator('[data-field="action-when"]').select_option('armed')
        root.locator('[data-field="action-timing"]').select_option('after')
        assert root.locator('#action-repeat-option').is_hidden()
        root.locator('[data-field="timed"]').check()
        assert root.locator('[data-field="action-stop-clock"]').input_value()=='own'
        assert root.locator('#action-own-duration').is_visible()
        root.locator('[data-duration="action-duration"] [data-duration-sec]').fill('20')
        root.locator('#action-targets summary').click()
        root.locator('[data-field="manual-target"]').fill('light.test')
        root.locator('[data-dialog-action="add-manual-target"]').click()
        root.get_by_role('button',name='Speichern',exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        saved_output=next(a for a in json.loads(backend.raw)['areas'][0]['actions'] if a['name']=='Licht nach Aktivierung')
        assert saved_output['timed'] and saved_output['duration_seconds']==20
        # Save a notification through the friendly action editor.
        root.get_by_role("button", name="Aktionen", exact=True).click()
        root.locator('[data-action="add"]').click()
        root.locator('[data-field="name"]').fill("Batteriewarnung Telefon")
        root.locator('#action-service-picker > summary').click()
        root.locator('#action-service-search').fill('notify.mobile_app_telefon')
        root.locator('[data-ha-service="notify.mobile_app_telefon"]').click()
        when = root.locator('[data-field="action-when"]')
        timing = root.locator('[data-field="action-timing"]')
        for operation in ['mute','acknowledge']:
            when.select_option(operation)
            assert timing.input_value()=='after'
            assert root.locator('#action-repeat-option').is_hidden()
            assert root.locator('#action-stop-option').is_hidden()
        when.select_option("armed")
        for value in ["before", "during", "after"]:
            timing.select_option(value)
            assert timing.input_value() == value
        timing.select_option("before")
        when.select_option("unstable")
        assert timing.input_value() == "during"
        assert timing.locator('option[value="before"]').evaluate('(el)=>el.disabled'), timing.evaluate('(el)=>el.outerHTML')
        timing.select_option("after")
        assert "Erholungszeit" in root.locator('#action-timing-hint').inner_text()
        when.select_option("disarmed")
        assert timing.input_value() == "after"
        assert timing.locator('option[value="during"]').evaluate('(el)=>el.disabled')
        when.select_option("battery")
        timing.select_option("after")
        assert root.locator('[data-field="notification-platform"]').is_disabled()
        page.screenshot(path=str(ROOT / "qa/action-timing.png"), full_page=True)
        root.locator('[data-field="notify-service"]').select_option("mobile_app_telefon")
        root.locator('[data-field="notify-message"]').fill("Bitte Batterie prüfen")
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        action = next(a for a in json.loads(backend.raw)["areas"][0]["actions"] if a["name"]=="Batteriewarnung Telefon")
        assert action["when"] == "battery" and action["domain"] == "notify" and action["timing"] == "after"
        # A role template writes explicit permissions; a HA user can be activated.
        root.get_by_role("button", name="Rollen", exact=True).click()
        assert root.get_by_text("Admin", exact=True).is_visible()
        assert root.get_by_text("Standardrolle · Alle Bedienrechte", exact=True).is_visible()
        root.locator('[data-action="add"]').click()
        root.locator('[data-field="role-preset"]').select_option("guest")
        root.locator('[data-field="name"]').fill("Besucher")
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        role = json.loads(backend.raw)["roles"][-1]
        assert all(g["operations"] == ["arm", "disarm"] for g in role["grants"])
        root.get_by_role("button", name="Benutzer", exact=True).click()
        root.locator('[data-action="ha-setup"][data-ha-user="ha-anna"]').click()
        root.locator('[data-field="role:' + role["key"] + '"]').check()
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        assert json.loads(backend.raw)["users"][-1]["ha_user_ids"] == ["ha-anna"]
        # Configure a mode-scoped Homematic event without executing hardware.
        root.get_by_role("button", name="Bediengeräte", exact=True).click()
        root.locator('[data-action="add"]').click()
        root.locator('[data-field="name"]').fill("Fernbedienung Nacht")
        root.locator('[data-native-device="device_id"] select').select_option("door-device")
        root.locator('[data-field="user"]').select_option("max")
        root.locator('[data-field="mode"]').select_option("night")
        root.get_by_text("Ereignis und Zuordnung prüfen", exact=True).click()
        root.locator('[data-field="address"]').fill("REMOTE")
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        assert json.loads(backend.raw)["bindings"][-1]["scope"] == "mode"
        # Graphical radio-source setup also persists without a global save.
        root.get_by_role("button", name="Diagnose", exact=True).click()
        root.get_by_text("Überwachung und Funkquellen einstellen", exact=True).click()
        root.locator('#add-radio').click()
        root.locator('[data-native-source="radio-duty-0"] select').select_option("sensor.rssi")
        root.locator('[data-field="radio-name-0"]').fill("Test-Funkmodul")
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert len(json.loads(backend.raw)["diagnostics"]["radio_interfaces"]) == 1
        page.set_viewport_size({"width": 390, "height": 844})
        for label in ("Übersicht", "Bereiche", "Modi", "Aktionen", "Diagnose", "Benutzer", "Rollen", "Bediengeräte", "Bedienhistorie"):
            root.get_by_role("button", name=label, exact=True).click()
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"), "Mobile " + label
        root.get_by_role("button", name="Bereiche", exact=True).click()
        root.locator('[data-area-key="home"]').evaluate('(el)=>el.open=true')
        page.screenshot(path=str(ROOT / "qa/sensors-mobile.png"), full_page=True)
        root.get_by_role("button", name="Übersicht", exact=True).click()
        root.locator('[data-runtime-mode="night"]').click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert all(area.mode == "night" for area in backend.runtime.engine.areas.values())
        root.locator('[data-runtime-operation="disarm"]:not([data-runtime-area])').click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert all(area.mode is None for area in backend.runtime.engine.areas.values())
        assert root.locator('#success-notice').is_visible()
        page.wait_for_timeout(5200)
        assert root.locator('#success-notice').count() == 0
        assert root.locator('[data-runtime-operation="disarm"]:not([data-runtime-area])').is_disabled()
        root.get_by_role("button", name="Bedienhistorie", exact=True).click()
        root.locator('[data-action="audit-refresh"]').click()
        page.wait_for_timeout(150)
        groups = root.locator('.history-group')
        assert groups.count() >= 2
        assert root.locator('.history-entry').first.evaluate("el=>getComputedStyle(el).borderTopWidth")=='1px'
        assert root.locator('.history-group').first.evaluate("el=>getComputedStyle(el).borderTopWidth")=='1px'
        assert not groups.first.evaluate("el=>el.open")
        groups.first.locator('summary').click()
        assert groups.first.locator('.list-item').count() >= 4
        page.screenshot(path=str(ROOT / "qa/history-mobile.png"), full_page=True)
        page.set_viewport_size({"width":1180,"height":1000})
        page.screenshot(path=str(ROOT / "qa/history-desktop.png"), full_page=True)
        root.get_by_role("button", name="Bereiche", exact=True).click()
        root.locator('[data-area-key="system_sabotage"]').evaluate('(el)=>el.open=true')
        switch = root.locator('[data-sabotage-toggle="0"]')
        assert switch.is_checked()
        before = json.loads(backend.raw)['sensors'][0]['sabotage'].copy()
        backend.fail_next = True
        switch.uncheck()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert switch.is_checked()
        assert json.loads(backend.raw)['sensors'][0]['sabotage'] == before
        switch.uncheck()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert json.loads(backend.raw)["sensors"][0]["sabotage"]["enabled"] is False
        root.locator('[data-area-key="system_sabotage"] [data-action="edit"]').click()
        assert root.get_by_text('Zugeordnete Sensoren',exact=True).count()==0
        assert root.locator('[data-field^="area-sabotage-"]').count()==0
        root.get_by_role('button',name='Speichern',exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        assert json.loads(backend.raw)['sensors'][0]['sabotage']['enabled'] is False
        root.get_by_role("button", name="Bereiche", exact=True).click()
        root.locator('[data-area-key="home"]').evaluate('(el)=>el.open=true')
        root.locator('[data-area-key="home"] [data-action="sensor-edit"]').first.click()
        assert root.locator('[data-field="sabotage-enabled"]').count() == 0
        root.get_by_role("button", name="Speichern", exact=True).click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog && !document.querySelector('ha-alarmzentrale-panel')._busy")
        assert json.loads(backend.raw)["sensors"][0]["sabotage"]["enabled"] is False
        assert not errors, errors
        backend.admin = False
        page.evaluate("() => {const panel=document.querySelector('ha-alarmzentrale-panel');window.hassMock.user.is_admin=false;panel.hass=window.hassMock;panel._loadStarted=false;panel._busy=false;return panel._load();}")
        assert root.locator('[data-tab]').count() == 1
        assert root.get_by_role("button", name="Rollen", exact=True).count() == 0
        assert root.locator('[data-runtime-operation="disarm"]').count() > 0
        browser.close()
    print("Browser checks passed: 9 tabs desktop/mobile, direct save, backend validation, failed-save retention, disabled diagnostics, authorized shared mode, role/user/action/radio/device saves, non-admin view.")


if __name__ == "__main__":
    run()
