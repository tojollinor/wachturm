import asyncio
import importlib
import unittest
from copy import deepcopy
from test_sabotage import config,engine,fixture
from test_workflows import authorized_runtime,commands,security,actions,FakeBackend,presentation
control=importlib.import_module("alarm_core_tests.control")
notifications=importlib.import_module("alarm_core_tests.notifications")

class CentralTests(unittest.IsolatedAsyncioTestCase):
    async def test_pin_global_command_invalid_pin_lockout_and_rights(self):
        raw,_=authorized_runtime()
        raw["users"][0]["panel_pin_hash"]=security.make_pin_hash("1234")
        specs,access=config.parse_configuration(raw)
        machine=engine.AlarmEngine(specs);machine.update_sensor("binary_sensor.door","off");machine.update_sensor("binary_sensor.tamper","off")
        command=commands.AlarmCommands(machine,security.Authorization(access))
        async def executor(fn,*args):return fn(*args)
        with self.assertRaises(security.AccessDenied):await command.global_from_pin_async(executor,"9999","arm","night")
        self.assertTrue(all(a.mode is None for a in machine.areas.values()))
        self.assertEqual(command.authorization.failed_attempts,1)
        await command.global_from_pin_async(executor,"1234","arm","night",expected_user="alice")
        self.assertTrue(all(a.mode=="night" for a in machine.areas.values()))
        with self.assertRaises(security.AccessDenied):await command.global_from_pin_async(executor,"1234","disarm",expected_user="other")
        self.assertTrue(all(a.mode=="night" for a in machine.areas.values()))
        await command.global_from_pin_async(executor,"1234","disarm")
        self.assertTrue(all(a.mode is None for a in machine.areas.values()))
        self.assertTrue(all("1234" not in str(event) for event in command.audit.events))

    def test_global_mode_change_disarms_excluded_area_atomically(self):
        raw,_=authorized_runtime();raw["modes"][0]["area_keys"]=["home"]
        specs,access=config.parse_configuration(raw);machine=engine.AlarmEngine(specs)
        machine.update_sensor("binary_sensor.door","off");machine.update_sensor("binary_sensor.tamper","off")
        command=commands.AlarmCommands(machine,security.Authorization(access));person=access.users[0]
        command.global_for_user(person,"arm","away")
        command.global_for_user(person,"arm","night")
        self.assertIsNone(machine.areas["system_sabotage"].mode)
        self.assertEqual(machine.areas["home"].mode,"night")

    def test_close_finishes_all_exit_delays_but_never_bypasses_blocker(self):
        raw=fixture();raw["modes"][0]["exit_delay_seconds"]=60
        rule=raw["sensors"][0]["memberships"][0]["mode_rules"]["night"]
        rule.update(initial_policy="defer",arm_on_close=True)
        machine=engine.AlarmEngine(config.parse_specs(raw))
        machine.update_sensor("binary_sensor.door","on");machine.update_sensor("binary_sensor.tamper","off")
        machine.arm_mode("night")
        self.assertTrue(all(a.phase==engine.Phase.ARMING for a in machine.areas.values()))
        machine.update_sensor("binary_sensor.door","off")
        self.assertTrue(all(a.phase==engine.Phase.ARMED for a in machine.areas.values()))
        for area in machine.areas.values():area.disarm()
        machine.update_sensor("binary_sensor.door","on");machine.arm_mode("night")
        machine.update_sensor("binary_sensor.tamper","on")
        machine.update_sensor("binary_sensor.door","off")
        self.assertTrue(all(a.phase==engine.Phase.ARMING for a in machine.areas.values()))

    def test_test_session_blocks_all_controls_and_records_edges(self):
        _,runtime=authorized_runtime();runtime.commands.test_until=10**12
        with self.assertRaises(security.AccessDenied):runtime.commands.mode_from_ha_user("ha-alice","night","arm")
        with self.assertRaises(security.AccessDenied):runtime.commands.execute(runtime.authorization.spec.users[0],"home","arm","night")
        session=control.SensorTest();area=runtime.engine.areas["home"]
        area.update_sensor("binary_sensor.door","on");session.observe(area,area.last_event)
        area.update_sensor("binary_sensor.door","off");session.observe(area,area.last_event)
        self.assertTrue(session.view()["samples"][0]["closed"])
        self.assertTrue(all(not a.alarm_latched for a in runtime.engine.areas.values()))

    def test_pin_policy_defaults_and_custom_rules(self):
        _,runtime=authorized_runtime()
        self.assertFalse(any(control.pin_policy({},runtime.authorization).values()))
        self.assertEqual(control.pin_policy({"operation_pin":{"disarm":True}},runtime.authorization)["disarm"],True)
        with self.assertRaises(ValueError):control.pin_policy({"operation_pin":{"disarm":"yes"}},runtime.authorization)

    async def test_ios_critical_and_status_are_separate_and_android_tts_is_optional(self):
        raw=fixture(False);raw["areas"][0]["actions"]=[{"key":"push","name":"Push","domain":"notify","service":"mobile_app_phone","data":{"message":"{alarm_message}"},"notification":{"platform":"ios","critical":True,"buttons":True}}]
        area=engine.AreaController(config.parse_specs(raw)[0]);area.update_sensor("binary_sensor.door","off");area.arm("night");area.update_sensor("binary_sensor.door","on")
        backend=FakeBackend();backend.notification_links=notifications.NotificationLinks();sent=[]
        async def call(domain,service,*,entity_ids,data):sent.append(deepcopy(data))
        backend.call=call;runner=actions.ActionRunner(area,backend)
        event=next(e for e in area.history if e.kind=="alarm_started")
        await runner.handle(event)
        self.assertEqual(len(sent),2);self.assertNotEqual(sent[0]["data"]["tag"],sent[1]["data"]["tag"])
        self.assertEqual(sent[0]["data"]["push"]["sound"]["critical"],1)
        self.assertEqual(sent[1]["data"]["push"]["sound"],"none")
        self.assertEqual([a["title"] for a in sent[1]["data"]["actions"]],["Stumm","Deaktivieren"])
        self.assertEqual(sent[0]["data"]["url"],"/ha-alarmzentrale?alarm=1")
        self.assertEqual(notifications.tts_payload("Test",True)["data"]["media_stream"],"alarm_stream_max")


