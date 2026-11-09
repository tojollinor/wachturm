"""Opt-in, passive Home Assistant state adapter for diagnostics.

Never controls alarm state or actuators. No polling of radio hardware; values
come exclusively from already cached Home Assistant states.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from collections import deque
import math
from typing import Any

from .diagnostics import Battery, Connectivity, Diagnostics, Settings, Event, RssiSample, RadioSample, Device
from .connection import parse_connection, derived_connectivity

_CHANNELS = ("battery", "battery_level", "battery_voltage", "connectivity", "rssi_device", "rssi_peer")
_STATE_RULES = ("battery_low_states", "battery_good_states", "battery_empty_states",
                "connectivity_online_states", "connectivity_offline_states")
_DEFAULT_RULES = {
    "battery_low_states": ("low",), "battery_good_states": ("good", "ok"),
    "battery_empty_states": ("empty",),
    "connectivity_online_states": ("online", "connected"),
    "connectivity_offline_states": ("offline", "disconnected"),
}
_RADIO_CHANNELS = ("duty_cycle", "carrier_sense")


def _nonneg_int(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"diagnostics.{label} must be an integer >= {minimum}")
    return value


def _entity(value: Any, label: str) -> str:
    if not isinstance(value, str) or "." not in value or not value.strip() or " " in value:
        raise ValueError(f"diagnostics.{label} requires a Home Assistant entity_id")
    return value.strip()


@dataclass(frozen=True)
class DiagnosticConfig:
    enabled: bool
    settings: Settings | None
    devices: dict[str, dict[str, str]]
    radios: dict[str, dict[str, str]]
    state_rules: dict[str, dict[str, tuple[str, ...]]]
    connection_rules: dict[str, dict] = field(default_factory=dict)

    @property
    def entity_ids(self) -> frozenset[str]:
        return frozenset(v for sources in (*self.devices.values(), *self.radios.values()) for v in sources.values())


def parse_diagnostics(root: dict) -> DiagnosticConfig:
    """Optional config; active monitoring requires *all* user-supplied timing values.

    No default timing, incident thresholds or retention is invented.
    Physical devices are deduplicated by HA device_id (fallback sensor key).
    """
    raw = root.get("diagnostics", {})
    if not isinstance(raw, dict):
        raise ValueError("diagnostics must be an object")
    enabled = raw.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("diagnostics.enabled must be boolean")
    if not enabled:
        return DiagnosticConfig(False, None, {}, {}, {})
    if root.get("schema") != 2:
        raise ValueError("diagnostics requires schema=2")
    fields = {
        "offline_grace_seconds": 0, "flap_window_seconds": 1,
        "flap_count": 2, "recovery_seconds": 0,
        "common_outage_window_seconds": 1, "common_outage_count": 2,
        "history_limit": 1, "battery_freshness_seconds": 0,
    }
    missing = set(fields) - set(raw)
    if missing:
        raise ValueError(f"diagnostics settings incomplete: {', '.join(sorted(missing))}")
    args = {k: _nonneg_int(raw[k], k, minimum) for k, minimum in fields.items()}
    settings = Settings(
        offline_grace=timedelta(seconds=args["offline_grace_seconds"]),
        flap_window=timedelta(seconds=args["flap_window_seconds"]),
        flap_count=args["flap_count"],
        recovery_duration=timedelta(seconds=args["recovery_seconds"]),
        common_outage_window=timedelta(seconds=args["common_outage_window_seconds"]),
        common_outage_count=args["common_outage_count"],
        history_limit=args["history_limit"],
        battery_freshness=timedelta(seconds=args["battery_freshness_seconds"]),
    )
    devices: dict[str, dict[str, str]] = {}
    state_rules: dict[str, dict[str, tuple[str, ...]]] = {}
    connection_rules: dict[str, dict] = {}
    for sensor in root.get("sensors", []):
        if not isinstance(sensor, dict):
            raise ValueError("diagnostic sensor must be an object")
        sources = sensor.get("diagnostic_entities", {})
        if not isinstance(sources, dict):
            raise ValueError("diagnostic_entities must be an object")
        if set(sources) - set(_CHANNELS):
            raise ValueError("Unknown diagnostic channel")
        if not sources:
            continue
        device = sensor.get("device_id") or sensor.get("key")
        if not isinstance(device, str) or not device:
            raise ValueError("diagnostics requires device_id or sensor key")
        selected = {k: _entity(v, k) for k, v in sources.items()}
        connection = parse_connection(sensor)
        if device in connection_rules and connection_rules[device] != connection:
            raise ValueError("Conflicting reachability rules for the same physical device")
        connection_rules[device] = connection
        raw_rules = sensor.get("diagnostic_values", {})
        if not isinstance(raw_rules, dict) or set(raw_rules) - set(_STATE_RULES):
            raise ValueError("Invalid diagnostic_values")
        rule = {}
        for key in _STATE_RULES:
            values = raw_rules.get(key, _DEFAULT_RULES[key])
            if not isinstance(values, list) and not isinstance(values, tuple):
                raise ValueError(f"{key} must be an array of states")
            if not all(isinstance(v, str) and v.strip() for v in values):
                raise ValueError(f"{key} requires nonempty state strings")
            rule[key] = tuple(v.strip().lower() for v in values)
            if len(rule[key]) != len(set(rule[key])):
                raise ValueError(f"Duplicate {key}")
        for fields in ((_STATE_RULES[:3]), (_STATE_RULES[3:])):
            values = [value for key in fields for value in rule[key]]
            if len(values) != len(set(values)):
                raise ValueError("Conflicting diagnostic states")
        if device in state_rules and state_rules[device] != rule:
            raise ValueError("Conflicting diagnostic rules for the same physical device")
        state_rules[device] = rule
        previous = devices.setdefault(device, {})
        for channel, entity_id in selected.items():
            if channel in previous and previous[channel] != entity_id:
                raise ValueError("Conflicting diagnostics for the same physical device")
            previous[channel] = entity_id
    radios: dict[str, dict[str, str]] = {}
    raw_radios = raw.get("radio_interfaces", {})
    if not isinstance(raw_radios, dict):
        raise ValueError("diagnostics.radio_interfaces must be an object")
    for interface, mapping in raw_radios.items():
        if not isinstance(interface, str) or not interface or not isinstance(mapping, dict):
            raise ValueError("Invalid radio interface")
        if set(mapping) - set(_RADIO_CHANNELS):
            raise ValueError("Unknown radio diagnostic channel")
        radios[interface] = {k: _entity(v, k) for k, v in mapping.items()}
    return DiagnosticConfig(True, settings, devices, radios, state_rules, connection_rules)


def _state_value(state: Any) -> str | None:
    if state is None:
        return None
    value = getattr(state, "state", None)
    if value is None:
        return None
    value = str(value).strip().lower()
    return value if value not in ("unknown", "unavailable", "none", "") else None


def _numeric(state: Any) -> float | None:
    value = _state_value(state)
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


class DiagnosticAdapter:
    """Idempotent cached-state synchronizer; assumes monotonic UTC timestamps."""

    def __init__(self, config: DiagnosticConfig):
        if not config.enabled or config.settings is None:
            raise ValueError("Diagnostics not enabled")
        self.config = config
        self.core = Diagnostics(config.settings)
        self._last_values: dict[tuple[str, str], str | None] = {}
        self._radio_values: dict[str, tuple[float | None, float | None]] = {}
        self._rssi_values: dict[str, tuple[float | None, float | None]] = {}
        self.battery_levels: dict[str, tuple[float | None, datetime]] = {}
        self.battery_voltages: dict[str, tuple[float | None, datetime]] = {}
        self.last_events: list[Event] = []

    def refresh(self, states: Any, when: datetime) -> bool:
        """Called after a HA state event or tick; never requests network/hardware."""
        changed = False
        events: list[Event] = []
        for device, channels in self.config.devices.items():
            self.core.devices.setdefault(device, Device())
            rules = self.config.state_rules[device]
            connection = self.config.connection_rules.get(device, {"method": "states"})
            for channel in ("battery", "connectivity"):
                entity_id = channels.get(channel)
                if channel == "connectivity" and connection["method"] == "disabled":
                    continue
                derived = channel == "connectivity" and connection["method"] in {"availability", "rssi"}
                if derived and connection["method"] == "rssi":
                    entity_id = channels.get(connection["rssi_channel"])
                if not entity_id:
                    continue
                value = _state_value(states.get(entity_id))
                if derived:
                    value = derived_connectivity(states.get(entity_id), connection)
                key = (device, channel)
                # Refresh only on state transition, preserving original battery timestamp.
                if key in self._last_values and value == self._last_values[key]:
                    continue
                self._last_values[key] = value
                changed = True
                if channel == "battery":
                    def state_kind(value):
                        for key, category in (("battery_empty_states", Battery.EMPTY),
                                              ("battery_low_states", Battery.LOW),
                                              ("battery_good_states", Battery.GOOD)):
                            if value in rules[key]:
                                return category
                        return Battery.UNKNOWN
                    events.extend(self.core.battery_update(device, state_kind(value), when))
                    # An HA cached state is not necessarily a fresh sensor report.
                    # Preserve its last_updated when available, otherwise no age proof.
                    ha_state = states.get(entity_id)
                    stamp = getattr(ha_state, "last_updated", None)
                    if isinstance(stamp, datetime) and stamp.tzinfo is not None and stamp.utcoffset() is not None:
                        self.core.devices[device].battery_at = min(stamp.astimezone(timezone.utc), when)
                    else:
                        self.core.devices[device].battery_at = None
                else:
                    status = (Connectivity(value) if derived else
                              Connectivity.ONLINE if value in rules["connectivity_online_states"] else
                              Connectivity.OFFLINE if value in rules["connectivity_offline_states"] else
                              Connectivity.UNKNOWN)
                    events.extend(self.core.connectivity_update(device, status, when))
            if "battery_level" in channels:
                level = _numeric(states.get(channels["battery_level"]))
                if (device not in self.battery_levels or self.battery_levels[device][0] != level):
                    self.battery_levels[device] = (level, when)
                    changed = True
            if "battery_voltage" in channels:
                voltage = _numeric(states.get(channels["battery_voltage"]))
                if device not in self.battery_voltages or self.battery_voltages[device][0] != voltage:
                    self.battery_voltages[device] = (voltage, when)
                    changed = True
            if "rssi_device" in channels or "rssi_peer" in channels:
                vals = (_numeric(states.get(channels["rssi_device"])) if "rssi_device" in channels else None,
                        _numeric(states.get(channels["rssi_peer"])) if "rssi_peer" in channels else None)
                if device not in self._rssi_values or self._rssi_values[device] != vals:
                    self._rssi_values[device] = vals
                    changed = True
                    self.core.rssi_update(device, when, rssi_device=vals[0], rssi_peer=vals[1])
        for interface, channels in self.config.radios.items():
            vals = (_numeric(states.get(channels["duty_cycle"])) if "duty_cycle" in channels else None,
                    _numeric(states.get(channels["carrier_sense"])) if "carrier_sense" in channels else None)
            if interface not in self._radio_values or self._radio_values[interface] != vals:
                self._radio_values[interface] = vals
                changed = True
                self.core.radio_update(interface, when, duty_cycle=vals[0], carrier_sense=vals[1])
        events.extend(self.core.evaluate(when))
        if events:
            changed = True
        self.last_events = events
        return changed

    def snapshot(self) -> dict:
        """Bounded JSON snapshot; no user credentials or HA state mutation."""
        def dt(value):
            return value.isoformat() if value else None
        return {
            "schema": 1,
            "devices": {key: {
                "connectivity": state.connectivity.value, "battery": state.battery.value,
                "battery_at": dt(state.battery_at), "last_seen": dt(state.last_seen),
                "offline_since": dt(state.offline_since), "online_since": dt(state.online_since),
                "losses": [dt(x) for x in state.losses], "unstable": state.unstable,
                "issued_offline": state.issued_offline,
            } for key, state in self.core.devices.items()},
            "battery_levels": {key: {"value": value, "at": dt(when)}
                               for key, (value, when) in self.battery_levels.items()},
            "battery_voltages": {key: {"value": value, "at": dt(when)}
                                  for key, (value, when) in self.battery_voltages.items()},
            "events": [{"at": dt(e.at), "kind": e.kind, "source": e.source,
                        "detail": e.detail} for e in self.core.history],
            "rssi": {k: [{"at": dt(e.at), "rssi_device": e.rssi_device,
                           "rssi_peer": e.rssi_peer} for e in items]
                     for k, items in self.core.rssi.items()},
            "radio": {k: [{"at": dt(e.at), "duty_cycle": e.duty_cycle,
                            "carrier_sense": e.carrier_sense} for e in items]
                      for k, items in self.core.radio.items()},
        }

    def restore(self, raw: dict) -> None:
        if not isinstance(raw, dict) or raw.get("schema") != 1:
            raise ValueError("Unsupported diagnostic snapshot")
        def dt(value):
            if value is None:
                return None
            result = datetime.fromisoformat(value)
            if result.tzinfo is None or result.utcoffset() is None:
                raise ValueError("Diagnostic timestamps need time zones")
            return result.astimezone(timezone.utc)
        limit = self.config.settings.history_limit
        core = Diagnostics(self.config.settings)
        for key, record in raw.get("devices", {}).items():
            if key not in self.config.devices:
                continue
            core.devices[key] = Device(
                connectivity=Connectivity(record["connectivity"]), battery=Battery(record["battery"]),
                battery_at=dt(record.get("battery_at")), last_seen=dt(record.get("last_seen")),
                offline_since=dt(record.get("offline_since")), online_since=dt(record.get("online_since")),
                losses=deque(dt(v) for v in record.get("losses", [])[-limit:]),
                unstable=bool(record.get("unstable", False)),
                issued_offline=bool(record.get("issued_offline", False)),
            )
        for event in raw.get("events", [])[-limit:]:
            core.history.append(Event(dt(event["at"]), str(event["kind"]),
                                      str(event["source"]), str(event.get("detail", ""))))
        for key, items in raw.get("rssi", {}).items():
            if key in self.config.devices:
                core.rssi[key] = deque((RssiSample(dt(x["at"]), key, x.get("rssi_device"), x.get("rssi_peer"))
                                        for x in items[-limit:]), maxlen=limit)
        for key, items in raw.get("radio", {}).items():
            if key in self.config.radios:
                core.radio[key] = deque((RadioSample(dt(x["at"]), key, x.get("duty_cycle"), x.get("carrier_sense"))
                                         for x in items[-limit:]), maxlen=limit)
        self.battery_levels = {
            key: (item.get("value"), dt(item["at"]))
            for key, item in raw.get("battery_levels", {}).items() if key in self.config.devices
        }
        self.battery_voltages = {
            key: (item.get("value"), dt(item["at"]))
            for key, item in raw.get("battery_voltages", {}).items() if key in self.config.devices
        }
        self.core = core
        # Deliberately reread cached HA states after restart. A restored online
        # value is not proof that the physical device is currently available.
        self._last_values = {}
        self._rssi_values = {}
        self._radio_values = {}

    def view(self) -> dict:
        """Admin-only frontend payload, no secrets or control operations."""
        s = self.core
        def latest(series: dict, item: str) -> dict | None:
            rows = series.get(item)
            if not rows:
                return None
            entry = rows[-1]
            return {k: v for k, v in vars(entry).items() if k != "at"} | {"at": entry.at.isoformat()}
        return {
            "enabled": True,
            "devices": [{"id": device, "connectivity": state.connectivity.value,
                         "battery": state.battery.value,
                         "battery_level": self.battery_levels.get(device, (None, None))[0],
                         "battery_voltage": self.battery_voltages.get(device, (None, None))[0],
                         "battery_at": state.battery_at.isoformat() if state.battery_at else None,
                         "unstable": state.unstable,
                         "offline_since": state.offline_since.isoformat() if state.offline_since else None,
                         "rssi": latest(s.rssi, device),
                         "history": [{"at": p.at.isoformat(), "rssi_device": p.rssi_device,
                                      "rssi_peer": p.rssi_peer}
                                     for p in s.rssi.get(device, ())]}
                        for device, state in s.devices.items()],
            "radios": [{"id": interface, "latest": latest(s.radio, interface),
                        "history": [{"at": p.at.isoformat(), "duty_cycle": p.duty_cycle,
                                     "carrier_sense": p.carrier_sense}
                                    for p in s.radio.get(interface, ())]}
                       for interface in self.config.radios],
            "events": [{"at": e.at.isoformat(), "kind": e.kind, "source": e.source,
                        "detail": e.detail} for e in s.history],
        }
