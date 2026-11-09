import importlib
import unittest
from unittest.mock import patch
from test_sabotage import config,engine,fixture
from test_revision5 import actions,FakeBackend

control=importlib.import_module('alarm_core_tests.control')


class AlarmElapsedTests(unittest.TestCase):
    def alarm(self):
        clock=[1000.0];area=engine.AreaController(config.parse_specs(fixture(False))[0],clock=lambda:clock[0])
        area.update_sensor('binary_sensor.door','off');area.arm('night');area.update_sensor('binary_sensor.door','on')
        return area,clock

    def test_start_survives_mute_retrigger_history_trim_and_restore(self):
        area,clock=self.alarm();self.assertEqual(control.aggregate([area])['alarm_since'],1000)
        clock[0]=2000;area.mute_actions();self.assertEqual(area.alarm_started_at,1000)
        area.update_sensor('binary_sensor.door','off');area.update_sensor('binary_sensor.door','on')
        self.assertEqual(area.alarm_started_at,1000)
        for _ in range(270):area.record_action_result('push',success=True,info='test')
        self.assertFalse(any(e.kind=='alarm_started' for e in area.history))
        restored=engine.AreaController(area.spec,clock=lambda:clock[0]);restored.restore(area.snapshot())
        self.assertEqual(control.aggregate([restored])['alarm_since'],1000)
        restored.acknowledge();self.assertIsNone(control.aggregate([restored])['alarm_since'])

    def test_old_snapshot_fallback_and_new_alarm_after_disarm(self):
        area,clock=self.alarm();saved=area.snapshot();saved.pop('alarm_started_at')
        restored=engine.AreaController(area.spec);restored.restore(saved)
        self.assertEqual(restored.alarm_started_at,1000)
        area.disarm();self.assertIsNone(area.alarm_started_at)
        clock[0]=3000;area.update_sensor('binary_sensor.door','off');area.arm('night');area.trigger()
        self.assertEqual(area.alarm_started_at,3000)


class SpeechTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_text_tokens_used_for_speech_and_repeats_only(self):
        raw=fixture(False);raw['areas'][0]['actions']=[{'key':'push','name':'Push','domain':'notify','service':'mobile_app_phone','data':{'message':'Push: {alarm_message}'},'notification':{'platform':'android','tts':True,'tts_repeat_seconds':10,'tts_text':'Achtung {sensor} in {area}, Modus {mode}'}}]
        area=engine.AreaController(config.parse_specs(raw)[0]);area.update_sensor('binary_sensor.door','off');area.arm('night');area.update_sensor('binary_sensor.door','on')
        sent=[];backend=FakeBackend()
        async def call(domain,service,*,entity_ids,data):sent.append(data)
        backend.call=call;runner=actions.ActionRunner(area,backend)
        event=next(e for e in area.history if e.kind=='alarm_started')
        with patch.object(actions.time,'monotonic',return_value=100):await runner.handle(event)
        self.assertTrue(sent[0]['message'].startswith('Push:'))
        speech=sent[1]['data']['tts_text'];self.assertTrue(speech.startswith('Achtung Haustür'))
        self.assertIn('Wohnung',speech);self.assertIn('night',speech);self.assertNotIn('Push:',speech)
        with patch.object(actions.time,'monotonic',return_value=111):await runner.repeat_tts()
        self.assertEqual(sent[-1]['data']['tts_text'],speech)

    async def test_text_validation_and_empty_fallback(self):
        for value in [None,True,42,[], 'x'*10001]:
            with self.assertRaises(ValueError):engine.ActionSpec('a','A','notify','mobile_app_phone',data={'message':'Alarm'},notification={'platform':'android','tts_text':value})
