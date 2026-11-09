import asyncio
import importlib
import json
from copy import deepcopy
from urllib.parse import urlparse, parse_qs
import unittest
from test_sabotage import config, engine, editor, fixture
from test_workflows import authorized_runtime, security, commands, FakeBackend, actions
notifications = importlib.import_module("alarm_core_tests.notifications")


class Configuration4Tests(unittest.TestCase):
    def test_modes_without_areas_and_empty_runtime_roundtrip(self):
        raw = fixture()
        for mode in raw["modes"]:
            mode["area_keys"] = []
        raw["sensors"][0]["memberships"][0].pop("mode_rules")
        specs, _ = config.parse_configuration(raw)
        self.assertEqual(specs, ())
        machine = engine.AlarmEngine(specs)
        machine.restore(machine.snapshot())
        raw["modes"][0]["icon"] = "mdi:weather-night"
        text = json.dumps(raw)
        self.assertEqual(json.loads(editor.prepare_configuration(text, raw, editor.revision_of(text)))["modes"][0]["icon"], "mdi:weather-night")

    def controls(self, type="switch", full=True):
        raw, _ = authorized_runtime(full)
        raw["bindings"] = [{"key":"control", "user":"alice", "input_type":type,
            "entity_id":"input_boolean.alarm", "trigger_states":["on"],
            "operation":"arm", "mode":"night", "scope":"mode"}]
        specs, access = config.parse_configuration(raw)
        machine = engine.AlarmEngine(specs)
        machine.update_sensor("binary_sensor.door", "off")
        machine.update_sensor("binary_sensor.tamper", "off")
        return raw, commands.AlarmCommands(machine, security.Authorization(access))

    def test_switch_controls_all_mode_areas_and_disarms(self):
        _, control = self.controls()
        self.assertTrue(control.from_event("alarmzentrale_entity", {"entity_id":"input_boolean.alarm","state":"on"}))
        self.assertTrue(all(a.mode == "night" for a in control.engine.areas.values()))
        self.assertTrue(control.from_event("alarmzentrale_entity", {"entity_id":"input_boolean.alarm","state":"off"}))
        self.assertTrue(all(a.mode is None for a in control.engine.areas.values()))

    def test_switch_and_generic_event_cannot_bypass_area_rights(self):
        _, control = self.controls(full=False)
        self.assertFalse(control.from_event("alarmzentrale_entity", {"entity_id":"input_boolean.alarm","state":"on"}))
        self.assertTrue(all(a.mode is None for a in control.engine.areas.values()))
        self.assertFalse(control.from_event("alarmzentrale_entity", {"entity_id":"input_boolean.alarm","state":"unavailable"}))

    def test_generic_taster_matches_identifying_fields_only(self):
        raw, _ = self.controls()
        raw["bindings"][0].update(input_type="event",event_type="zha_event",event_data={"device_id":"abc", "command":"single"})
        specs, access = config.parse_configuration(raw)
        machine=engine.AlarmEngine(specs)
        machine.update_sensor("binary_sensor.door","off");machine.update_sensor("binary_sensor.tamper","off")
        control=commands.AlarmCommands(machine,security.Authorization(access))
        self.assertFalse(control.from_event("zha_event",{"device_id":"other","command":"single"}))
        self.assertTrue(control.from_event("zha_event",{"device_id":"abc","command":"single"}))
        self.assertTrue(all(a.mode=="night" for a in machine.areas.values()))

    def test_old_single_area_remote_now_arms_entire_mode(self):
        raw, _ = authorized_runtime()
        raw["bindings"]=[{"key":"remote","user":"alice","event_type":"homematic.keypress","device_id":"remote","address":"REMOTE","event_action":"press_short","channel":1,"area":"system_sabotage","operation":"arm","mode":"night"}]
        specs, access=config.parse_configuration(raw)
        machine=engine.AlarmEngine(specs)
        machine.update_sensor("binary_sensor.door","off");machine.update_sensor("binary_sensor.tamper","off")
        control=commands.AlarmCommands(machine,security.Authorization(access))
        self.assertTrue(control.from_event("homematic.keypress",{"device_id":"remote","address":"REMOTE","type":"press_short","subtype":1}))
        machine.update_sensor("binary_sensor.door","on")
        self.assertTrue(machine.areas["home"].alarm_latched)


