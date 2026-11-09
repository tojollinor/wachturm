from check_ui import install_brand_assets
"""Real browser checks for mobile controls, full-screen alarm and notification options."""
from check_revision5_ui import Backend5,ROOT,NATIVE_STUBS
from playwright.sync_api import sync_playwright
import json

def run():
    backend=Backend5();errors=[]
    raw=json.loads(backend.raw);raw['areas'][0]['actions']=[{'key':'push','name':'Testmeldung','domain':'notify','service':'mobile_app_phone','data':{'message':'{alarm_message}'}}];backend.raw=json.dumps(raw);backend.rebuild()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(**({'channel':'msedge'} if __import__('sys').platform=='win32' else {}),headless=True)
        page=browser.new_page(viewport={'width':1180,'height':1000},color_scheme='dark')
        page.route('http://alarm.test/**',lambda route:route.fulfill(status=200,body='<html></html>',content_type='text/html'))
        page.goto('http://alarm.test/ha-alarmzentrale')
        page.on('pageerror',lambda e:errors.append(str(e)));page.expose_binding('backend',backend.call)
        page.set_content('<style>body{--primary-text-color:#eee;--secondary-text-color:#aaa;--primary-background-color:#111;--secondary-background-color:#222;--card-background-color:#1c1c1c;--divider-color:#444;--primary-color:#03a9f4;--success-color:#4caf50;--error-color:#f44336;--warning-color:#ffb300;background:#111;color:#eee;font:14px Arial}</style><ha-alarmzentrale-panel></ha-alarmzentrale-panel>')
        attrs={'alarmzentrale_panel':True,'integration':'ha_alarmzentrale','modi':[{'key':m['key'],'name':m['name'],'icon':m.get('icon')} for m in json.loads(backend.raw)['modes']]}
        page.evaluate("data=>{window.demoDevices=data.devices;window.hassMock={user:{id:'ha-admin',is_admin:true},states:data.states,callWS:m=>window.backend(m),connection:{subscribeMessage:async()=>()=>{}}};}",{'devices':backend.devices,'states':{**backend.states,'alarm_control_panel.zentrale':{'state':'disarmed','attributes':attrs}}})
        install_brand_assets(page)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/wachturm-icons.js'))
        page.add_script_tag(content=NATIVE_STUBS)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-card.js'))
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-panel.js'))
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').hass=window.hassMock")
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel')._doc&&!document.querySelector('ha-alarmzentrale-panel')._busy")
        root=page.locator('ha-alarmzentrale-panel')
        assert root.locator('a[href="/ha-alarmzentrale?alarm=1"]').count()==0
        page.set_viewport_size({'width':390,'height':844})
        assert root.locator('.mobile-menu').is_visible()
        page.set_viewport_size({'width':1180,'height':1000})
        root.get_by_role('button',name='Diagnose',exact=True).click()
        assert root.locator('a[href="/ha-alarmzentrale?alarm=1"]').count()==1
        root.get_by_role('button',name='Aktionen',exact=True).click()
        assert root.locator('.action-card').count()>0
        root.locator('.action-card [data-action=edit]').first.click()
        root.locator('[data-field=action-kind]').select_option('notify')
        root.locator('[data-field=notification-platform]').select_option('android')
        assert root.locator('[data-field=notification-screen-on]').is_visible()
        assert root.locator('[data-duration=notification-tts-repeat]').is_visible()
        page.screenshot(path=str(ROOT/'qa/notification-options-dev7.png'),full_page=True)
        root.locator('[data-dialog-action=cancel]').click()
        backend.runtime.engine.update_sensor('binary_sensor.door','off')
        backend.runtime.commands.global_for_user(backend.runtime.authorization.spec.users[0],'arm','night')
        backend.runtime.engine.update_sensor('binary_sensor.door','on')
        page.evaluate("()=>{const p=document.querySelector('ha-alarmzentrale-panel');history.pushState({},'', '/ha-alarmzentrale?alarm=1');p.route={};}")
        card=root.locator('ha-alarmzentrale-card');card.wait_for()
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel').shadowRoot.querySelector('ha-alarmzentrale-card')._view")
        assert card.get_attribute('fullscreen') is not None
        assert card.locator('.alarm-active').count()==1
        assert card.locator('ha-card').bounding_box()['height']>=880
        assert root.locator('.top').count()==0
        assert root.locator('.alarm-screen').evaluate('el=>el.scrollHeight===el.clientHeight')
        page.screenshot(path=str(ROOT/'qa/fullscreen-alarm-dev7.png'),full_page=True)
        backend.runtime.engine.areas['home'].acknowledge()
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').shadowRoot.querySelector('ha-alarmzentrale-card').load()")
        page.wait_for_timeout(100)
        assert 'Ignoriert' in card.locator('.source.ignored').inner_text()
        backend.runtime.commands.global_for_user(backend.runtime.authorization.spec.users[0],'disarm')
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').shadowRoot.querySelector('ha-alarmzentrale-card').load()")
        page.wait_for_timeout(100)
        assert card.locator('[data-mode=night]').is_disabled()
        backend.runtime.engine.update_sensor('binary_sensor.door','off')
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').shadowRoot.querySelector('ha-alarmzentrale-card').load()")
        page.wait_for_timeout(100)
        assert card.locator('[data-mode=night]').is_enabled()
        card.locator('[data-mode=night]').click()
        pin=page.locator('ha-alarmzentrale-pin');pin.wait_for()
        assert pin.locator('input').get_attribute('inputmode')=='none'
        assert pin.locator('input').get_attribute('readonly') is not None
        for digit in '1234':pin.locator(f'[data-key="{digit}"]').click()
        assert pin.locator('input').input_value()=='1234'
        pin.locator('[data-key=cancel]').click()
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(100)
        assert root.locator('.alarm-screen').evaluate('el=>el.scrollHeight===el.clientHeight')
        close=root.get_by_role('button',name='Schließen',exact=True)
        page.screenshot(path=str(ROOT/'qa/fullscreen-debug.png'),full_page=True)
        close.wait_for(state='visible')
        assert close.bounding_box()['x']>200
        page.screenshot(path=str(ROOT/'qa/fullscreen-mobile-dev7.png'),full_page=True)
        close.click();page.wait_for_url('http://alarm.test/lovelace')
        assert not errors,errors
        browser.close()
    print('Revision 7 UI passed: notification options, fullscreen pulse, acknowledged contact, blocking/recovery, PIN keypad, mobile menu.')

if __name__=='__main__':run()
