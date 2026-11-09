import asyncio
from copy import deepcopy
import importlib
import json
from types import SimpleNamespace
import unittest

from test_sabotage import config, editor, engine, fixture

security = importlib.import_module("alarm_core_tests.security")
commands = importlib.import_module("alarm_core_tests.commands")
presentation = importlib.import_module("alarm_core_tests.presentation")
actions = importlib.import_module("alarm_core_tests.actions")


def authorized_runtime(full=True):
    raw = fixture(True)
    raw["users"] = [{"key": "alice", "name": "Alice", "ha_user_ids": ["ha-alice"], "grants": [
        {"area": key, "operations": ["arm", "disarm", "mute", "acknowledge"]}
        for key in (["home", "system_sabotage"] if full else ["home"])]}]
    specs, spec = config.parse_configuration(raw)
    machine = engine.AlarmEngine(specs)
    machine.update_sensor("binary_sensor.door", "off")
    machine.update_sensor("binary_sensor.tamper", "off")
    auth = security.Authorization(spec)
    runtime = SimpleNamespace(engine=machine, authorization=auth, actions=SimpleNamespace(in_flight=set(), queues={}))
    runtime.commands = commands.AlarmCommands(machine, auth)
    return raw, runtime


class WorkflowTests(unittest.TestCase):
    def test_shared_operation_correlates_audit_and_engine_and_persists(self):
        _, runtime = authorized_runtime()
        runtime.commands.mode_from_ha_user("ha-alice", "night", "arm")
        records = runtime.commands.audit.events
        ids = {e["event_id"] for e in records}
        self.assertEqual(len(ids), 1)
        self.assertNotIn(None, ids)
        for area in runtime.engine.areas.values():
            self.assertEqual({e.event_id for e in area.history if e.kind == "armed"}, ids)
            saved = area.snapshot()
            self.assertTrue(any(e.get("event_id") in ids for e in saved["history"]))
            restored = engine.AreaController(area.spec)
            restored.restore(saved)
            self.assertTrue(any(e.event_id in ids for e in restored.history))
        runtime.commands.disarm_all_from_ha_user("ha-alice")
        self.assertNotIn(runtime.commands.audit.events[-1]["event_id"], ids)
        audit = importlib.import_module("alarm_core_tests.audit").AuditTrail()
        audit.restore(runtime.commands.audit.snapshot())
        self.assertEqual(audit.events, runtime.commands.audit.events)


    def test_missing_area_rights_are_explained_without_granting_access(self):
        _, runtime = authorized_runtime(False)
        view = presentation.runtime_view(runtime, "ha-alice", administrator=True)
        self.assertFalse(view["mode_access"]["night"])
        self.assertIn("Sabotage", view["mode_issues"]["night"])
        private = presentation.runtime_view(runtime, "ha-alice")
        self.assertNotIn("Sabotage", private["mode_issues"]["night"])
        self.assertFalse(private["mode_access"]["night"])


    def test_shared_mode_authorization_is_atomic_and_audited(self):
        _, runtime = authorized_runtime(False)
        with self.assertRaises(security.AccessDenied):
            runtime.commands.mode_from_ha_user("ha-alice", "night", "arm")
        self.assertTrue(all(a.mode is None for a in runtime.engine.areas.values()))
        self.assertTrue(all(e["outcome"] == "denied" for e in runtime.commands.audit.events))

    def test_shared_mode_preflight_does_not_partially_arm(self):
        _, runtime = authorized_runtime()
        runtime.engine.update_sensor("binary_sensor.tamper", "on")
        with self.assertRaises(engine.AlarmError):
            runtime.commands.mode_from_ha_user("ha-alice", "night", "arm")
        self.assertTrue(all(a.mode is None for a in runtime.engine.areas.values()))
        runtime.engine.update_sensor("binary_sensor.tamper", "off")
        self.assertEqual(set(runtime.commands.mode_from_ha_user("ha-alice", "night", "arm")), {"home", "system_sabotage"})
        runtime.commands.mode_from_ha_user("ha-alice", "night", "disarm")
        self.assertTrue(all(a.mode is None for a in runtime.engine.areas.values()))

    def test_configuration_lock_rejects_alarm_commands(self):
        _, runtime = authorized_runtime()
        runtime.commands.configuration_locked = True
        with self.assertRaises(security.AccessDenied):
            runtime.commands.from_ha_user("ha-alice", "home", "arm", "night")
        with self.assertRaises(security.AccessDenied):
            runtime.commands.mode_from_ha_user("ha-alice", "night", "arm")
        self.assertTrue(all(a.mode is None for a in runtime.engine.areas.values()))

    def test_retired_controller_refuses_commands_after_reload(self):
        _, runtime = authorized_runtime()
        runtime.commands.retired = True
        with self.assertRaises(security.AccessDenied):
            runtime.commands.from_ha_user("ha-alice", "home", "arm", "night")
        self.assertTrue(all(a.mode is None for a in runtime.engine.areas.values()))

    def test_disarm_all_authorizes_before_any_area_changes(self):
        _, runtime = authorized_runtime(False)
        for area in runtime.engine.areas.values():
            area.arm("night")
        with self.assertRaises(security.AccessDenied):
            runtime.commands.disarm_all_from_ha_user("ha-alice")
        self.assertTrue(all(a.mode == "night" for a in runtime.engine.areas.values()))
        _, runtime = authorized_runtime()
        runtime.commands.mode_from_ha_user("ha-alice", "night", "arm")
        self.assertTrue(presentation.runtime_view(runtime, "ha-alice")["global_disarm_allowed"])
        runtime.commands.disarm_all_from_ha_user("ha-alice")
        self.assertTrue(all(a.mode is None for a in runtime.engine.areas.values()))

    def test_disabled_identity_sees_no_runtime_details(self):
        raw, _ = authorized_runtime()
        raw["users"][0]["enabled"] = False
        specs, access = config.parse_configuration(raw)
        runtime = SimpleNamespace(engine=engine.AlarmEngine(specs), authorization=security.Authorization(access),
                                  actions=SimpleNamespace(in_flight=set(), queues={}))
        view = presentation.runtime_view(runtime, "ha-alice")
        self.assertEqual(view["areas_state"], [])
        self.assertEqual(view["mode_access"], {})
        self.assertFalse(view["global_disarm_allowed"])

    def test_runtime_view_filters_areas_and_never_grants_admin_alarm_rights(self):
        _, runtime = authorized_runtime(False)
        view = presentation.runtime_view(runtime, "ha-alice")
        self.assertEqual([a["key"] for a in view["areas_state"]], ["home"])
        self.assertFalse(view["mode_access"]["night"])
        self.assertFalse(view["safe_to_edit"])
        self.assertNotIn("system_sabotage", json.dumps(view))
        self.assertNotIn("panel_pin", json.dumps(view))
        administrator = presentation.runtime_view(runtime, "unmapped-admin", administrator=True)
        self.assertEqual(len(administrator["areas_state"]), 2)
        self.assertTrue(all(not a["permissions"] for a in administrator["areas_state"]))

    def test_dormant_area_can_be_saved_before_its_mode_and_rights(self):
        raw, _ = authorized_runtime()
        before = config.parse_specs(raw)
        raw["areas"].append({"key": "new_group", "name": "Neue Gruppe", "actions": []})
        raw["sensors"][0]["memberships"].append({"area": "new_group", "entity_keys": ["opening"]})
        raw["users"][0]["grants"].append({"area": "new_group", "operations": ["arm"]})
        after, _ = config.parse_configuration(raw)
        self.assertEqual(after, before)
        invalid = deepcopy(raw)
        invalid["sensors"][0]["entities"][0]["active_states"] = []
        with self.assertRaises(ValueError):
            config.parse_configuration(invalid)

    def test_disabled_mode_signal_does_not_remove_other_modes(self):
        raw = fixture()
        raw["sensors"][0]["memberships"][0]["mode_rules"]["night"] = {"enabled": False}
        home = next(a for a in config.parse_specs(raw) if a.key == "home")
        self.assertIsNone(home.zones[0].policy("night"))
        self.assertEqual(home.zones[0].policy("away").entry_delay_seconds, 20)
        raw["sensors"][0]["memberships"][0]["mode_rules"]["away"] = {"enabled": False}
        home = next(a for a in config.parse_specs(raw) if a.key == "home")
        self.assertEqual(home.zones, ())

    def test_new_conflicting_multi_area_rule_is_rejected(self):
        raw = fixture()
        candidate = deepcopy(raw)
        candidate["areas"].append({"key": "other", "name": "Anderer Bereich"})
        for mode in candidate["modes"]:
            mode["area_keys"].append("other")
        candidate["sensors"][0]["memberships"].append({"area": "other", "entity_keys": ["opening"],
                                                       "mode_rules": {"night": {"behavior": "standby"}, "away": {"behavior": "standby"}}})
        serialized = json.dumps(raw)
        with self.assertRaisesRegex(editor.EditorError, "Widersprüchliche"):
            editor.prepare_configuration(serialized, candidate, editor.revision_of(serialized))
        candidate["sensors"][0]["memberships"][-1]["mode_rules"]["night"]["behavior"] = "instant"
        editor.prepare_configuration(serialized, candidate, editor.revision_of(serialized))

    def test_configuration_summary_contains_names_not_action_data(self):
        raw = fixture()
        candidate = deepcopy(raw)
        candidate["areas"][0]["actions"] = [{"key": "message", "name": "Meldung", "data": {"message": "SECRET"}}]
        summary = editor.describe_changes(json.dumps(raw), json.dumps(candidate))
        self.assertIn("Wohnung", summary)
        self.assertNotIn("SECRET", summary)

    def test_homematic_mode_binding_checks_all_rights(self):
        for full in (False, True):
            raw, _ = authorized_runtime(full)
            raw["bindings"] = [{"key": "remote", "user": "alice", "event_type": "homematic.keypress", "device_id": "remote-id",
                                "address": "REMOTE", "event_action": "press_short", "channel": 1,
                                "area": "home", "operation": "arm", "mode": "night", "scope": "mode"}]
            specs, access = config.parse_configuration(raw)
            machine = engine.AlarmEngine(specs)
            machine.update_sensor("binary_sensor.door", "off")
            machine.update_sensor("binary_sensor.tamper", "off")
            command = commands.AlarmCommands(machine, security.Authorization(access))
            result = command.from_event("homematic.keypress", {"device_id": "remote-id", "address": "REMOTE", "type": "press_short", "subtype": 1})
            self.assertEqual(result, full)
            self.assertTrue(all(a.mode == ("night" if full else None) for a in machine.areas.values()))
            if full:
                self.assertTrue(all(e["identity_confidence"] == "device_assignment_only" for e in command.audit.events))


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.states = {"siren.alarm": "off"}

    async def call(self, domain, service, *, entity_ids, data):
        self.calls.append((domain, service, entity_ids))
        for key in entity_ids:
            self.states[key] = "on" if service == "turn_on" else "off"

    def read_state(self, key):
        return self.states.get(key)

    def read_revision(self, key):
        return "test-revision"


class ActionWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_notification_does_not_trigger_siren_or_alarm(self):
        backend = FakeBackend()
        spec = engine.AreaSpec("area", "Bereich", (engine.ModeSpec("away", "Abwesend"),), actions=(
            engine.ActionSpec("siren", "Sirene", "siren", "turn_on", ("siren.alarm",), timed=True),
            engine.ActionSpec("battery", "Batteriemeldung", "notify", "phone", data={"message": "Batterie schwach"}, when="battery")))
        area = engine.AreaController(spec)
        runner = actions.ActionRunner(area, backend)
        await runner.handle(engine.AlarmEvent("area", "diagnostic_battery", None, 0))
        self.assertEqual([c[0] for c in backend.calls], ["notify"])
        self.assertFalse(area.alarm_latched)
        self.assertEqual(backend.states["siren.alarm"], "off")

    async def test_alarm_outputs_stop_safely_and_disarm_notification_still_runs(self):
        backend = FakeBackend()
        spec = engine.AreaSpec("area", "Bereich", (engine.ModeSpec("away", "Abwesend"),), actions=(
            engine.ActionSpec("siren", "Sirene", "siren", "turn_on", ("siren.alarm",), timed=True),
            engine.ActionSpec("disarmed", "Unscharf", "notify", "phone", data={"message": "Unscharf"}, when="disarmed")))
        area = engine.AreaController(spec)
        area.arm("away")
        area.trigger("test")
        runner = actions.ActionRunner(area, backend)
        await runner.handle(area.last_event)
        self.assertEqual(backend.states["siren.alarm"], "on")
        area.disarm()
        await runner.handle(area.last_event)
        self.assertEqual(backend.states["siren.alarm"], "off")
        self.assertEqual(backend.calls[-1][0], "notify")

    def test_diagnostic_siren_and_non_alarm_timed_outputs_are_rejected(self):
        with self.assertRaises(ValueError):
            engine.ActionSpec("siren", "Sirene", "siren", "turn_on", when="battery")
        with self.assertRaises(ValueError):
            engine.ActionSpec("light", "Licht", "light", "turn_on", timed=True, when="armed")


if __name__ == "__main__":
    unittest.main()
