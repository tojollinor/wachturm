import asyncio
import importlib
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from copy import deepcopy
from dataclasses import replace
from test_sabotage import config,engine,fixture
from test_workflows import FakeBackend,actions,authorized_runtime

simulation=importlib.import_module('alarm_core_tests.simulation')


class ModeActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_filter_for_alarm_and_disarm_uses_previous_mode(self):
        for mode,expected in [('night',0),('away',2)]:
            raw=fixture(False)
            raw['areas'][0]['actions']=[{'key':key,'name':key,'domain':'notify','service':'phone','data':{'message':'Test'},'mode_keys':['away'],'when':when} for key,when in [('a','alarm'),('d','disarmed')]]
            area=engine.AreaController(config.parse_specs(raw)[0]);backend=FakeBackend();runner=actions.ActionRunner(area,backend)
            area.update_sensor('binary_sensor.door','off');area.arm(mode);area.trigger('door__opening')
            await runner.handle(area.last_event)
            area.disarm();self.assertEqual(area.last_event.mode,mode)
            await runner.handle(area.last_event)
            self.assertEqual(len(backend.calls),expected)

    async def test_explicit_empty_modes_disable_and_absent_preserves_behavior(self):
        for keys,expected in [((),0),(None,1)]:
            area=engine.AreaController(replace(config.parse_specs(fixture(False))[0],actions=(engine.ActionSpec('a','A','notify','phone',data={'message':'Test'},mode_keys=keys),)))
            backend=FakeBackend();runner=actions.ActionRunner(area,backend)
            area.update_sensor('binary_sensor.door','off');area.arm('night');area.trigger()
            await runner.handle(area.last_event);self.assertEqual(len(backend.calls),expected)

    async def test_unknown_mode_rejected_even_in_dormant_area(self):
        raw=fixture(False);raw['areas'][0]['actions']=[{'key':'a','name':'A','domain':'notify','service':'phone','mode_keys':['missing']}]
        with self.assertRaises(ValueError):config.parse_specs(raw)


class ActionSimulationTests(unittest.IsolatedAsyncioTestCase):
    def make(self,**options):
        action=engine.ActionSpec('push','Push','notify','mobile_app_phone',data={'message':'{alarm_message} · {sensor} · {area} · {mode} · {time}'},notification={'platform':'android','critical':True,'buttons':True,'tts':True,'tts_max':True,'tts_repeat_seconds':10,**options})
        backend=FakeBackend();sent=[]
        async def call(domain,service,*,entity_ids,data):sent.append(data)
        backend.call=call
        zones=config.parse_specs(fixture(False))[0].zones
        return simulation.ActionSimulation(action,backend,'Wohnung','night','Nacht',zones),sent

    async def test_realistic_tokens_and_test_buttons_do_not_target_real_alarm(self):
        test,sent=self.make();await test.start()
        self.assertIn('TEST',sent[0]['title']);self.assertIn('TEST',sent[0]['message'])
        for text in ['Haustür','Wohnung','Nacht']:self.assertIn(text,sent[0]['message'])
        self.assertNotIn('{',sent[0]['message'])
        for button in sent[0]['data']['actions']:
            self.assertIn('az_test=',button['uri']);self.assertNotIn('az_action=',button['uri'])
        self.assertIn('TEST',sent[1]['data']['tts_text'])
        self.assertEqual(test.view()['preview']['summary']['phase'],'triggered')
        await test.mute();self.assertFalse(test.runner.tts_due)
        self.assertTrue(any(x.get('message')=='command_stop_tts' for x in sent))
        await test.stop();self.assertFalse(test.view()['active'])
        with self.assertRaises(ValueError):await test.mute()

    async def test_test_cleanup_stops_only_owned_outputs(self):
        for before,after in [('off','off'),('on','on')]:
            backend=FakeBackend();backend.states['siren.alarm']=before
            action=engine.ActionSpec('siren','Sirene','siren','turn_on',('siren.alarm',))
            test=simulation.ActionSimulation(action,backend,'Wohnung','night','Nacht')
            await test.start();await test.stop()
            self.assertEqual(backend.states['siren.alarm'],after)

    async def test_bounded_lifetime_and_test_links_are_unique(self):
        first,_=self.make();second,_=self.make()
        self.assertNotEqual(first.token,second.token)
        first.until=0;await first.start();await first.run()
        self.assertTrue(first.done.is_set());self.assertFalse(first.runner.tts_due)

    async def test_does_not_mutate_live_engine(self):
        real=engine.AlarmEngine(config.parse_specs(fixture(False)));before=real.snapshot()
        test,_=self.make();await test.start();await test.mute();await test.stop()
        self.assertEqual(real.snapshot(),before)


class ActionTestEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_is_not_saved_tokens_are_checked_and_test_blocks_real_arm(self):
        # The HA adapters were imported with minimal framework stubs by earlier tests.
        import sys
        module=sys.modules.get('alarm_core_tests.websocket')
        adapter=sys.modules.get('alarm_core_tests.ha_actions')
        if module is None or adapter is None:self.skipTest('HA adapter fixture unavailable')
        raw,runtime=authorized_runtime()
        runtime.configuration=deepcopy(raw);runtime.editor_lock=asyncio.Lock();runtime.action_simulation=None;runtime.simulation_task=None
        runtime.engine.update_sensor('binary_sensor.door','off');runtime.engine.update_sensor('binary_sensor.tamper','off')
        before=runtime.engine.snapshot();saved=deepcopy(runtime.configuration)
        backend=FakeBackend()
        tasks=[]
        def create(coro,*args):
            task=asyncio.create_task(coro);tasks.append(task);return task
        hass=SimpleNamespace(async_create_background_task=create)
        results=[];errors=[]
        connection=SimpleNamespace(send_result=lambda ident,result:results.append(result),send_error=lambda ident,code,message:errors.append(message))
        draft={'key':'draft','name':'Entwurf','domain':'notify','service':'mobile_app_phone','data':{'message':'{alarm_message}'},'mode_keys':['night'],'notification':{'platform':'android'}}
        with patch.object(module,'_entry_and_runtime',return_value=(None,runtime)),patch.object(module,'_record_editor'),patch.object(adapter,'HAActionBackend',return_value=backend):
            await module.ws_action_test(hass,connection,{'id':1,'operation':'start','action':draft,'area':'home','mode':'night'})
            self.assertFalse(errors,errors);self.assertTrue(results[-1]['simulation']['active'])
            self.assertTrue(runtime.commands.test_active())
            self.assertEqual(runtime.engine.snapshot(),before);self.assertEqual(runtime.configuration,saved)
            await module.ws_action_test(hass,connection,{'id':2,'operation':'stop','token':'wrong'})
            self.assertTrue(errors);self.assertTrue(runtime.action_simulation.view()['active'])
            await module.ws_action_test(hass,connection,{'id':3,'operation':'stop','token':runtime.action_simulation.token})
            await asyncio.gather(*tasks)
            self.assertFalse(runtime.commands.test_active());self.assertFalse(runtime.action_simulation.view()['active'])
            self.assertEqual(runtime.engine.snapshot(),before)

    async def test_replacing_android_with_ios_drains_previous_test_and_routes_only_ios(self):
        import sys
        module=sys.modules.get('alarm_core_tests.websocket');adapter=sys.modules.get('alarm_core_tests.ha_actions')
        if module is None or adapter is None:self.skipTest('HA adapter fixture unavailable')
        raw,runtime=authorized_runtime();runtime.configuration=raw;runtime.editor_lock=asyncio.Lock();runtime.action_simulation=None;runtime.simulation_task=None
        runtime.engine.update_sensor('binary_sensor.door','off');runtime.engine.update_sensor('binary_sensor.tamper','off')
        backend=FakeBackend();sent=[]
        async def call(domain,service,*,entity_ids,data):sent.append((service,data))
        backend.call=call;tasks=[]
        def create(coro,*args):
            task=asyncio.create_task(coro);tasks.append(task);return task
        hass=SimpleNamespace(async_create_background_task=create);errors=[];results=[]
        connection=SimpleNamespace(send_result=lambda ident,result:results.append(result),send_error=lambda ident,code,message:errors.append(message))
        android={'key':'android','name':'Android','domain':'notify','service':'mobile_app_android','data':{'message':'Alarm'},'notification':{'platform':'android','tts':True,'tts_repeat_seconds':10}}
        ios={'key':'ios','name':'iPhone','domain':'notify','service':'mobile_app_iphone','data':{'message':'Alarm'},'notification':{'platform':'ios','critical':True}}
        with patch.object(module,'_entry_and_runtime',return_value=(None,runtime)),patch.object(module,'_record_editor'),patch.object(adapter,'HAActionBackend',return_value=backend):
            runtime.sensor_test=None
            await module.ws_runtime_test(hass,connection,{'id':0,'operation':'notify','action':'android'})
            self.assertTrue(errors);self.assertFalse(sent);errors.clear()
            await module.ws_action_test(hass,connection,{'id':1,'operation':'start','action':android,'area':'home','mode':'night'})
            old=runtime.action_simulation
            await module.ws_action_test(hass,connection,{'id':2,'operation':'start','action':ios,'area':'home','mode':'night'})
            self.assertFalse(errors,errors);self.assertTrue(old.done.is_set());self.assertFalse(old.runner.tts_due)
            # Stopping the previous Android speech is silent, not another alert.
            after_switch=[(service,data) for service,data in sent if service=='mobile_app_android']
            self.assertEqual(after_switch[-2][1]['message'],'command_stop_tts')
            self.assertEqual(after_switch[-1][1]['message'],'clear_notification')
            count=len(sent);await old.runner.repeat_tts();self.assertEqual(len(sent),count)
            self.assertTrue(all(service=='mobile_app_iphone' for service,data in sent[sent.index(after_switch[-1])+1:]))
            with self.assertRaises(ValueError):await runtime.action_simulation.runner.backend.call('notify','mobile_app_android',entity_ids=(),data={'message':'Forbidden'})
            await module.ws_action_test(hass,connection,{'id':3,'operation':'stop','token':old.token})
            self.assertTrue(errors);self.assertFalse(runtime.action_simulation.done.is_set())
            await module.ws_action_test(hass,connection,{'id':4,'operation':'stop','token':runtime.action_simulation.token})
            await asyncio.gather(*tasks)
