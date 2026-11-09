"""Versioned sensor/area/mode configuration layout.

Pure data adapter: the existing area state machines remain responsible for safety.
Legacy area-local configurations remain supported without an implicit migration.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from .sabotage import SABOTAGE_AREA, with_sabotage_area, sabotage_zone
from .connection import parse_connection

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


def _object(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    return value


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a nonempty string")
    return value.strip()


def _key(value: Any, where: str) -> str:
    v = _str(value, where)
    if not _KEY.fullmatch(v):
        raise ValueError(f"{where} must be a stable lowercase key")
    return v


def _int(value: Any, where: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{where} must be a nonnegative integer")
    return value


def _unique(items: list[str], where: str) -> None:
    if len(set(items)) != len(items):
        raise ValueError(f"Duplicate {where}")


def _effective_mode_rule(rule: dict, mode: dict) -> dict:
    enabled = rule.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("mode rule enabled must be boolean")
    if not enabled:
        return {}
    behavior = rule.get("behavior", "instant")
    if behavior not in {"instant", "standby", "legacy_delay"}:
        raise ValueError("mode rule behavior must be instant, standby or legacy_delay")
    if behavior == "legacy_delay":
        delay = _int(rule.get("delay_seconds"), "legacy delay")
    else:
        if "delay_seconds" in rule:
            raise ValueError("delay_seconds only valid for legacy_delay")
        delay = mode.get("standby_seconds", 0) if behavior == "standby" else 0
    follow = rule.get("follow_entry_delay", False)
    if type(follow) is not bool:
        raise ValueError("follow_entry_delay must be boolean")
    if type(rule.get("arm_on_close",False)) is not bool:
        raise ValueError("arm_on_close must be boolean")
    result = {"arm_on_close":rule.get("arm_on_close",False), "mode": mode["key"], "entry_delay_seconds": delay, "follow_entry_delay": follow}
    if "initial_policy" in rule:
        if rule["initial_policy"] not in {"block", "defer", "ignore"}:
            raise ValueError("Invalid mode-specific initial policy")
        result["initial_policy"] = rule["initial_policy"]
    return result


def expand_v2_config(raw: dict) -> dict:
    """Project global modes and sensors into independent area controllers.

    A single physical device can have many selected entities. Every selected
    entity has an independently evaluated area/mode policy and an area-local
    runtime key. We intentionally do not silently merge two signal sources.
    """
    root = with_sabotage_area(_object(raw, "configuration"))
    if root.get("schema") != 2:
        raise ValueError("Global mode/sensor configuration requires schema=2")
    area_defs = _list(root.get("areas"), "areas")
    mode_defs = _list(root.get("modes"), "modes")
    sensor_defs = _list(root.get("sensors"), "sensors")

    area_keys = [_key(_object(a, "area").get("key"), "area.key") for a in area_defs]
    _unique(area_keys, "area keys")
    modes: dict[str, dict] = {}
    for item in mode_defs:
        m = deepcopy(_object(item, "mode"))
        k = _key(m.get("key"), "mode.key")
        if k in modes:
            raise ValueError("Duplicate global mode key")
        if "icon" in m and not re.fullmatch(r"mdi:[a-z0-9-]+", str(m["icon"])):
            raise ValueError("Invalid mode icon")
        m["name"] = _str(m.get("name"), "mode.name")
        m["standby_seconds"] = _int(m.get("standby_seconds", 0), "mode standby")
        members = [_key(a, "mode.area_keys") for a in _list(m.get("area_keys"), "mode.area_keys")]

        _unique(members, "mode area assignments")
        if set(members) - set(area_keys):
            raise ValueError(f"Mode {k} refers to an unknown area")
        m["area_keys"] = members
        from .engine import ModeSpec
        ModeSpec(key=k, name=m["name"], standby_seconds=m["standby_seconds"],
                 exit_delay_seconds=_int(m.get("exit_delay_seconds", 0), "exit delay"), mute_after_seconds=None if m.get("mute_after_seconds",1800) is None else _int(m.get("mute_after_seconds",1800), "action duration"),
                 delay_on_mode_change=m.get("delay_on_mode_change",False), restart_timer_on_retrigger=m.get("restart_timer_on_retrigger",True))
        for flag in ("delay_on_mode_change", "restart_timer_on_retrigger"):
            if flag in m and type(m[flag]) is not bool:
                raise ValueError("Mode flags must be boolean")
        modes[k] = m

    expanded_areas: dict[str, dict] = {}
    for item in area_defs:
        a = deepcopy(_object(item, "area"))
        if "modes" in a or "zones" in a:
            raise ValueError("schema=2 areas must use global modes and sensors")
        if "icon" in a and not re.fullmatch(r"mdi:[a-z0-9-]+", str(a["icon"])):
            raise ValueError("Invalid area icon")
        k = a["key"]
        local_modes = [{key: deepcopy(value) for key, value in mode.items() if key != "area_keys"}
                       for mode in modes.values() if k in mode["area_keys"]]
        a["modes"] = local_modes
        for action in a.get("actions",[]):
            if "mode_keys" in action and (not isinstance(action["mode_keys"],list) or any(not isinstance(k,str) or k not in modes for k in action["mode_keys"])):
                raise ValueError("Action refers to an unknown mode")
        a["zones"] = []
        expanded_areas[k] = a

    sensor_keys: list[str] = []
    for item in sensor_defs:
        sensor = _object(item, "sensor")
        sensor_key = _key(sensor.get("key"), "sensor.key")
        sensor_keys.append(sensor_key)
        sensor_name = _str(sensor.get("name"), "sensor.name")
        parse_connection(sensor)
        if sensor.get("sensor_type", "auto") not in {"auto", "door", "window", "garage", "gate", "motion", "glass", "vibration", "smoke", "fire", "heat", "water", "gas", "co", "panic", "sabotage", "sound", "generic"}:
            raise ValueError("Invalid sensor type")
        if "icon" in sensor and not re.fullmatch(r"mdi:[a-z0-9]+(?:-[a-z0-9]+)*", _str(sensor["icon"], "sensor.icon")):
            raise ValueError("Invalid sensor icon")
        if "device_id" in sensor and sensor["device_id"] is not None:
            _str(sensor["device_id"], "sensor.device_id")
        entities: dict[str, dict] = {}
        from .engine import ZoneSpec, ZoneModeSpec
        for entry in _list(sensor.get("entities"), "sensor.entities"):
            entity = _object(entry, "entity")
            entity_key = _key(entity.get("key"), "sensor.entity.key")
            if entity_key in entities:
                raise ValueError(f"Repeated entity key in sensor {sensor_key}")
            entity_id = _str(entity.get("entity_id"), "sensor.entity.entity_id")
            if "." not in entity_id:
                raise ValueError("Invalid Home Assistant entity_id")
            ZoneSpec(key=entity_key, name=entity.get("name") or sensor_name, entity_id=entity_id,
                     attribute=entity.get("attribute"),
                     active_states=tuple(_list(entity.get("active_states", ["on"]), "active states")),
                     normal_states=tuple(_list(entity.get("normal_states", ["off"]), "normal states")),
                     mode_policies=(ZoneModeSpec("validation"),))
            entities[entity_key] = entity
        if not entities and not sensor.get("sabotage"):
            raise ValueError(f"Sensor {sensor_key} must select at least one entity")
        identities = {(e["entity_id"], e.get("attribute")) for e in entities.values()}
        if len(identities) != len(entities):
            raise ValueError(f"Duplicate entity/attribute in sensor {sensor_key}")
        memberships: set[str] = set()
        for item2 in _list(sensor.get("memberships"), "sensor.memberships"):
            link = _object(item2, "sensor membership")
            area_key = _key(link.get("area"), "sensor membership area")
            if area_key not in expanded_areas:
                raise ValueError(f"Sensor {sensor_key} refers to unknown area {area_key}")
            if area_key == SABOTAGE_AREA:
                raise ValueError("Use the dedicated sabotage contact for the system sabotage area")
            if area_key in memberships:
                raise ValueError(f"Sensor {sensor_key} repeats area {area_key}")
            memberships.add(area_key)
            selected = [_key(e, "membership.entity_keys") for e in _list(
                link.get("entity_keys", list(entities)), "membership.entity_keys")]
            if not selected:
                raise ValueError("A sensor membership must select at least one entity")
            _unique(selected, "selected entity keys")
            if set(selected) - set(entities):
                raise ValueError("Sensor membership references an unknown entity")
            entity_rules = _object(link.get("entity_rules", {}), "membership.entity_rules")
            if set(entity_rules) - set(selected):
                raise ValueError("Entity-specific rules for an unselected entity")
            area = expanded_areas[area_key]
            member_mode_keys = {m["key"] for m in area["modes"]}
            for entity_key in selected:
                entity = entities[entity_key]
                overrides = _object(entity_rules.get(entity_key, {}), "entity-specific rules")
                rules = _object(overrides.get("mode_rules", link.get("mode_rules", None)), "mode rules") if (
                    "mode_rules" in overrides or "mode_rules" in link
                ) else {m: {} for m in member_mode_keys}
                if set(rules) - member_mode_keys:
                    raise ValueError(f"Sensor mode rules refer to mode outside area {area_key}")
                mode_policies = [policy for mode in area["modes"]
                                 if mode["key"] in rules
                                 for policy in [_effective_mode_rule(_object(rules[mode["key"]], "mode rule"), mode)]
                                 if policy]
                # A single default entity can preserve a legacy zone key.
                legacy_key = link.get("zone_key")
                runtime_key = _key(
                    legacy_key if legacy_key is not None and (len(selected) == 1 or entity_key == "default")
                    else f"{sensor_key}__{entity_key}",
                    "area sensor runtime key")
                area["zones"].append({
                    "key": runtime_key,
                    "name": overrides.get("name", entity.get("name", sensor_name)),
                    "entity_id": entity["entity_id"],
                    "attribute": overrides.get("attribute", entity.get("attribute")),
                    "mode_policies": mode_policies,
                    "active_states": overrides.get("active_states", entity.get("active_states", ["on"])),
                    "normal_states": overrides.get("normal_states", entity.get("normal_states", ["off"])),
                    "initial_policy": overrides.get("initial_policy", link.get("initial_policy", "block")),
                    "fault_policy": overrides.get("fault_policy", link.get("fault_policy", "block")),
                })
        sabo = sabotage_zone(sensor, expanded_areas[SABOTAGE_AREA]["modes"])
        if sabo is not None:
            expanded_areas[SABOTAGE_AREA]["zones"].append(sabo)
    _unique(sensor_keys, "sensor keys")
    # Validate dormant groups and disabled signals without inventing runtime
    # modes. A group can be saved before it is assigned to an actual mode.
    from .configuration import parse_specs
    for area in expanded_areas.values():
        dormant = [z for z in area["zones"] if not z["mode_policies"]]
        if not area["modes"] or dormant:
            validation = deepcopy(area)
            validation["modes"] = [{"key": "validation", "name": "Validation"}]
            validation["zones"] = deepcopy(dormant if area["modes"] else area["zones"])
            for zone in validation["zones"]:
                zone["mode_policies"] = [{"mode": "validation"}]
            parse_specs({"areas": [validation]})
        area["zones"] = [z for z in area["zones"] if z["mode_policies"]]
    expanded = deepcopy(root)
    # The reserved area stays visible in configuration but has no runtime or
    # permissions until explicitly assigned to at least one mode.
    expanded["areas"] = [a for a in expanded_areas.values() if a["modes"]]
    # Security, service bindings, users, roles, etc. are left untouched.
    return expanded


def upgrade_legacy_config(raw: dict) -> dict:
    """Explicit, lossless conversion of the supported legacy area-local layout.

    Reject conflicting mode definitions, rather than silently merging global
    modes with different behavior. No I/O and no automatic writes are done.
    """
    source = deepcopy(_object(raw, "legacy configuration"))
    if source.get("schema", 1) != 1 or "modes" in source or "sensors" in source:
        raise ValueError("Only legacy area-local configurations can be upgraded")
    areas = _list(source.get("areas"), "areas")
    modes: dict[str, dict] = {}
    new_areas: list[dict] = []
    sensors: list[dict] = []
    area_keys: set[str] = set()
    for old_area in areas:
        area = deepcopy(_object(old_area, "area"))
        area_key = _key(area.get("key"), "area.key")
        if area_key in area_keys:
            raise ValueError("Duplicate area key")
        area_keys.add(area_key)
        old_modes = _list(area.pop("modes", None), "area.modes")
        old_zones = _list(area.pop("zones", []), "area.zones")
        for m in old_modes:
            m = deepcopy(_object(m, "mode"))
            key = _key(m.get("key"), "mode.key")
            if key in modes:
                comparison = {k: v for k, v in modes[key].items() if k not in {"area_keys", "standby_seconds"}}
                if comparison != m:
                    raise ValueError(f"Mode {key} has conflicting area-local settings; manual migration required")
                modes[key]["area_keys"].append(area_key)
            else:
                modes[key] = {**m, "area_keys": [area_key], "standby_seconds": 0}
        for z in old_zones:
            z = deepcopy(_object(z, "zone"))
            zone_key = _key(z.get("key"), "zone.key")
            rules: dict[str, dict] = {}
            for old_p in _list(z.get("mode_policies"), "zone.mode_policies"):
                p = _object(old_p, "zone mode rule")
                mode_key = _key(p.get("mode"), "zone mode")
                delay = _int(p.get("entry_delay_seconds", 0), "entry delay")
                rules[mode_key] = {
                    "behavior": "legacy_delay" if delay else "instant",
                    **({"delay_seconds": delay} if delay else {}),
                    "follow_entry_delay": p.get("follow_entry_delay", False),
                }
            sensors.append({
                "key": f"{area_key}__{zone_key}", "name": z.get("name"),
                "entities": [{"key": "default", "entity_id": z.get("entity_id"),
                              "attribute": z.get("attribute"),
                              "active_states": z.get("active_states", ["on"]),
                              "normal_states": z.get("normal_states", ["off"])}],
                "memberships": [{"area": area_key, "zone_key": zone_key,
                                 "initial_policy": z.get("initial_policy", "block"),
                                 "fault_policy": z.get("fault_policy", "block"),
                                 "mode_rules": rules}],
            })
        new_areas.append(area)
    source.update({"schema": 2, "areas": new_areas, "modes": list(modes.values()), "sensors": sensors})
    return with_sabotage_area(source)
