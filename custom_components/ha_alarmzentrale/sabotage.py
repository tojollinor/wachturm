"""Reserved sabotage area and explicit, independently evaluated contacts."""
from copy import deepcopy
import re

SABOTAGE_AREA = "system_sabotage"
SABOTAGE_NAME = "Sabotage"


def with_sabotage_area(raw: dict) -> dict:
    """Add the dormant system area to v2 documents without assigning modes."""
    result = deepcopy(raw)
    if result.get("schema") != 2:
        return result
    areas = result.get("areas")
    if not isinstance(areas, list):
        raise ValueError("areas must be a list")
    matches = [a for a in areas if isinstance(a, dict) and a.get("key") == SABOTAGE_AREA]
    if len(matches) > 1:
        raise ValueError("Duplicate sabotage area")
    if matches:
        if matches[0].get("name") != SABOTAGE_NAME:
            raise ValueError("The system sabotage area cannot be renamed")
    else:
        areas.append({"key": SABOTAGE_AREA, "name": SABOTAGE_NAME,
                      "actions": [], "ack_policy": "manual", "recovery_fault_grace_seconds": 30})
    return result


def sabotage_zone(sensor: dict, modes: list[dict]) -> dict | None:
    """Validate a contact even while dormant; never derive its meaning."""
    from .engine import ZoneSpec, ZoneModeSpec

    contact = sensor.get("sabotage")
    if contact is None:
        return None
    if not isinstance(contact, dict) or set(contact) - {"entity_id", "attribute", "active_states", "normal_states", "enabled"}:
        raise ValueError("Invalid sabotage contact")
    if type(contact.get("enabled", True)) is not bool:
        raise ValueError("Sabotage enabled must be boolean")
    entity_id = contact.get("entity_id")
    if not isinstance(entity_id, str) or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entity_id):
        raise ValueError("Sabotage contact requires an entity_id")
    for field in ("active_states", "normal_states"):
        values = contact.get(field)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values):
            raise ValueError("Sabotage requires explicit active and normal states")
        if len(values) != len(set(values)):
            raise ValueError("Duplicate sabotage states")
    zone = {"key": f"{sensor['key']}__sabotage", "name": f"{sensor['name']} · Sabotage",
            "entity_id": entity_id, "attribute": contact.get("attribute"),
            "active_states": contact["active_states"], "normal_states": contact["normal_states"],
            "initial_policy": "block", "fault_policy": "block",
            "mode_policies": [{"mode": m["key"], "entry_delay_seconds": 0, "follow_entry_delay": False} for m in modes]}
    # A validation-only policy lets dormant contacts use the same strict checks
    # as active sensors. This policy is never emitted to the alarm engine.
    ZoneSpec(key=zone["key"], name=zone["name"], entity_id=entity_id,
             attribute=zone["attribute"], active_states=tuple(zone["active_states"]),
             normal_states=tuple(zone["normal_states"]),
             mode_policies=tuple(ZoneModeSpec(m["key"]) for m in modes) or (ZoneModeSpec("validation"),))
    return zone if modes and contact.get("enabled", True) else None
