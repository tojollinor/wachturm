import unittest
from unittest.mock import patch
from test_sabotage import fixture,config,engine
from test_revision5 import actions,FakeBackend


class MobileRepeatTests(unittest.IsolatedAsyncioTestCase):
    def setup_alarm(self):
        raw=fixture(False)
        raw['areas'][0]['actions']=[{'key':'push','name':'Push','domain':'notify','service':'mobile_app_phone','data':{'message':'{alarm_message}'},'notification':{'platform':'android','tts':True,'tts_max':True,'tts_repeat_seconds':30,'screen_on':True,'open_alarm':True,'alarm_stream':True}}]
        area=engine.AreaController(config.parse_specs(raw)[0])
        area.update_sensor('binary_sensor.door','off');area.arm('night');area.update_sensor('binary_sensor.door','on')
        sent=[];backend=FakeBackend()
        async def call(domain,service,*,entity_ids,data):sent.append(data)
        backend.call=call
        return area,actions.ActionRunner(area,backend),sent

    async def test_open_screen_repeat_and_stop_on_each_end(self):
        for operation in ['mute_actions','acknowledge','disarm']:
            area,runner,sent=self.setup_alarm()
            event=next(e for e in area.history if e.kind=='alarm_started')
            with patch.object(actions.time,'monotonic',return_value=100):await runner.handle(event)
            self.assertEqual(sent[0]['message'],'command_screen_on')
            self.assertEqual(sent[1]['data']['command'],'/ha-alarmzentrale?alarm=1')
            self.assertEqual(sent[2]['data']['channel'],'alarm_stream')
            with patch.object(actions.time,'monotonic',return_value=129):await runner.repeat_tts()
            self.assertEqual(sum(x['message']=='TTS' for x in sent),1)
            with patch.object(actions.time,'monotonic',return_value=130):await runner.repeat_tts()
            self.assertEqual(sum(x['message']=='TTS' for x in sent),2)
            getattr(area,operation)();await runner.handle(area.last_event)
            self.assertFalse(runner.tts_due)
            self.assertTrue(any(x['message']=='command_stop_tts' for x in sent))
            await runner.repeat_tts()
            self.assertEqual(sum(x['message']=='TTS' for x in sent),2)

    async def test_acknowledged_open_contact_remains_exception_until_normal(self):
        area,_,_=self.setup_alarm();area.acknowledge()
        self.assertTrue(any(z['acknowledged_active'] for z in area.zone_summary().values()))
        area.update_sensor('binary_sensor.door','off')
        self.assertFalse(any(z['acknowledged_active'] for z in area.zone_summary().values()))

    async def test_interval_is_bounded_and_not_boolean(self):
        for value in [True,-1,1,9,3601,'30']:
            with self.assertRaises(ValueError):
                engine.ActionSpec('p','Push','notify','mobile_app_phone',notification={'platform':'android','tts_repeat_seconds':value})
