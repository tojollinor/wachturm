import unittest
from test_sabotage import fixture,config,engine
from test_workflows import presentation,authorized_runtime
import importlib
notifications=importlib.import_module("alarm_core_tests.notifications")
control=importlib.import_module("alarm_core_tests.control")

class MutedAlarmTests(unittest.TestCase):
    def test_mute_keeps_original_message_sensor_mode_and_timestamp(self):
        raw=fixture(False);raw["areas"][0]["actions"]=[{"key":"push","name":"Push","domain":"notify","service":"mobile_app_phone","data":{"message":"{alarm_message} · {sensor} · {mode} · {time}"},"notification":{"platform":"android","buttons":True}}]
        clock=[1794140000.0];area=engine.AreaController(config.parse_specs(raw)[0],clock=lambda:clock[0])
        area.update_sensor("binary_sensor.door","off");area.arm("night");area.update_sensor("binary_sensor.door","on")
        action=area.spec.actions[0];event=next(e for e in area.history if e.kind=="alarm_started");links=notifications.NotificationLinks()
        before=notifications.notification_payload(action,area,event,links=links)
        clock[0]+=60;area.mute_actions()
        after=notifications.notification_payload(action,area,area.last_event,links=links,quiet=True)
        self.assertTrue(after["message"].startswith(before["message"]))
        self.assertIn("Sirene stummgeschaltet",after["message"])
        self.assertEqual([b["title"] for b in after["data"]["actions"]],["Deaktivieren"])
        self.assertTrue(area.alarm_latched)
        self.assertTrue(control.aggregate([area])["muted"])
        from urllib.parse import urlparse,parse_qs
        token=parse_qs(urlparse(before["data"]["actions"][0]["uri"]).query)["az_token"][0]
        with self.assertRaisesRegex(ValueError,"bereits stumm"):links.validate(token,"mute","home",type("E",(),{"areas":{"home":area}})())
        clock[0]+=1;area.update_sensor("binary_sensor.door","off")
        normal=notifications.notification_payload(action,area,area.last_event,links=links,quiet=True)
        self.assertIn(before["message"],normal["message"])
        self.assertIn("Normalzustand",normal["message"])

    def test_muted_state_clears_if_a_retrigger_reopens_action_window(self):
        raw=fixture(False);area=engine.AreaController(config.parse_specs(raw)[0])
        area.update_sensor("binary_sensor.door","off");area.arm("night");area.update_sensor("binary_sensor.door","on");area.mute_actions()
        self.assertTrue(control.aggregate([area])["muted"])
        area.update_sensor("binary_sensor.door","off");area.update_sensor("binary_sensor.door","on")
        self.assertFalse(control.aggregate([area])["muted"])
