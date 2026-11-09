import unittest
from copy import deepcopy
from test_sabotage import fixture, config, engine


class ModePolicyTests(unittest.TestCase):
    def make(self, policy, delay=0):
        raw = fixture(False)
        raw["sensors"][0]["memberships"][0]["mode_rules"]["night"]["initial_policy"] = policy
        raw["modes"][0]["exit_delay_seconds"] = delay
        clock = [100.0]
        runtime = engine.AlarmEngine(config.parse_specs(raw), clock=lambda: clock[0])
        runtime.update_sensor("binary_sensor.door", "on")
        return runtime, clock

    def test_block_defer_ignore_and_rearm(self):
        for policy in ("block", "defer", "ignore"):
            with self.subTest(policy=policy):
                runtime, _ = self.make(policy)
                area = runtime.areas["home"]
                if policy == "block":
                    with self.assertRaises(engine.AlarmError):
                        runtime.arm_mode("night")
                    continue
                runtime.arm_mode("night")
                self.assertFalse(area.alarm_latched)
                runtime.update_sensor("binary_sensor.door", "off")
                runtime.update_sensor("binary_sensor.door", "on")
                self.assertEqual(area.alarm_latched, policy == "defer")
                if policy == "ignore":
                    runtime.update_sensor("binary_sensor.door", "unavailable")
                    self.assertFalse(area.alarm_latched)
                    area.disarm()
                    runtime.update_sensor("binary_sensor.door", "off")
                    runtime.arm_mode("night")
                    runtime.update_sensor("binary_sensor.door", "on")
                    self.assertTrue(area.alarm_latched)

    def test_ignore_persists_restart_and_exit_delay(self):
        runtime, clock = self.make("ignore", 10)
        area = runtime.areas["home"]
        runtime.arm_mode("night")
        runtime.update_sensor("binary_sensor.door", "off")
        clock[0] += 11
        runtime.tick()
        self.assertTrue(next(iter(area.zones.values())).ignored_for_arming)
        saved = area.snapshot()
        restored, _ = self.make("ignore", 10)
        restored.areas["home"].restore(saved)
        restored.update_sensor("binary_sensor.door", "off")
        restored.update_sensor("binary_sensor.door", "on")
        self.assertFalse(restored.areas["home"].alarm_latched)

    def test_mode_change_removes_exclusion(self):
        runtime, _ = self.make("ignore")
        runtime.arm_mode("night")
        runtime.update_sensor("binary_sensor.door", "off")
        runtime.arm_mode("away")
        self.assertFalse(next(iter(runtime.areas["home"].zones.values())).ignored_for_arming)
        runtime.update_sensor("binary_sensor.door", "on")
        self.assertEqual(runtime.areas["home"].phase, engine.Phase.PENDING)

    def test_sabotage_switch_preserves_mapping_and_normal_alarm(self):
        raw = fixture()
        raw["sensors"][0]["sabotage"]["enabled"] = False
        before = deepcopy(raw)
        runtime = engine.AlarmEngine(config.parse_specs(raw))
        self.assertNotIn("binary_sensor.tamper", runtime.watched_entities)
        runtime.update_sensor("binary_sensor.door", "off")
        runtime.arm_mode("night")
        runtime.update_sensor("binary_sensor.tamper", "on")
        self.assertFalse(runtime.areas["system_sabotage"].alarm_latched)
        runtime.update_sensor("binary_sensor.door", "on")
        self.assertTrue(runtime.areas["home"].alarm_latched)
        self.assertEqual(raw, before)
        raw["sensors"][0]["sabotage"]["enabled"] = "false"
        with self.assertRaises(ValueError):
            config.parse_specs(raw)

    def test_exit_delay_block_has_explicit_reason(self):
        runtime, clock = self.make("block", 10)
        runtime.update_sensor("binary_sensor.door", "off")
        runtime.arm_mode("night")
        runtime.update_sensor("binary_sensor.door", "on")
        clock[0] += 11
        runtime.tick()
        area = runtime.areas["home"]
        self.assertEqual(area.phase, engine.Phase.ARMING)
        self.assertIn("Zone already active", area.arming_block_reason)
        runtime.update_sensor("binary_sensor.door", "off")
        runtime.tick()
        self.assertEqual(area.phase, engine.Phase.ARMED)
        self.assertEqual(area.arming_block_reason, "")

    def test_legacy_snapshot_and_invalid_new_exclusion(self):
        runtime, _ = self.make("defer")
        runtime.arm_mode("night")
        saved = runtime.areas["home"].snapshot()
        legacy = deepcopy(saved)
        legacy["schema"] = 3
        for value in legacy["zones"].values():
            value.pop("ignored")
        restored, _ = self.make("defer")
        restored.areas["home"].restore(legacy)
        self.assertTrue(next(iter(restored.areas["home"].zones.values())).deferred_until_normal)
        for value in saved["zones"].values():
            value["ignored"] = True
        with self.assertRaises(engine.AlarmError):
            restored.areas["home"].restore(saved)
