from check_ui import install_brand_assets
"""Browser regression for compact sensor layouts and isolated action simulation."""
from check_revision5_ui import Backend5,ROOT,NATIVE_STUBS
from playwright.sync_api import sync_playwright
import json,asyncio,importlib
from concurrent.futures import ThreadPoolExecutor
from test_workflows import FakeBackend
from test_sabotage import config
Simulation=importlib.import_module('alarm_core_tests.simulation').ActionSimulation


class Backend9(Backend5):
    def complete(self,coro):
        with ThreadPoolExecutor(max_workers=1) as pool:return pool.submit(asyncio.run,coro).result()
    def call(self,source,message):
        if message['type'].endswith('/runtime/action_test'):
            if message['operation']=='start':
                mode=next(m for m in json.loads(self.raw)['modes'] if m['key']==message['mode'])
                area=self.runtime.engine.areas[message['area']]
                action=config.parse_specs({'areas':[{'key':'test','name':'Test','modes':[{'key':mode['key'],'name':mode['name']}],'actions':[message['action']]}]})[0].actions[0]
                self.runtime.action_simulation=Simulation(action,FakeBackend(),area.spec.name,mode['key'],mode['name'],area.spec.zones)
                self.complete(self.runtime.action_simulation.start())
            else:
                test=self.runtime.action_simulation
                if message['token']!=test.token:raise ValueError('Ungültiger Test')
                self.complete(test.mute() if message['operation']=='mute' else test.stop())
            return {'simulation':self.runtime.action_simulation.view()}
        return super().call(source,message)


