"""Explicit per-sensor, area and mode policy for already-observed diagnostic events.

No default alarms or notifications. A user's deliberate per-mode 'alarm' choice
is required before diagnostic events can trigger the existing area engine.
"""
from __future__ import annotations

from typing import Any

REACTIONS = frozenset(("log", "event", "alarm"))
KINDS = frozenset(("offline", "offline_low_battery", "battery", "unstable"))


def validate_diagnostic_reactions(config: dict) -> None:
    if config.get("schema") != 2:
        return
    areas = {area["key"] for area in config.get("areas", [])}
    modes = {mode["key"]: set(mode.get("area_keys", [])) for mode in config.get("modes", [])}
    for sensor in config.get("sensors", []):
        for membership in sensor.get("memberships", []):
            area = membership.get("area")
            arm_policy = membership.get("diagnostic_arm_policy", membership.get("fault_policy", "block"))
            if arm_policy not in {"block", "warn", "alarm"}:
                raise ValueError("Invalid diagnostic arming policy")
            raw = membership.get("diagnostic_reactions", {})
            if not isinstance(raw, dict):
                raise ValueError("diagnostic_reactions must be an object")
            if area not in areas:
                raise ValueError("Unknown diagnostic reaction area")
            for mode, reactions in raw.items():
                if mode not in modes or area not in modes[mode]:
                    raise ValueError("Unknown diagnostic reaction mode or area assignment")
                if not isinstance(reactions, dict) or set(reactions) - KINDS:
                    raise ValueError("Invalid diagnostic reaction")
                for kind, action in reactions.items():
                    if action not in REACTIONS:
                        raise ValueError("Invalid diagnostic reaction action")
                    if kind in {"battery", "unstable"} and action == "alarm":
                        raise ValueError("Battery and flapping may not directly start an intrusion alarm")


def _active_membership(sensor: dict, membership: dict, mode: str) -> bool:
    """A diagnostic route must not bypass a disabled sensor/mode assignment."""
    selected = set(membership.get("entity_keys", [e.get("key") for e in sensor.get("entities", [])]))
    for entry in sensor.get("entities", []):
        key = entry.get("key")
        if key not in selected:
            continue
        override = membership.get("entity_rules", {}).get(key, {})
        rules = override.get("mode_rules", membership.get("mode_rules"))
        if rules is None or (mode in rules and rules[mode].get("enabled", True)):
            return True
    return False


def selected_reactions(config: dict, device: str, area: str, mode: str, kind: str,
                       *, low_battery: bool = False) -> set[str]:
    """Return explicitly configured action types. Empty means observation only."""
    results: set[str] = set()
    if config.get("schema") != 2 or not config.get("diagnostics", {}).get("enabled"):
        return results
    if kind == "offline_unstable":
        kind = "unstable"  # Flapping suppresses future offline alarms.
    if kind not in ("offline", "unstable", "battery"):
        return results
    for sensor in config.get("sensors", []):
        if (sensor.get("device_id") or sensor.get("key")) != device:
            continue
        for membership in sensor.get("memberships", []):
            if membership.get("area") != area or not _active_membership(sensor, membership, mode):
                continue
            rules = membership.get("diagnostic_reactions", {}).get(mode, {})
            # Low battery has its own safe default, never falls through to an
            # unrelated general offline alarm action.
            result = (rules.get("offline_low_battery", "log") if kind == "offline" and low_battery
                      else rules.get(kind, "log"))
            if result != "log":
                results.add(result)
    return results


def arming_faults(config: dict, diagnostics: Any, area_key: str, mode_key: str) -> tuple[tuple[str, str], ...]:
    """Connectivity diagnostics evaluated at arming preflight; never assume unknown is OK.

    Only configured connectivity sources participate. Status is evaluated once
    per physical device and alarm area. Policies inherit the already configured
    fault_policy unless explicitly overridden by diagnostic_arm_policy.
    """
    from .diagnostics import Connectivity

    if not config.get("diagnostics", {}).get("enabled"):
        return ()
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for sensor in config.get("sensors", []):
        device = sensor.get("device_id") or sensor.get("key")
        if sensor.get("connection", {}).get("method") == "disabled":
            continue
        if device in seen or ("connectivity" not in sensor.get("diagnostic_entities", {}) and sensor.get("connection", {}).get("method") != "rssi"):
            continue
        member = next((m for m in sensor.get("memberships", []) if m.get("area") == area_key), None)
        sabotage_member = area_key == "system_sabotage" and bool(sensor.get("sabotage")) and sensor["sabotage"].get("enabled", True)
        if sabotage_member and member is None:
            member = {"fault_policy": "block"}
        if member is None or (not sabotage_member and not _active_membership(sensor, member, mode_key)):
            continue
        observed = diagnostics.core.devices.get(device)
        if observed is not None and observed.connectivity == Connectivity.ONLINE:
            continue
        seen.add(device)
        results.append((device, member.get("diagnostic_arm_policy", member.get("fault_policy", "block"))))
    return tuple(results)