class SensorDraftTests(unittest.TestCase):
    def test_unassigned_contact_and_disabled_sabotage_can_be_tested(self):
        raw=fixture(False);raw["sensors"][0]["memberships"]=[];raw["sensors"][0]["sabotage"]["enabled"]=False
        session=control.SensorTest()
        for entity in ("binary_sensor.door","binary_sensor.tamper"):
            session.observe_config(raw,entity,"on")
            session.observe_config(raw,entity,"off")
        self.assertEqual(len(session.samples),2)
        self.assertTrue(all(v["closed"] for v in session.samples.values()))


class NativePanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_panel_and_only_own_obsolete_registry_entries_removed(self):
        import sys,types
        from enum import IntFlag
        names=['homeassistant','homeassistant.components','homeassistant.components.alarm_control_panel','homeassistant.helpers','homeassistant.helpers.entity_registry','homeassistant.helpers.dispatcher','homeassistant.exceptions']
        previous={name:sys.modules.get(name) for name in names}
        class Base:pass
        class Features(IntFlag):ARM_HOME=1;ARM_AWAY=2;ARM_NIGHT=4
        states=types.SimpleNamespace(DISARMED='disarmed',ARMING='arming',PENDING='pending',TRIGGERED='triggered',ARMED_HOME='armed_home',ARMED_AWAY='armed_away',ARMED_NIGHT='armed_night',ARMED_CUSTOM_BYPASS='armed_custom_bypass')
        entities=[types.SimpleNamespace(entity_id='alarm_control_panel.old_home',domain='alarm_control_panel',platform='ha_alarmzentrale',unique_id='entry_home'),types.SimpleNamespace(entity_id='alarm_control_panel.foreign',domain='alarm_control_panel',platform='other',unique_id='entry_other'),types.SimpleNamespace(entity_id='alarm_control_panel.central',domain='alarm_control_panel',platform='ha_alarmzentrale',unique_id='entry_central')]
        removed=[];registry=types.SimpleNamespace(async_remove=removed.append)
        try:
            for name in names:sys.modules[name]=types.ModuleType(name)
            api=sys.modules['homeassistant.components.alarm_control_panel'];api.AlarmControlPanelEntity=Base;api.AlarmControlPanelEntityFeature=Features;api.AlarmControlPanelState=states;api.CodeFormat=types.SimpleNamespace(NUMBER='number')
            er=sys.modules['homeassistant.helpers.entity_registry'];er.async_get=lambda hass:registry;er.async_entries_for_config_entry=lambda *args:entities
            sys.modules['homeassistant.helpers'].entity_registry=er
            sys.modules['homeassistant.helpers.dispatcher'].async_dispatcher_connect=lambda *args:None
            sys.modules['homeassistant.exceptions'].HomeAssistantError=ValueError
            adapter=importlib.import_module('alarm_core_tests.alarm_control_panel')
            raw,runtime=authorized_runtime();runtime.configuration=raw
            added=[];entry=types.SimpleNamespace(entry_id='entry',title='Alarmzentrale',runtime_data=runtime)
            await adapter.async_setup_entry(object(),entry,added.extend)
            self.assertEqual(len(added),1)
            self.assertEqual(removed,['alarm_control_panel.old_home'])
            central=added[0];self.assertTrue(central.extra_state_attributes['alarmzentrale_panel'])
            self.assertEqual(central._attr_name,'Wachturm')
            self.assertEqual(central.icon,'wachturm:shield')
            custom=adapter.AlarmCentralEntity(types.SimpleNamespace(entry_id='entry',title='Mein Haus'),runtime)
            self.assertEqual(custom._attr_name,'Mein Haus')
            self.assertEqual(central.alarm_state,'disarmed')
            runtime.commands.global_for_user(runtime.authorization.spec.users[0],'arm','night')
            self.assertEqual(central.alarm_state,'armed_night')
            runtime.engine.update_sensor('binary_sensor.door','on')
            self.assertEqual(central.alarm_state,'triggered')
            self.assertEqual(central.icon,'wachturm:shield')
        finally:
            for name,value in previous.items():
                if value is None:sys.modules.pop(name,None)
                else:sys.modules[name]=value


class ServerPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_policy_cannot_be_skipped_by_omitting_pin(self):
        import sys,types
        names=['homeassistant','homeassistant.components','homeassistant.components.websocket_api','homeassistant.core','voluptuous']
        previous={name:sys.modules.get(name) for name in names}
        try:
            for name in names:sys.modules[name]=types.ModuleType(name)
            api=sys.modules['homeassistant.components.websocket_api'];api.require_admin=lambda f:f;api.async_response=lambda f:f;api.websocket_command=lambda schema:lambda f:f
            sys.modules['homeassistant.components'].websocket_api=api
            core=sys.modules['homeassistant.core'];core.HomeAssistant=object;core.callback=lambda f:f
            vol=sys.modules['voluptuous'];vol.Required=lambda key:key;vol.Optional=lambda key:key;vol.In=lambda values:values
            module=importlib.import_module('alarm_core_tests.websocket')
            raw,_=authorized_runtime();raw['users'][0]['panel_pin_hash']=security.make_pin_hash('2468');raw['operation_pin']={'arm':True,'disarm':False,'mute':True,'acknowledge':False}
            specs,access=config.parse_configuration(raw);machine=engine.AlarmEngine(specs);machine.update_sensor('binary_sensor.door','off');machine.update_sensor('binary_sensor.tamper','off')
            auth=security.Authorization(access);command=commands.AlarmCommands(machine,auth)
            runtime=types.SimpleNamespace(configuration=raw,authorization=auth,commands=command,engine=machine,editor_lock=asyncio.Lock())
            async def get_user(uid):return types.SimpleNamespace(is_active=True)
            async def executor(fn,*args):return fn(*args)
            hass=types.SimpleNamespace(auth=types.SimpleNamespace(async_get_user=get_user),async_add_executor_job=executor)
            with self.assertRaises(security.AccessDenied):await module.execute_operation(hass,runtime,'ha-alice','arm','night')
            self.assertTrue(all(a.mode is None for a in machine.areas.values()))
            await module.execute_operation(hass,runtime,'ha-alice','arm','night','2468')
            self.assertTrue(all(a.mode=='night' for a in machine.areas.values()))
            await module.execute_operation(hass,runtime,'ha-alice','disarm')
            self.assertTrue(all(a.mode is None for a in machine.areas.values()))
            command.test_until=10**12
            with self.assertRaises(security.AccessDenied):await module.execute_operation(hass,runtime,'ha-alice','arm','night','2468')
        finally:
            for name,value in previous.items():
                if value is None:sys.modules.pop(name,None)
                else:sys.modules[name]=value