def run():
    backend=Backend9();raw=json.loads(backend.raw)
    raw['areas'][0]['actions']=[{'key':'push','name':'Testmeldung','domain':'notify','service':'mobile_app_phone','data':{'message':'{alarm_message} · {sensor} · {mode}'},'notification':{'platform':'android','buttons':True}}]
    raw['sensors'][0]['name']='Türkontakt mit längerem Anzeigenamen';backend.raw=json.dumps(raw);backend.rebuild()
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(**({'channel':'msedge'} if __import__('sys').platform=='win32' else {}),headless=True)
        page=browser.new_page(viewport={'width':1180,'height':1000},color_scheme='dark')
        page.route('http://alarm.test/**',lambda route:route.fulfill(status=200,body='<html></html>',content_type='text/html'));page.goto('http://alarm.test/ha-alarmzentrale')
        page.on('pageerror',lambda e:errors.append(str(e)));page.expose_binding('backend',backend.call)
        page.set_content('<style>body{--primary-text-color:#eee;--secondary-text-color:#aaa;--primary-background-color:#111;--secondary-background-color:#222;--card-background-color:#1c1c1c;--divider-color:#444;--primary-color:#03a9f4;--success-color:#4caf50;--error-color:#f44336;--warning-color:#ffb300;background:#111;color:#eee;font:14px Arial}</style><ha-alarmzentrale-panel></ha-alarmzentrale-panel>')
        attrs={'alarmzentrale_panel':True,'integration':'ha_alarmzentrale','modi':[{'key':m['key'],'name':m['name']} for m in raw['modes']]}
        page.evaluate("data=>{window.demoDevices=data.devices;window.hassMock={user:{id:'ha-admin',is_admin:true},states:data.states,callWS:m=>window.backend(m),connection:{subscribeMessage:async()=>()=>{}}};}",{'devices':backend.devices,'states':{**backend.states,'alarm_control_panel.zentrale':{'state':'disarmed','attributes':attrs}}})
        install_brand_assets(page)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/wachturm-icons.js'))
        page.add_script_tag(content=NATIVE_STUBS)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-card.js'));page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-panel.js'))
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').hass=window.hassMock")
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel')._doc&&!document.querySelector('ha-alarmzentrale-panel')._busy")
        root=page.locator('ha-alarmzentrale-panel')
        assert root.locator('h1').inner_text()=='Wachturm'
        assert root.locator('img.brand-logo').get_attribute('src').startswith('/ha_alarmzentrale_assets/wachturm-logo.png')
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel').shadowRoot.querySelector('img.brand-logo').naturalWidth>0")
        page.screenshot(path=str(ROOT/'qa/wachturm-header-dev12.png'),full_page=True)
        assert root.locator('.readiness-indicators').is_visible()
        root.locator('[data-detail-key=readiness] summary').click();assert not root.locator('.readiness-indicators').is_visible()
        root.get_by_role('button',name='Diagnose',exact=True).click()
        assert root.locator('#test-notify-action').count()==0
        assert 'Benachrichtigung separat testen' not in root.inner_text()
        assert page.evaluate("window.customCards.find(c=>c.type==='ha-alarmzentrale-card').name")=='Wachturm – Alarmsteuerung'
        launch=root.locator('.diagnostic-alarm-launch');assert launch.count()==1
        assert launch.evaluate('el=>el===el.parentElement.lastElementChild')
        root.get_by_role('button',name='Aktionen',exact=True).click()
        assert root.locator('.action-card').first.evaluate("el=>getComputedStyle(el).borderTopWidth")=='1px'
        assert root.locator('.action-card').last.evaluate("el=>getComputedStyle(el).borderBottomWidth")=='1px'
        root.locator('.action-card [data-action=edit]').first.click()
        assert root.locator('[data-field=action-mode-night]').is_checked()
        assert root.locator('[data-field=action-mode-away]').is_checked()
        root.locator('[data-field=action-mode-away]').uncheck()
        root.locator('[data-field=notification-tts]').check()
        root.locator('[data-field=notification-tts-text]').fill('Eigene Ansage: ')
        root.locator('[data-token-target=notification-tts-text][data-insert-token=sensor]').click()
        assert '{sensor}' in root.locator('[data-field=notification-tts-text]').input_value()
        assert root.locator('[data-field=notify-message]').input_value()=='{alarm_message} · {sensor} · {mode}'
        root.locator('[data-field=test-action-mode]').select_option('night');root.locator('[data-field=test-action-area]').select_option('home')
        root.locator('[data-action-test=start]').click()
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel')._runtime.simulation?.active")
        assert 'Test läuft' in root.locator('#action-test-status').inner_text()
        assert not any(a.alarm_latched for a in backend.runtime.engine.areas.values())
        assert backend.runtime.action_simulation.action.notification['tts_text'].startswith('Eigene Ansage:')
        token=backend.runtime.action_simulation.token
        root.locator('[data-action-test=mute]').click();page.wait_for_timeout(50)
        assert not backend.runtime.action_simulation.area.action_window_active
        root.locator('[data-action-test=stop]').click();page.wait_for_timeout(50)
        assert not backend.runtime.action_simulation.view()['active']
        page.screenshot(path=str(ROOT/'qa/action-test-dev9.png'),full_page=True)
        root.locator('[data-dialog-action=cancel]').click()
        # Inspect actual sabotage layout at desktop and phone sizes.
        page.evaluate("()=>{const p=document.querySelector('ha-alarmzentrale-panel');const i=p._doc.areas.findIndex(a=>a.key==='system_sabotage');p._dialog={kind:'areas',index:i,item:JSON.parse(JSON.stringify(p._doc.areas[i]))};p._render();}")
        for width in [1180,390]:
            page.set_viewport_size({'width':width,'height':900})
            assert root.locator('img.brand-logo').is_visible()
            row=root.locator('.sabotage-sensor-row').first
            title=row.locator('.item-title').bounding_box();statuses=row.locator('.entity-statuses').bounding_box()
            assert statuses['y']>=title['y']+title['height']
            assert row.evaluate('el=>el.scrollWidth<=el.clientWidth')
        page.screenshot(path=str(ROOT/'qa/sabotage-mobile-dev9.png'),full_page=True)
        root.locator('[data-dialog-action=cancel]').click()
        # Create a second test to verify its full-screen card never controls real state.
        backend.call(None,{'type':'ha_alarmzentrale/runtime/action_test','operation':'start','area':'home','mode':'night','action':raw['areas'][0]['actions'][0]})
        token=backend.runtime.action_simulation.token
        page.evaluate("token=>{history.pushState({},'', '/ha-alarmzentrale?alarm=1&az_simulation='+token);document.querySelector('ha-alarmzentrale-panel').route={};}",token)
        card=root.locator('ha-alarmzentrale-card');card.wait_for()
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel').shadowRoot.querySelector('ha-alarmzentrale-card')._simulating")
        assert 'TEST' in card.locator('h2').inner_text()
        assert card.locator('.alarm-symbol').get_attribute('icon')=='mdi:alarm-light-outline'
        timer=card.locator('[data-alarm-start]');assert timer.count()==1
        first=timer.inner_text();page.wait_for_timeout(1100);assert timer.inner_text()!=first
        assert card.locator('.contact-line.error').count()==1
        assert card.locator('.contact-line.ready').count()==1
        assert card.locator('.alarm-information').evaluate("el=>getComputedStyle(el).gridTemplateColumns.split(' ').length")==2
        assert card.locator('.contact-line.error').evaluate('el=>getComputedStyle(el).color')=='rgb(244, 67, 54)'
        assert card.locator('[data-operation=arm]').first.is_disabled()
        page.screenshot(path=str(ROOT/'qa/fullscreen-tiles-dev9.png'),full_page=True)
        card.locator('[data-operation=mute]').click();page.wait_for_timeout(100)
        assert card.locator('[data-alarm-start]').count()==1
        assert not backend.runtime.action_simulation.area.action_window_active
        assert not any(a.alarm_latched for a in backend.runtime.engine.areas.values())
        assert not errors,errors
        browser.close()
    print('Revision 9 UI passed: frames, readiness indicators, mode choices, draft tests, mobile sabotage layout, red tiles and isolated simulation card.')

if __name__=='__main__':run()