class Notification4Tests(unittest.IsolatedAsyncioTestCase):
    def area(self, platform="ios"):
        raw=fixture(False)
        raw["areas"][0]["actions"]=[{"key":"push","name":"Push","domain":"notify","service":"mobile_app_phone","data":{"title":"{area}","message":"{alarm_message} | {sensor} | {mode} | {time}"},"notification":{"platform":platform,"critical":True,"persistent":True,"buttons":True}},
            {"key":"siren","name":"Sirene","domain":"siren","service":"turn_on","entity_ids":["siren.alarm"],"timed":True}]
        area=engine.AreaController(config.parse_specs(raw)[0],clock=lambda:1794140000)
        area.update_sensor("binary_sensor.door","off");area.arm("night");area.update_sensor("binary_sensor.door","on")
        return area

    def test_payload_tokens_platforms_and_stale_links(self):
        for platform in ("ios","android"):
            area=self.area(platform);action=area.spec.actions[0];event=next(e for e in area.history if e.kind=="alarm_started")
            links=notifications.NotificationLinks()
            data=notifications.notification_payload(action,area,event,links=links)
            self.assertIn("Haustür",data["message"]);self.assertNotIn("{",data["message"])
            self.assertEqual(len(data["data"]["actions"]),2)
            url=data["data"]["actions"][0]["uri"]
            token=parse_qs(urlparse(url).query)["az_token"][0]
            machine=type("M",(),{"areas":{"home":area}})()
            links.validate(token,"mute","home",machine)
            with self.assertRaises(ValueError):links.validate(token,"disarm",None,machine)
            if platform=="ios":self.assertEqual(data["data"]["push"]["sound"]["critical"],1)
            else:self.assertTrue(data["data"]["persistent"])
            area.disarm()
            with self.assertRaises(ValueError):links.validate(token,"mute","home",machine)
            done=notifications.notification_payload(action,area,area.last_event,links=links,quiet=True)
            self.assertIn("deaktiviert",done["message"])
            self.assertEqual(len(done["data"]["actions"]),0)
            if platform=="ios":self.assertEqual(done["data"]["push"]["sound"],"none")
            else:self.assertFalse(done["data"]["persistent"])

    async def test_remote_alarm_runs_siren_and_push_then_updates_normal_state(self):
        area=self.area("android");backend=FakeBackend();backend.notification_links=notifications.NotificationLinks()
        captured=[]
        original=backend.call
        async def call(domain,service,*,entity_ids,data):
            captured.append((domain,deepcopy(data)));await original(domain,service,entity_ids=entity_ids,data=data)
        backend.call=call
        runner=actions.ActionRunner(area,backend)
        await runner.handle(next(e for e in area.history if e.kind=="alarm_started"))
        self.assertEqual(backend.states["siren.alarm"],"on")
        self.assertTrue(any(domain=="notify" for domain,_ in captured))
        area.update_sensor("binary_sensor.door","off")
        await runner.handle(area.last_event)
        self.assertIn("Normalzustand",captured[-1][1]["message"])
        self.assertEqual(captured[-1][1]["data"]["channel"],"Alarmzentrale Status")
        area.disarm();await runner.handle(area.last_event)
        self.assertEqual(backend.states["siren.alarm"],"off")
        self.assertTrue(any("Alarm beendet" in payload.get("message","") for _,payload in captured))


class Dispatcher4Tests(unittest.IsolatedAsyncioTestCase):
    async def test_abwesend_remote_standby_reaches_actual_dispatch_queue(self):
        import sys, types
        old={k:sys.modules.get(k) for k in ("homeassistant","homeassistant.core")}
        core=types.ModuleType("homeassistant.core");core.HomeAssistant=object
        sys.modules.setdefault("homeassistant",types.ModuleType("homeassistant"));sys.modules["homeassistant.core"]=core
        try:
            adapter=importlib.import_module("alarm_core_tests.ha_actions")
        finally:
            for key,value in old.items():
                if value is None:sys.modules.pop(key,None)
                else:sys.modules[key]=value
        raw,_=authorized_runtime()
        raw["areas"][0]["actions"]=[{"key":"siren","name":"Sirene","domain":"siren","service":"turn_on","entity_ids":["siren.alarm"],"timed":True},
          {"key":"push","name":"Push","domain":"notify","service":"mobile_app_phone","data":{"message":"{alarm_message}"},"notification":{"platform":"ios","critical":True}},
          {"key":"armed","name":"Aktivierung","domain":"notify","service":"mobile_app_phone","data":{"message":"Aktiviert"},"when":"armed"}]
        raw["bindings"]=[{"key":"remote","user":"alice","event_type":"homematic.keypress","device_id":"remote","address":"R","event_action":"press_short","channel":1,"area":"system_sabotage","operation":"arm","mode":"away"}]
        specs,access=config.parse_configuration(raw);clock=[1794140000.0]
        machine=engine.AlarmEngine(specs,clock=lambda:clock[0])
        machine.update_sensor("binary_sensor.door","off");machine.update_sensor("binary_sensor.tamper","off")
        states={"siren.alarm":types.SimpleNamespace(state="off",context=types.SimpleNamespace(id="initial"))}
        calls=[]
        async def service(domain,name,data,*,target=None,blocking=True):
            calls.append((domain,name,deepcopy(data)))
            for key in (target or {}).get("entity_id",[]):
                states[key]=types.SimpleNamespace(state="on" if name=="turn_on" else "off",context=types.SimpleNamespace(id=str(len(calls))))
        hass=types.SimpleNamespace(states=types.SimpleNamespace(get=states.get),services=types.SimpleNamespace(has_service=lambda *a:True,async_call=service),async_create_task=asyncio.create_task)
        dispatcher=adapter.HAActionDispatcher(hass,machine)
        for area in machine.areas.values():area.subscribe(dispatcher.enqueue)
        dispatcher.start()
        control=commands.AlarmCommands(machine,security.Authorization(access))
        try:
            self.assertTrue(control.from_event("homematic.keypress",{"device_id":"remote","address":"R","type":"press_short","subtype":1}))
            for q in dispatcher.queues.values():await q.join()
            self.assertTrue(any(data.get("message")=="Aktiviert" for _,_,data in calls))
            machine.update_sensor("binary_sensor.door","on")
            self.assertEqual(machine.areas["home"].phase,engine.Phase.PENDING)
            self.assertEqual(states["siren.alarm"].state,"off")
            clock[0]+=21;machine.tick()
            for q in dispatcher.queues.values():await q.join()
            self.assertEqual(states["siren.alarm"].state,"on")
            self.assertTrue(any("Haustür" in data.get("message","") for _,_,data in calls), [(d,n,x.get("message")) for d,n,x in calls])
            control.disarm_all_from_ha_user("ha-alice")
            for q in dispatcher.queues.values():await q.join()
            self.assertEqual(states["siren.alarm"].state,"off")
        finally:await dispatcher.stop()
