from check_ui import install_brand_assets
"""Exercise new configuration workflows against the real pure backend."""
from check_ui import Backend, ROOT, NATIVE_STUBS
from playwright.sync_api import sync_playwright
import json


def run():
    backend=Backend();errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(**({'channel':'msedge'} if __import__('sys').platform=='win32' else {}),headless=True)
        page=browser.new_page(viewport={"width":1180,"height":1000},color_scheme="dark")
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.expose_binding("backend",backend.call)
        page.set_content('<style>body{--primary-text-color:#e8edf2;--secondary-text-color:#a1acb7;--primary-background-color:#101419;--secondary-background-color:#202830;--card-background-color:#181e25;--divider-color:#34404c;--primary-color:#15b5cc;--text-primary-color:#06191c;--success-color:#5bce9a;--error-color:#ff7979;--warning-color:#edb65a;background:#101419}</style><ha-alarmzentrale-panel></ha-alarmzentrale-panel>')
        page.evaluate("data=>{window.demoDevices=data.devices;window.hassMock={user:{id:'ha-admin',is_admin:true},states:data.states,callWS:msg=>window.backend(msg),connection:{subscribeMessage:async()=>()=>{}}};}",{"devices":backend.devices,"states":backend.states})
        install_brand_assets(page)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/wachturm-icons.js'))
        page.add_script_tag(content=NATIVE_STUBS)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-panel.js'))
        page.evaluate("document.querySelector('ha-alarmzentrale-panel').hass=window.hassMock")
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel')._doc&&!document.querySelector('ha-alarmzentrale-panel')._busy")
        root=page.locator('ha-alarmzentrale-panel')
        def saved():page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._dialog&&!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert root.locator('[data-action="reload"]').count()==0
        assert root.locator('[data-action="menu"]').count()==0
        root.get_by_role('button',name='Modi',exact=True).click()
        root.locator('[data-action="edit"]').first.click()
        assert root.locator('[data-native-icon]').count()==1
        duration=root.locator('[data-duration="mute_after_seconds"]')
        duration.locator('[data-duration-enable]').check()
        duration.locator('[data-duration-min]').fill('2')
        duration.locator('[data-duration-sec]').fill('15')
        root.get_by_role('button',name='Speichern',exact=True).click();saved()
        assert json.loads(backend.raw)['modes'][0]['mute_after_seconds']==135
        root.get_by_role('button',name='Bediengeräte',exact=True).click()
        root.locator('[data-action="add"]').click()
        root.locator('[data-field="input_type"]').select_option('switch')
        root.locator('[data-field="user"]').select_option('max')
        root.locator('[data-field="mode"]').select_option('night')
        root.locator('[data-native-source="control-entity"] select').select_option('binary_sensor.door')
        assert not root.locator('#binding-operation').is_visible()
        assert root.locator('[data-field="area"]').count()==0
        root.get_by_role('button',name='Speichern',exact=True).click();saved()
        assert json.loads(backend.raw)['bindings'][-1]['input_type']=='switch'
        root.get_by_role('button',name='Aktionen',exact=True).click()
        root.locator('[data-action="add"]').click()
        root.locator('[data-field="action-kind"]').select_option('notify')
        root.locator('[data-field="action-area-system_sabotage"]').check()
        root.locator('[data-field="notify-service"]').select_option('mobile_app_telefon')
        root.locator('[data-field="notify-message"]').fill('Test: ')
        root.locator('[data-insert-token="alarm_message"]').click()
        root.locator('[data-field="notification-platform"]').select_option('android')
        page.screenshot(path=str(ROOT/'qa/action-dev4.png'),full_page=True)
        root.get_by_role('button',name='Speichern',exact=True).click();saved()
        raw=json.loads(backend.raw)
        self_actions=[a['actions'] for a in raw['areas']]
        assert all(len(a)==1 for a in self_actions)
        assert all(a[0]['notification']['platform']=='android' for a in self_actions)
        assert '{alarm_message}' in self_actions[0][0]['data']['message']
        root.get_by_role('button',name='Modi',exact=True).click()
        root.locator('[data-action="edit"]').first.click()
        root.get_by_text("Wohnung",exact=True).last.click()
        for field in root.locator('[data-field^="mode-area-"]').all():field.uncheck()
        root.get_by_role('button',name='Speichern',exact=True).click();saved()
        assert json.loads(backend.raw)['modes'][0]['area_keys']==[]
        root.get_by_role('button',name='Bereiche',exact=True).click()
        root.locator('[data-action="delete"]').last.click()
        assert 'Abwesend' in root.locator('.modal').inner_text()
        root.locator('[data-dialog-action="confirm-delete"]').click();saved()
        raw=json.loads(backend.raw)
        assert len(raw['areas'])==1
        assert all('home' not in m['area_keys'] for m in raw['modes'])
        assert all(g['area']!='home' for p in raw.get('users',[])+raw.get('roles',[]) for g in p.get('grants',[]))
        page.set_viewport_size({"width":390,"height":844})
        root.get_by_role('button',name='Modi',exact=True).click()
        root.locator('[data-action="edit"]').first.click()
        page.screenshot(path=str(ROOT/'qa/mode-dev4-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        browser.close()
    print('Revision 4 browser workflows passed: duration, icons, switch, tokens, multi-area actions, empty mode, deletion and permission cleanup.')

if __name__=='__main__':run()
