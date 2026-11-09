"""Strict Home Assistant-independent configuration validation and conversion."""

from __future__ import annotations

import json
from typing import Any

from .engine import ActionSpec, AckPolicy, AreaSpec, FaultPolicy, InitialPolicy, ModeSpec, ZoneModeSpec, ZoneSpec
from .layout import expand_v2_config, upgrade_legacy_config as _upgrade_legacy_config


def _obj(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be true or false")
    return value


def _seconds(value: Any, label: str, *, allow_none: bool = False) -> int | None:
    if allow_none and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _states(value: Any, label: str) -> tuple[str, ...]:
    values = _array(value, label)
    if not values:
        raise ValueError(f"{label} cannot be empty")
    return tuple(_string(s, label) for s in values)


def parse_specs(raw: Any) -> tuple[AreaSpec, ...]:
    """Parse all areas/zones with explicit defaults and fail on malformed values."""
    root = _obj(raw, "root")
    if root.get("schema", 1) == 2:
        root = expand_v2_config(root)
    elif root.get("schema", 1) != 1 or "sensors" in root or "modes" in root:
        raise ValueError("Unsupported or ambiguous configuration layout")
    areas: list[AreaSpec] = []
    for area_raw in _array(root.get("areas"), "areas"):
        area = _obj(area_raw, "area")
        area_key = _string(area.get("key"), "area.key")
        modes: list[ModeSpec] = []
        for m_raw in _array(area.get("modes"), "area.modes"):
            m = _obj(m_raw, "mode")
            modes.append(ModeSpec(
                key=_string(m.get("key"), "mode.key"),
                name=_string(m.get("name"), "mode.name"),
                exit_delay_seconds=_seconds(m.get("exit_delay_seconds", 0), "exit delay"),
                standby_seconds=_seconds(m.get("standby_seconds", 0), "standby seconds"),
                delay_on_mode_change=_bool(m.get("delay_on_mode_change", False), "delay_on_mode_change"),
                mute_after_seconds=_seconds(m.get("mute_after_seconds", 1800), "mute_after_seconds", allow_none=True),
                restart_timer_on_retrigger=_bool(m.get("restart_timer_on_retrigger", True), "restart_timer_on_retrigger"),
            ))
        zones: list[ZoneSpec] = []
        for z_raw in _array(area.get("zones", []), "area.zones"):
            z = _obj(z_raw, "zone")
            policies: list[ZoneModeSpec] = []
            for p_raw in _array(z.get("mode_policies"), "zone.mode_policies"):
                p = _obj(p_raw, "zone mode")
                policies.append(ZoneModeSpec(
                    mode=_string(p.get("mode"), "zone mode key"),
                    entry_delay_seconds=_seconds(p.get("entry_delay_seconds", 0), "entry delay"),
                    follow_entry_delay=_bool(p.get("follow_entry_delay", False), "follow_entry_delay"),
                    initial_policy=InitialPolicy(p["initial_policy"]) if "initial_policy" in p else None,
                    arm_on_close=_bool(p.get("arm_on_close",False),"arm_on_close"),
                ))
            zones.append(ZoneSpec(
                key=_string(z.get("key"), "zone.key"),
                name=_string(z.get("name"), "zone.name"),
                entity_id=_string(z.get("entity_id"), "zone.entity_id"),
                mode_policies=tuple(policies),
                active_states=_states(z.get("active_states", ["on"]), "zone.active_states"),
                normal_states=_states(z.get("normal_states", ["off"]), "zone.normal_states"),
                initial_policy=InitialPolicy(z.get("initial_policy", InitialPolicy.BLOCK.value)),
                fault_policy=FaultPolicy(z.get("fault_policy", FaultPolicy.BLOCK.value)),
                attribute=z.get("attribute"),
            ))
        actions: list[ActionSpec] = []
        for action_raw in _array(area.get("actions", []), "area.actions"):
            action = _obj(action_raw, "action")
            raw_data = _obj(action.get("data", {}), "action.data")
            # Action data is fixed JSON, not a dynamic template or Python expression.
            for forbidden in ("domain", "service"):
                if forbidden in raw_data:
                    raise ValueError(f"Action data cannot contain {forbidden}")
            raw_entities = _array(action.get("entity_ids", []), "action.entity_ids")
            entity_ids = tuple(_string(x, "action.entity_ids") for x in raw_entities)
            actions.append(ActionSpec(
                key=_string(action.get("key"), "action.key"),
                name=_string(action.get("name"), "action.name"),
                domain=_string(action.get("domain"), "action.domain"),
                service=_string(action.get("service"), "action.service"),
                entity_ids=entity_ids,
                data=raw_data,
                repeat_on_retrigger=_bool(action.get("repeat_on_retrigger", False), "repeat_on_retrigger"),
                timed=_bool(action.get("timed", False), "action.timed"),
                when=_string(action.get("when", "alarm"), "action.when"),
                timing=None if "timing" not in action else _string(action["timing"], "action.timing"),
                notification=action.get("notification", {}),
                mode_keys=None if "mode_keys" not in action else tuple(_string(k,"action.mode_keys") for k in _array(action["mode_keys"],"action.mode_keys")),
            ))
        areas.append(AreaSpec(
            key=area_key,
            name=_string(area.get("name"), "area.name"),
            modes=tuple(modes),
            zones=tuple(zones),
            actions=tuple(actions),
            ack_policy=AckPolicy(area.get("ack_policy", "manual")),
            recovery_fault_grace_seconds=_seconds(
                area.get("recovery_fault_grace_seconds", 30), "recovery_fault_grace_seconds"
            ),
        ))
    keys = [a.key for a in areas]
    if (not areas and raw.get("schema", 1) != 2) or len(keys) != len(set(keys)):
        raise ValueError("At least one area and unique area keys are required")
    return tuple(areas)


def upgrade_legacy_config(raw: dict) -> dict:
    """Return an explicitly validated conversion proposal without saving it.

    The projected area specs must remain exactly equivalent, including runtime
    zone keys and the previously configured per-mode entry delays. Ambiguous
    conversions raise ValueError instead of silently weakening alarm rules.
    """
    original = parse_specs(raw)
    converted = _upgrade_legacy_config(raw)
    if parse_specs(converted) != original:
        raise ValueError("Migration changed alarm behavior; manual review required")
    # Retain security configuration exactly, not merely equivalent permissions.
    for field in ("users", "roles", "bindings"):
        if converted.get(field, []) != raw.get(field, []):
            raise ValueError(f"Migration changed {field}")
    return converted


def parse_specs_json(config_json: str) -> tuple[AreaSpec, ...]:
    if not isinstance(config_json, str):
        raise ValueError("JSON configuration must be text")
    try:
        return parse_specs(json.loads(config_json))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON configuration") from exc


def default_config_json() -> str:
    from .sabotage import with_sabotage_area
    raw=with_sabotage_area({
        "schema":2,"areas":[],"sensors":[],
        "modes":[
            {"key":"away","name":"Abwesend","area_keys":[],"exit_delay_seconds":10,"standby_seconds":0},
            {"key":"home","name":"Zuhause","area_keys":[],"standby_seconds":0},
            {"key":"night","name":"Nacht","area_keys":[],"standby_seconds":0},
        ],
    })
    return json.dumps(raw,indent=2,ensure_ascii=False)

# Identity and input event configuration is separate from zones.
# Parsing all parts together prevents installing a partial, unsafe policy.
def parse_security(raw: Any):
    from .security import AlarmUser, EventBinding, Grant, Role, SecuritySpec, Window

    root = _obj(raw, "root")

    def grants(items, label):
        return tuple(Grant(
            area=_string(g.get("area"), "grant.area"),
            operations=tuple(_string(v, "grant.operation") for v in _array(g.get("operations"), "grant.operations")),
            modes=tuple(_string(v, "grant.mode") for v in _array(g.get("modes", []), "grant.modes")),
        ) for item in _array(items, label) for g in [_obj(item, "grant")])

    def minute(value):
        if not isinstance(value, str) or not __import__("re").fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError("Time window must be HH:MM")
        hour, minute = map(int, value.split(":"))
        return hour * 60 + minute

    roles = tuple(Role(key=_string(r.get("key"), "role.key"), grants=grants(r.get("grants", []), "role.grants"))
                  for raw_role in _array(root.get("roles", []), "roles") for r in [_obj(raw_role, "role")])
    users = []
    for raw_user in _array(root.get("users", []), "users"):
        u = _obj(raw_user, "user")
        windows = []
        for raw_window in _array(u.get("time_windows", []), "user.time_windows"):
            w = _obj(raw_window, "window")
            weekdays = _array(w.get("weekdays"), "window.weekdays")
            windows.append(Window(
                weekdays=tuple(weekdays), start_minute=minute(w.get("start")),
                end_minute=minute(w.get("end")),
            ))
        users.append(AlarmUser(
            key=_string(u.get("key"), "user.key"), name=_string(u.get("name"), "user.name"),
            enabled=_bool(u.get("enabled", True), "user.enabled"),
            roles=tuple(_string(r, "user.role") for r in _array(u.get("roles", []), "user.roles")),
            grants=grants(u.get("grants", []), "user.grants"),
            ha_user_ids=tuple(_string(id, "user.ha_user_ids") for id in _array(u.get("ha_user_ids", []), "user.ha_user_ids")),
            panel_pin_hash=u.get("panel_pin_hash"), time_windows=tuple(windows),
        ))
    bindings = []
    for raw_binding in _array(root.get("bindings", []), "bindings"):
        b = _obj(raw_binding, "binding")
        bindings.append(EventBinding(
            key=_string(b.get("key"), "binding.key"), user=_string(b.get("user"), "binding.user"),
            event_type=_string(b.get("event_type", "alarmzentrale_entity"), "binding.event_type"),
            device_id=str(b.get("device_id") or "entity"),
            address=str(b.get("address") or "entity"),
            event_action=str(b.get("event_action") or "state"),
            channel=b.get("channel", 0), area=str(b.get("area") or "mode"),
            operation=_string(b.get("operation"), "binding.operation"),
            mode=b.get("mode"), interface_id=b.get("interface_id"),
            scope=("mode" if root.get("schema")==2 and b.get("operation") in {"arm","disarm"} else b.get("scope", "area")), input_type=b.get("input_type", "homematic"),
            entity_id=b.get("entity_id", ""), trigger_states=tuple(b.get("trigger_states", ["on"])), event_data=b.get("event_data", {}),
        ))
    return SecuritySpec(roles=roles, users=tuple(users), bindings=tuple(bindings))


def parse_configuration(raw: Any):
    from .diagnostic_adapter import parse_diagnostics
    parse_diagnostics(raw)
    from .diagnostic_policy import validate_diagnostic_reactions
    areas = parse_specs(raw)
    validate_diagnostic_reactions(raw)
    security = parse_security(raw)
    from .control import pin_policy
    from types import SimpleNamespace
    pin_policy(raw,SimpleNamespace(spec=security))
    validation_areas = list(areas)
    if raw.get("schema") == 2:
        from types import SimpleNamespace
        active_keys = {a.key for a in areas}
        validation_areas.extend(SimpleNamespace(key=a["key"], modes=()) for a in raw["areas"] if a["key"] not in active_keys)
        if any(binding.scope == "area" and binding.area not in active_keys for binding in security.bindings):
            raise ValueError("Bediengeräte benötigen einen Bereich, der einem Modus zugeordnet ist")
    if raw.get("schema") == 2:
        mode_keys = {m["key"] for m in raw.get("modes", [])}
        if any(b.scope == "mode" and b.mode is not None and b.mode not in mode_keys for b in security.bindings):
            raise ValueError("Bediengerät verweist auf unbekannten Modus")
    security.validate_areas(validation_areas)
    return areas, security


def parse_configuration_json(config_json: str):
    if not isinstance(config_json, str):
        raise ValueError("Configuration must be JSON text")
    try:
        return parse_configuration(json.loads(config_json))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON configuration") from exc
