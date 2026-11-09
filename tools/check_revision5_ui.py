from check_ui import install_brand_assets
"""Central card/PIN and revised UI workflows against pure alarm commands."""
from check_ui import Backend,ROOT,NATIVE_STUBS,presentation,security
from playwright.sync_api import sync_playwright
import json
import time
import importlib
control=importlib.import_module("alarm_core_tests.control")

class Backend5(Backend):
    def __init__(self):
        super().__init__()
        raw=json.loads(self.raw);raw['users'][0]['panel_pin_hash']=security.make_pin_hash('1234');raw['operation_pin']={'arm':True,'disarm':True,'mute':False,'acknowledge':False}
        self.raw=json.dumps(raw);self.rebuild();self.command_count=0
    def rebuild(self):
        super().rebuild();self.runtime.configuration=json.loads(self.raw);self.runtime.sensor_test=None
        for area in self.runtime.engine.areas.values():area.subscribe(lambda event,area=area:self.runtime.sensor_test.observe(area,event) if self.runtime.sensor_test else None)
    def call(self,source,message):
        kind=message['type']
        if kind.endswith('/runtime/command'):
            self.command_count+=1;op=message['operation'];policy=control.pin_policy(self.runtime.configuration,self.runtime.authorization)
            user=self.runtime.authorization.identify_panel_pin(message.get('pin')) if policy[op] else self.runtime.authorization.spec.users[0]
            self.runtime.commands.global_for_user(user,op,message.get('mode'),'panel_pin' if policy[op] else 'ha_account')
            return self.view()
        if kind.endswith('/runtime/test'):
            if message['operation']=='sensor_start':self.runtime.sensor_test=control.SensorTest();self.runtime.commands.test_until=self.runtime.sensor_test.until
            elif message['operation']=='sensor_stop':self.runtime.sensor_test=None;self.runtime.commands.test_until=0
            return {'test_session':self.runtime.sensor_test.view() if self.runtime.sensor_test else {'active':False}}
        if kind.endswith('/editor/backup'):return {'configuration':json.loads(self.raw)}
        return super().call(source,message)

