"""Exercise configuration, editor and real alarm engine without importing HA."""
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ha_alarmzentrale"
package = types.ModuleType("alarm_core_tests")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("alarm_core_tests", package)
config = importlib.import_module("alarm_core_tests.configuration")
engine = importlib.import_module("alarm_core_tests.engine")
editor = importlib.import_module("alarm_core_tests.editor")
sabotage = importlib.import_module("alarm_core_tests.sabotage")


def fixture(assigned=True):
    return sabotage.with_sabotage_area({
        "schema": 2,
        "areas": [{"key": "home", "name": "Wohnung", "actions": []}],
        "modes": [{"key": key, "name": key, "area_keys": ["home"] + ([sabotage.SABOTAGE_AREA] if assigned else []),
                   "standby_seconds": 20} for key in ("night", "away")],
        "sensors": [{"key": "door", "name": "Haustür", "entities": [
            {"key": "opening", "entity_id": "binary_sensor.door", "active_states": ["on"], "normal_states": ["off"]}],
            "memberships": [{"area": "home", "entity_keys": ["opening"],
                             "mode_rules": {"night": {"behavior": "instant"}, "away": {"behavior": "standby"}}}],
            "sabotage": {"entity_id": "binary_sensor.tamper", "active_states": ["on"], "normal_states": ["off"]}}],
    })


