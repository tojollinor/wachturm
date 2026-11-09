from check_ui import install_brand_assets
"""Regression checks for polling, mute feedback, compact layout and local PIN errors."""
from check_revision5_ui import Backend5,ROOT,NATIVE_STUBS
from playwright.sync_api import sync_playwright
import json


def run():
    backend=Backend5();errors=[]
    raw=json.loads(backend.raw);raw['sensors'][0]['memberships'][0]['mode_rules']['away']['initial_policy']='defer';backend.raw=json.dumps(raw);backend.rebuild()
    backend.runtime.engine.update_sensor('binary_sensor.door','on')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(**({'channel':'msedge'} if __import__('sys').platform=='win32' else {}),headless=True)
        page=browser.new_page(viewport={'width':1180,'height':1000},color_scheme='dark')
        page.on('pageerror',lambda e:errors.append(str(e)));page.expose_binding('backend',backend.call)
        page.set_content('<style>body{--primary-text-color:#e8edf2;--secondary-text-color:#a1acb7;--primary-background-color:#101419;--secondary-background-color:#202830;--card-background-color:#181e25;--divider-color:#34404c;--primary-color:#15b5cc;--text-primary-color:#06191c;--success-color:#5bce9a;--error-color:#ff7979;--warning-color:#edb65a;background:#101419;color:#e8edf2;font:14px Arial}</style><div id="card" style="max-width:500px;margin:24px auto"></div>')
        attrs={'alarmzentrale_panel':True,'integration':'ha_alarmzentrale','modi':[{'key':m['key'],'name':m['name']} for m in json.loads(backend.raw)['modes']]}
        page.evaluate("data=>{window.demoDevices=data.devices;window.hassMock={user:{id:'ha-admin',is_admin:true},states:data.states,callWS:m=>window.backend(m),connection:{subscribeMessage:async()=>()=>{}}};}",{'devices':backend.devices,'states':{**backend.states,'alarm_control_panel.zentrale':{'state':'disarmed','attributes':attrs}}})
        install_brand_assets(page)
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/wachturm-icons.js'))
        page.add_script_tag(content=NATIVE_STUBS)
        page.add_script_tag(content="customElements.define('ha-card',class extends HTMLElement{connectedCallback(){this.style.display='block';this.style.borderRadius='16px';this.style.background='var(--card-background-color)';}})")
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-card.js'))
        page.evaluate("()=>{const c=document.createElement('ha-alarmzentrale-card');c.setConfig({entity:'alarm_control_panel.zentrale'});c.hass=window.hassMock;document.querySelector('#card').append(c);}")
        card=page.locator('ha-alarmzentrale-card');page.wait_for_function("document.querySelector('ha-alarmzentrale-card')._view")
        assert card.locator('a').count()==0
        assert card.locator('h2').evaluate('el=>getComputedStyle(el).fontSize')=='18px'
        assert card.locator('.alarm-symbol').evaluate('el=>getComputedStyle(el).getPropertyValue("--mdc-icon-size").trim()')=='36px'
        assert card.locator('.alarm-symbol').get_attribute('icon')=='mdi:shield-off-outline'
        detail=card.locator('[data-detail-key="readiness"]');detail.locator('summary').click()
        await_message="document.querySelector('ha-alarmzentrale-card').load()"
        page.evaluate(await_message);page.wait_for_timeout(100)
        assert detail.evaluate('el=>el.open')
        # Force a changed runtime payload, then verify both open and closed choices survive.
        backend.runtime.engine.areas['home']._emit('zone_normal','door__opening');page.evaluate(await_message);page.wait_for_timeout(100)
        assert detail.evaluate('el=>el.open')
        detail.locator('summary').click();backend.runtime.engine.areas['home']._emit('zone_normal','door__opening');page.evaluate(await_message);page.wait_for_timeout(100)
        assert not detail.evaluate('el=>el.open')
        card.locator('[data-mode="away"]').click();pin=page.locator('ha-alarmzentrale-pin')
        for key in '1234':pin.locator('[data-key="'+key+'"]').click()
        pin.locator('[data-key="ok"]').click();page.wait_for_function("!document.querySelector('ha-alarmzentrale-card')._busy")
        assert card.locator('.source.ignored').count()==1
        assert 'Ignoriert' in card.locator('.source').inner_text()
        backend.runtime.engine.update_sensor('binary_sensor.door','off')
        backend.runtime.commands.global_for_user(backend.runtime.authorization.spec.users[0],'arm','night')
        backend.runtime.engine.update_sensor('binary_sensor.door','on');page.evaluate(await_message);page.wait_for_timeout(80)
        assert card.locator('ha-card').evaluate("el=>getComputedStyle(el).animationName")=='alarm-card-pulse'
        assert card.locator('.alarm-symbol').evaluate("el=>getComputedStyle(el).animationName")=='alarm-icon-pulse'
        assert card.locator('.alarm-symbol').bounding_box()['x']>card.locator('h2').bounding_box()['x']
        card.locator('[data-operation="mute"]').click();page.wait_for_function("!document.querySelector('ha-alarmzentrale-card')._busy")
        assert card.locator('[data-operation="mute"]').is_disabled()
        assert card.locator('[data-operation="mute"] ha-icon').get_attribute('icon')=='mdi:volume-off'
        assert card.locator('.source').count()==1
        assert backend.runtime.engine.areas['home'].alarm_latched
        page.screenshot(path=str(ROOT/'qa/card-muted-dev6.png'),full_page=True)
        backend.runtime.commands.global_for_user(backend.runtime.authorization.spec.users[0],'disarm')
        page.evaluate("()=>{document.querySelector('#card').remove();document.body.insertAdjacentHTML('beforeend','<ha-alarmzentrale-panel></ha-alarmzentrale-panel>');}")
        page.add_script_tag(path=str(ROOT/'custom_components/ha_alarmzentrale/frontend/alarmzentrale-panel.js'));page.evaluate("document.querySelector('ha-alarmzentrale-panel').hass=window.hassMock")
        page.wait_for_function("document.querySelector('ha-alarmzentrale-panel')._doc&&!document.querySelector('ha-alarmzentrale-panel')._busy")
        root=page.locator('ha-alarmzentrale-panel');ready=root.locator('[data-detail-key="readiness"]');ready.locator('summary').click()
        page.wait_for_timeout(4500);assert ready.evaluate('el=>el.open')
        y=root.locator('.tabs').bounding_box()['y'];page.evaluate("()=>{const p=document.querySelector('ha-alarmzentrale-panel');p._notice='Gespeichert';p._render();}")
        assert ready.evaluate('el=>el.open');assert abs(root.locator('.tabs').bounding_box()['y']-y)<1
        assert root.locator('.message-slot').bounding_box()['y']<root.locator('.top').bounding_box()['y']+root.locator('.top').bounding_box()['height']
        assert root.locator('.readiness-mode').count()==2
        root.get_by_role('button',name='Bereiche',exact=True).click()
        root.locator('[data-area-key=home]>summary').click()
        row=root.locator('[data-area-key=home] .sensor-main-row').first
        assert abs(row.locator('.sensor-title').bounding_box()['y']-row.locator('.entity-statuses').bounding_box()['y'])<12
        assert root.locator('.sensor-card .list-description').count()==0
        page.screenshot(path=str(ROOT/'qa/sensor-inline-dev6.png'),full_page=True)
        root.get_by_role('button',name='Benutzer',exact=True).click();root.locator('#pin-policy summary').click()
        page.evaluate("document.querySelector('ha-alarmzentrale-panel')._pinUsers=[]")
        root.locator('[data-save-pin-policy]').click()
        assert 'Zuerst eine Benutzer-PIN' in root.locator('#pin-policy-message').inner_text()
        assert root.locator('#pin-policy').evaluate('el=>el.open')
        assert 'Zuerst eine Benutzer-PIN' not in root.locator('.message-slot').inner_text()
        page.wait_for_timeout(4200);assert root.locator('#pin-policy').evaluate('el=>el.open')
        page.evaluate("document.querySelector('ha-alarmzentrale-panel')._pinUsers=['max']")
        root.locator('[data-save-pin-policy]').click();page.wait_for_function("!document.querySelector('ha-alarmzentrale-panel')._busy")
        assert 'PIN-Regeln gespeichert' in root.locator('#pin-policy-message').inner_text()
        assert root.locator('#pin-policy').evaluate('el=>el.open')
        root.get_by_role('button',name='Diagnose',exact=True).click()
        page.evaluate("()=>{const p=document.querySelector('ha-alarmzentrale-panel');p._diagnostics={enabled:true,radios:[{id:'OCCU',latest:{duty_cycle:35,carrier_sense:2},history:[{at:'2026-11-08T10:00:00Z',duty_cycle:25,carrier_sense:1},{at:'2026-11-08T10:01:00Z',duty_cycle:35,carrier_sense:2}]}]};p._render();}")
        assert root.locator('input[type="range"]').count()==0
        graph=root.locator('[data-graph]').first;assert graph.locator('.graph-value').is_hidden()
        graph.hover();assert graph.locator('.graph-value').is_visible()
        page.mouse.move(1,1);assert graph.locator('.graph-value').is_hidden()
        page.screenshot(path=str(ROOT/'qa/graphs-dev6.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});root.get_by_role('button',name='Übersicht',exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'qa/header-mobile-dev6.png'),full_page=True)
        assert not errors,errors;browser.close()
    print('Revision 6 UI passed: details survive real polls; mute/animation/icons; compact sensor row; local PIN feedback; stable header; graph hover without sliders.')

if __name__=='__main__':run()