def run():
    backend=Backend5();errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(**({'channel':'msedge'} if __import__('sys').platform=='win32' else {}),headless=True)
        page=browser.new_page(viewport={'width':1180,'height':1000},color_scheme='dark',accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.expose_binding('backend',backend.call)
        page.set_content('<style>body{--primary-text-color:#e8edf2;--secondary-text-color:#a1acb7;--primary-background-color:#101419;--secondary-background-color:#202830;--card-background-color:#181e25;--divider-color:#34404c;--primary-color:#15b5cc;--text-primary-color:#06191c;--success-color:#5bce9a;--error-color:#ff7979;--warning-color:#edb65a;background:#101419;color:#e8edf2;font:14px Arial}#card{max-width:460px;margin:20px auto}</style><div id="card"></div>')
        attrs={'alarmzentrale_panel':True,'integration':'ha_alarmzentrale','modi':[{'key':m['key'],'name':m['name'],'icon':m.get('icon')} for m in json.loads(backend.raw)['modes']]}
        data={'devices':backend.devices,'states':{**backend.states,'alarm_control_panel.zentrale':{'state':'disarmed','attributes':attrs},'alarm_control_panel.foreign':{'state':'disarmed','attributes':{'friendly_name':'Fremdes Panel'}},'alarm_control_panel.fake':{'state':'disarmed','attributes':{'alarmzentrale_panel':True,'integration':'other'}}}}
        page.evaluate("data=>{window.demoDevices=data.devices;window.hassMock={user:{id:'ha-admin',is_admin:true},states:data.states,callWS:msg=>window.backend(msg),connection:{subscribeMessage:async()=>()=>{}}};}",data)
        install_brand_assets(page)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/wachturm-icons.js'))
        page.add_script_tag(content=NATIVE_STUBS)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-card.js'))
        page.evaluate("()=>{const editor=document.createElement('ha-alarmzentrale-card-editor');editor.hass=window.hassMock;document.querySelector('#card').append(editor);}")
        assert page.locator('ha-alarmzentrale-card-editor option').count()==2
        page.evaluate("()=>{document.querySelector('#card').innerHTML='';const card=document.createElement('ha-alarmzentrale-card');card.setConfig({entity:'alarm_control_panel.zentrale'});card.hass=window.hassMock;document.querySelector('#card').append(card);}")
        card=page.locator('ha-alarmzentrale-card')
        page.wait_for_function("document.querySelector('ha-alarmzentrale-card')._view")
        card.locator('[data-mode="night"]').click()
        pin=page.locator('ha-alarmzentrale-pin');assert pin.is_visible()
        page.screenshot(path=str(ROOT/'qa/card-pin-dev5.png'),full_page=True)
        for digit in '9999':pin.locator('[data-key="'+digit+'"]').click()
        pin.locator('[data-key="ok"]').click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-card')._busy")
        assert all(a.mode is None for a in backend.runtime.engine.areas.values())
        page.wait_for_function("document.querySelector('ha-alarmzentrale-card')._error")
        assert 'PIN ist nicht korrekt.' in card.locator('.error').inner_text()
        card.locator('[data-mode="night"]').click()
        page.evaluate("window.pinInput=document.querySelector('ha-alarmzentrale-pin').shadowRoot.querySelector('input')")
        for digit in '1234':pin.locator('[data-key="'+digit+'"]').click()
        pin.locator('[data-key="ok"]').click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-card')._busy")
        assert all(a.mode=='night' for a in backend.runtime.engine.areas.values())
        assert page.evaluate("window.pinInput.value")==''
        backend.runtime.engine.update_sensor('binary_sensor.door','on')
        page.evaluate("document.querySelector('ha-alarmzentrale-card').load()")
        page.wait_for_function("document.querySelector('ha-alarmzentrale-card')._view.summary.alarm")
        assert card.locator('.source').count()==1
        page.screenshot(path=str(ROOT/'qa/card-alarm-dev5.png'),full_page=True)
        backend.runtime.engine.update_sensor('binary_sensor.door','off')
        page.evaluate("document.querySelector('ha-alarmzentrale-card').load()")
        page.wait_for_timeout(80)
        assert 'Wieder geschlossen' in card.locator('.source').inner_text()
        count=backend.command_count;card.locator('[data-operation="disarm"]').click();pin.locator('[data-key="cancel"]').click()
        assert backend.command_count==count
        card.locator('[data-operation="disarm"]').click()
        for digit in '1234':pin.locator('[data-key="'+digit+'"]').click()
        pin.locator('[data-key="ok"]').click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-card')._busy")
        assert all(a.mode is None for a in backend.runtime.engine.areas.values())
        page.screenshot(path=str(ROOT/'qa/card-dev5.png'),full_page=True)
        page.evaluate("()=>{document.querySelector('#card').remove();document.body.insertAdjacentHTML('beforeend','<ha-alarmzentrale-panel></ha-alarmzentrale-panel>');}")
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-panel.js'))
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').hass=window.hassMock")
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel')._doc&&!document.querySelector('ha-alarmzentrale-panel')._busy")
        root=page.locator('ha-alarmzentrale-panel')
        assert not root.locator('.area-details').evaluate('el=>el.open')
        y=root.locator('.tabs').bounding_box()['y']
        page.evaluate("()=>{const p=document.querySelector('ha-alarmzentrale-panel');p._notice='Gespeichert';p._render();}")
        assert abs(root.locator('.tabs').bounding_box()['y']-y)<1
        page.wait_for_timeout(5200)
        assert abs(root.locator('.tabs').bounding_box()['y']-y)<1
        root.get_by_role('button',name='Diagnose',exact=True).click()
        root.get_by_text('Geführte Funktionstests',exact=True).click()
        root.locator('[data-test="sensor_start"]').click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert backend.runtime.commands.test_active()
        backend.runtime.engine.update_sensor('binary_sensor.door','on');backend.runtime.engine.update_sensor('binary_sensor.door','off')
        page.evaluate("document.querySelector('ha-alarmzentrale-panel')._pollRuntime()")
        page.wait_for_timeout(100)
        assert 'Auslösung und Normalisierung erkannt' in root.locator('#sensor-test-results').inner_text()
        root.locator('[data-test="sensor_stop"]').click()
        page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert not backend.runtime.commands.test_active()
        root.get_by_role('button',name='Benutzer',exact=True).click()
        with page.expect_download() as download:root.locator('[data-backup]').click()
        assert download.value.suggested_filename.endswith('.json')
        root.get_by_role('button',name='Diagnose',exact=True).click()
        page.evaluate("()=>{const p=document.querySelector('ha-alarmzentrale-panel');p._diagnostics={enabled:true,radios:[{id:'occu',latest:{duty_cycle:35,carrier_sense:2},history:[{at:'2026-11-08T10:00:00Z',duty_cycle:25,carrier_sense:1},{at:'2026-11-08T10:01:00Z',duty_cycle:35,carrier_sense:2}]}]};p._render();}")
        graph=root.locator('[data-graph]').first
        graph.focus();graph.press('ArrowLeft')
        assert '25 %' in graph.locator('.graph-value').inner_text()
        page.screenshot(path=str(ROOT/'qa/graphs-dev5.png'),full_page=True)
        root.get_by_role('button',name='Übersicht',exact=True).click()
        page.screenshot(path=str(ROOT/'qa/overview-dev5.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(ROOT/'qa/overview-dev5-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        browser.close()
    print('Revision 5 UI passed: central-only filter, PIN success/error/cancel, wiped code, fixed notice slot, test session, backup and responsive overview.')

if __name__=='__main__':run()