class SabotageTests(unittest.TestCase):
    def test_sensor_type_and_icon_roundtrip_without_alarm_changes(self):
        raw = fixture()
        before = config.parse_specs(raw)
        raw["sensors"][0].update(sensor_type="door", icon="mdi:door")
        self.assertEqual(config.parse_specs(raw), before)
        serialized = json.dumps(raw)
        saved = json.loads(editor.prepare_configuration(serialized, raw, editor.revision_of(serialized)))
        self.assertEqual(saved["sensors"][0]["sensor_type"], "door")
        self.assertEqual(saved["sensors"][0]["icon"], "mdi:door")
        for field, value in (("sensor_type", "unsupported"), ("icon", 'mdi:door" onclick="bad')):
            invalid = deepcopy(raw)
            invalid["sensors"][0][field] = value
            with self.assertRaises(ValueError):
                config.parse_specs(invalid)

    def test_existing_config_and_legacy_upgrade_keep_behavior(self):
        old = {"areas":[{"key":"legacy_area","name":"Vorhandener Bereich","modes":[{"key":"away","name":"Abwesend","exit_delay_seconds":10},{"key":"home","name":"Zuhause"},{"key":"night","name":"Nacht"}],"zones":[]}]}
        upgraded = config.upgrade_legacy_config(old)
        self.assertEqual(config.parse_specs(old), config.parse_specs(upgraded))
        self.assertIn(sabotage.SABOTAGE_AREA, [a["key"] for a in upgraded["areas"]])

    def test_dormant_area_does_not_activate_implicitly(self):
        raw = fixture(False)
        before = deepcopy(raw)
        self.assertEqual([a.key for a in config.parse_specs(raw)], ["home"])
        self.assertEqual(raw, before)
        raw["sensors"][0]["sabotage"]["active_states"] = []
        with self.assertRaises(ValueError):
            config.parse_specs(raw)

    def test_new_default_has_dormant_system_area_and_no_real_sensors(self):
        raw = json.loads(config.default_config_json())
        self.assertEqual(raw["schema"], 2)
        self.assertEqual(raw["sensors"], [])
        self.assertEqual(raw["areas"][-1]["key"], sabotage.SABOTAGE_AREA)
        areas, _ = config.parse_configuration(raw)
        self.assertEqual(areas, ())
        self.assertEqual([a['key'] for a in raw['areas']],[sabotage.SABOTAGE_AREA])
        self.assertTrue(all(not m['area_keys'] for m in raw['modes']))

    def test_global_mode_preflight_is_atomic_with_open_tamper(self):
        runtime = engine.AlarmEngine(config.parse_specs(fixture()))
        self.assertIn("binary_sensor.tamper", runtime.watched_entities)
        runtime.update_sensor("binary_sensor.door", "off")
        runtime.update_sensor("binary_sensor.tamper", "on")
        with self.assertRaises(engine.AlarmError):
            runtime.arm_mode("away")
        self.assertTrue(all(a.phase == engine.Phase.DISARMED for a in runtime.areas.values()))
        runtime.update_sensor("binary_sensor.tamper", "off")
        self.assertEqual(set(runtime.arm_mode("away")), {"home", sabotage.SABOTAGE_AREA})
        runtime.update_sensor("binary_sensor.tamper", "on")
        self.assertTrue(runtime.areas[sabotage.SABOTAGE_AREA].alarm_latched)
        self.assertFalse(runtime.areas["home"].alarm_latched)

    def test_sabotage_requires_its_own_authorization(self):
        raw = fixture()
        raw["users"] = [{"key": "alice", "name": "Alice", "ha_user_ids": ["ha-alice"],
                         "grants": [{"area": "home", "operations": ["arm", "disarm"]}]}]
        specs, security = config.parse_configuration(raw)
        security_module = importlib.import_module("alarm_core_tests.security")
        commands_module = importlib.import_module("alarm_core_tests.commands")
        runtime = engine.AlarmEngine(specs)
        runtime.update_sensor("binary_sensor.door", "off")
        runtime.update_sensor("binary_sensor.tamper", "off")
        commands = commands_module.AlarmCommands(runtime, security_module.Authorization(security))
        with self.assertRaises(security_module.AccessDenied):
            commands.from_ha_user("ha-alice", sabotage.SABOTAGE_AREA, "arm", "away")
        self.assertEqual(runtime.areas[sabotage.SABOTAGE_AREA].phase, engine.Phase.DISARMED)
        raw["users"][0]["grants"].append({"area": sabotage.SABOTAGE_AREA, "operations": ["arm", "disarm"]})
        _, allowed = config.parse_configuration(raw)
        commands = commands_module.AlarmCommands(runtime, security_module.Authorization(allowed))
        commands.from_ha_user("ha-alice", sabotage.SABOTAGE_AREA, "arm", "away")
        self.assertEqual(runtime.areas[sabotage.SABOTAGE_AREA].phase, engine.Phase.ARMED)

    def test_tamper_triggers_own_area_immediately_in_each_assigned_mode(self):
        for mode in ("night", "away"):
            with self.subTest(mode=mode):
                specs = config.parse_specs(fixture())
                controls = {s.key: engine.AreaController(s, clock=lambda: 100) for s in specs}
                home, tamper = controls["home"], controls[sabotage.SABOTAGE_AREA]
                home.update_sensor("binary_sensor.door", "off")
                tamper.update_sensor("binary_sensor.tamper", "off")
                home.arm(mode)
                tamper.arm(mode)
                tamper.update_sensor("binary_sensor.tamper", "on")
                self.assertEqual(tamper.phase, engine.Phase.TRIGGERED)
                self.assertEqual(home.phase, engine.Phase.ARMED)
                self.assertIn("door__sabotage", tamper.alarm_sources)

    def test_opening_keeps_standby_and_never_triggers_tamper(self):
        specs = {s.key: s for s in config.parse_specs(fixture())}
        home = engine.AreaController(specs["home"], clock=lambda: 100)
        home.update_sensor("binary_sensor.door", "off")
        home.arm("away")
        home.update_sensor("binary_sensor.door", "on")
        self.assertEqual(home.phase, engine.Phase.PENDING)
        self.assertEqual(home.phase_deadline, 120)
        self.assertEqual(specs[sabotage.SABOTAGE_AREA].zones[0].entity_id, "binary_sensor.tamper")

    def test_open_or_unavailable_tamper_blocks_arming(self):
        spec = next(s for s in config.parse_specs(fixture()) if s.key == sabotage.SABOTAGE_AREA)
        for state in ("on", "unknown", "unavailable"):
            with self.subTest(state=state):
                control = engine.AreaController(spec)
                control.update_sensor("binary_sensor.tamper", state)
                with self.assertRaises(engine.AlarmError):
                    control.arm("away")
                self.assertEqual(control.phase, engine.Phase.DISARMED)

    def test_missing_state_during_arming_is_fault_not_sabotage(self):
        spec = next(s for s in config.parse_specs(fixture()) if s.key == sabotage.SABOTAGE_AREA)
        control = engine.AreaController(spec)
        control.update_sensor("binary_sensor.tamper", "off")
        control.arm("night")
        control.update_sensor("binary_sensor.tamper", "unavailable")
        self.assertFalse(control.alarm_latched)
        self.assertEqual(control.zones["door__sabotage"].observation, engine.Observation.FAULT)

    def test_sabotage_only_sensor_and_attribute(self):
        raw = fixture()
        sensor = raw["sensors"][0]
        sensor["entities"] = []
        sensor["memberships"] = []
        sensor["sabotage"].update(attribute="tamper", active_states=["true"], normal_states=["false"])
        spec = next(s for s in config.parse_specs(raw) if s.key == sabotage.SABOTAGE_AREA)
        control = engine.AreaController(spec)
        control.update_sensor("binary_sensor.tamper", "off", attributes={"tamper": False})
        control.arm("away")
        control.update_sensor("binary_sensor.tamper", "off", attributes={"tamper": True})
        self.assertTrue(control.alarm_latched)

    def test_invalid_states_duplicates_and_references_fail_closed(self):
        for change in (lambda r: r["sensors"][0]["sabotage"].update(active_states=["off"]),
                       lambda r: r["sensors"][0]["sabotage"].update(active_states=["unknown"]),
                       lambda r: r["sensors"][0]["sabotage"].update(normal_states=[]),
                       lambda r: r["sensors"].append(dict(r["sensors"][0], key="duplicate")),
                       lambda r: r["sensors"][0]["memberships"].append({"area": sabotage.SABOTAGE_AREA})):
            raw = fixture()
            change(raw)
            with self.assertRaises(ValueError):
                config.parse_specs(raw)

    def test_fixed_area_roundtrip_and_rename_delete_rejected(self):
        raw = fixture()
        serialized = json.dumps(raw)
        revision = editor.revision_of(serialized)
        self.assertEqual(json.loads(editor.prepare_configuration(serialized, raw, revision)), raw)
        renamed = deepcopy(raw)
        renamed["areas"][-1]["name"] = "Andere"
        with self.assertRaises(ValueError):
            editor.prepare_configuration(serialized, renamed, revision)
        deleted = deepcopy(raw)
        deleted["areas"].pop()
        with self.assertRaises(ValueError):
            editor.prepare_configuration(serialized, deleted, revision)

    def test_existing_editor_document_gains_dormant_area_without_revision_change(self):
        raw = fixture(False)
        raw["areas"].pop()
        serialized = json.dumps(raw)
        visible = editor.visible_document(serialized)
        self.assertEqual(visible["revision"], editor.revision_of(serialized))
        self.assertEqual(visible["configuration"]["areas"][-1]["key"], sabotage.SABOTAGE_AREA)
        self.assertNotIn(sabotage.SABOTAGE_AREA, raw["modes"][0]["area_keys"])


if __name__ == "__main__":
    unittest.main()
