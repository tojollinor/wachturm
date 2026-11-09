import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from test_sabotage import config, fixture

adapter_module = importlib.import_module("alarm_core_tests.diagnostic_adapter")
connection_module = importlib.import_module("alarm_core_tests.connection")
policy = importlib.import_module("alarm_core_tests.diagnostic_policy")


def configured(method="availability", direction="below"):
    raw = fixture(False)
    raw["diagnostics"] = {"enabled": True, "offline_grace_seconds": 5,
                          "flap_window_seconds": 60, "flap_count": 3,
                          "recovery_seconds": 10, "common_outage_window_seconds": 5,
                          "common_outage_count": 2, "history_limit": 20,
                          "battery_freshness_seconds": 300}
    sensor = raw["sensors"][0]
    sensor["device_id"] = "device"
    sensor["diagnostic_entities"] = {"connectivity": "binary_sensor.door", "rssi_device": "sensor.rssi"}
    sensor["connection"] = {"method": method, "rssi_channel": "rssi_device", "direction": direction,
                            "warning_threshold": -75 if direction == "below" else 75,
                            "offline_threshold": -90 if direction == "below" else 90}
    return raw


def state(value):
    return SimpleNamespace(state=value)


class ConnectionTests(unittest.TestCase):
    def test_battery_voltage_survives_diagnostic_snapshot(self):
        raw = configured()
        raw["sensors"][0]["diagnostic_entities"]["battery_voltage"] = "sensor.voltage"
        parsed = adapter_module.parse_diagnostics(raw)
        adapter = adapter_module.DiagnosticAdapter(parsed)
        adapter.refresh({"sensor.voltage": state("2.8"), "binary_sensor.door": state("off")}, datetime.now(timezone.utc))
        self.assertEqual(adapter.view()["devices"][0]["battery_voltage"], 2.8)
        restored = adapter_module.DiagnosticAdapter(parsed)
        restored.restore(adapter.snapshot())
        self.assertEqual(restored.view()["devices"][0]["battery_voltage"], 2.8)

    def test_availability_ignores_on_off_and_restores_after_unavailable(self):
        raw = configured()
        adapter = adapter_module.DiagnosticAdapter(adapter_module.parse_diagnostics(raw))
        when = datetime(2026, 11, 8, tzinfo=timezone.utc)
        states = {"binary_sensor.door": state("on"), "sensor.rssi": state("-60")}
        adapter.refresh(states, when)
        self.assertEqual(adapter.core.devices["device"].connectivity.value, "online")
        states["binary_sensor.door"] = state("off")
        adapter.refresh(states, when + timedelta(seconds=1))
        self.assertEqual(len(adapter.core.devices["device"].losses), 0)
        for unavailable in (None, "unknown", "unavailable"):
            states["binary_sensor.door"] = state(unavailable) if unavailable else None
            adapter.refresh(states, when + timedelta(seconds=2))
            self.assertEqual(adapter.core.devices["device"].connectivity.value, "offline")
        adapter.refresh(states, when + timedelta(seconds=8))
        self.assertTrue(any(e.kind == "offline" for e in adapter.last_events))
        states["binary_sensor.door"] = state("off")
        adapter.refresh(states, when + timedelta(seconds=9))
        self.assertEqual(adapter.core.devices["device"].connectivity.value, "online")

    def test_rssi_both_directions_and_invalid_values(self):
        when = datetime(2026, 11, 8, tzinfo=timezone.utc)
        for direction, values in (("below", [("-60", "online"), ("-80", "online"), ("-91", "offline")]),
                                  ("above", [("60", "online"), ("80", "online"), ("91", "offline")])):
            raw = configured("rssi", direction)
            del raw["sensors"][0]["diagnostic_entities"]["connectivity"]
            adapter = adapter_module.DiagnosticAdapter(adapter_module.parse_diagnostics(raw))
            self.assertIn("sensor.rssi", adapter.config.entity_ids)
            for index, (value, expected) in enumerate(values + [("unknown", "offline"), ("unavailable", "offline"), ("NaN", "offline"), ("Infinity", "offline"), ("invalid", "offline")]):
                adapter.refresh({"sensor.rssi": state(value)}, when + timedelta(seconds=index))
                self.assertEqual(adapter.core.devices["device"].connectivity.value, expected, (direction, value))

    def test_signal_colors_and_strict_threshold_boundaries(self):
        rule = configured()["sensors"][0]["connection"]
        for value, expected in ((-60, "good"), (-75, "good"), (-80, "warning"), (-90, "warning"), (-91, "bad"), (None, "unavailable")):
            self.assertEqual(connection_module.signal_quality(value, rule), expected)

    def test_reference_and_threshold_validation_even_with_diagnostics_disabled(self):
        for mutation in (lambda s: s.pop("device_id"),
                         lambda s: s["diagnostic_entities"].pop("connectivity"),
                         lambda s: s["connection"].update(warning_threshold=-99),
                         lambda s: s["connection"].update(offline_threshold=float("nan")),
                         lambda s: s["connection"].update(rssi_channel="battery")):
            raw = configured()
            mutation(raw["sensors"][0])
            raw["diagnostics"]["enabled"] = False
            with self.assertRaises(ValueError):
                config.parse_configuration(raw)

    def test_rssi_preflight_uses_existing_block_policy(self):
        raw = configured("rssi")
        raw["sensors"][0]["diagnostic_entities"].pop("connectivity")
        adapter = adapter_module.DiagnosticAdapter(adapter_module.parse_diagnostics(raw))
        adapter.refresh({"sensor.rssi": state("-99")}, datetime.now(timezone.utc))
        self.assertEqual(policy.arming_faults(raw, adapter, "home", "away"), (("device", "block"),))
        adapter.refresh({"sensor.rssi": state("-60")}, datetime.now(timezone.utc))
        self.assertEqual(policy.arming_faults(raw, adapter, "home", "away"), ())

    def test_legacy_state_mapping_and_disabled_monitoring(self):
        raw = configured("states")
        raw["sensors"][0]["diagnostic_values"] = {"connectivity_online_states": ["on"], "connectivity_offline_states": ["off"]}
        adapter = adapter_module.DiagnosticAdapter(adapter_module.parse_diagnostics(raw))
        adapter.refresh({"binary_sensor.door": state("off")}, datetime.now(timezone.utc))
        self.assertEqual(adapter.core.devices["device"].connectivity.value, "offline")
        raw["sensors"][0]["connection"]["method"] = "disabled"
        adapter = adapter_module.DiagnosticAdapter(adapter_module.parse_diagnostics(raw))
        adapter.refresh({"binary_sensor.door": state("off")}, datetime.now(timezone.utc))
        self.assertEqual(adapter.core.devices["device"].connectivity.value, "unknown")
        self.assertEqual(policy.arming_faults(raw, adapter, "home", "away"), ())

    def test_sabotage_preflight_also_checks_derived_reachability(self):
        raw = configured("rssi")
        raw["sensors"][0]["diagnostic_entities"].pop("connectivity")
        adapter = adapter_module.DiagnosticAdapter(adapter_module.parse_diagnostics(raw))
        adapter.refresh({"sensor.rssi": state("unavailable")}, datetime.now(timezone.utc))
        self.assertEqual(policy.arming_faults(raw, adapter, "system_sabotage", "away"), (("device", "block"),))


if __name__ == "__main__":
    unittest.main()
