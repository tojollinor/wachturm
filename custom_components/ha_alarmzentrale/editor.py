"""Pure configuration-editor contract; the client never receives PIN hashes.

This module must not import Home Assistant. It is intentionally fail-closed and
is unit-tested independently from the frontend and WebSocket adapters.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

from .configuration import parse_configuration, parse_configuration_json, parse_specs
from .security import make_pin_hash
from .sabotage import SABOTAGE_AREA, with_sabotage_area

MAX_CONFIG_BYTES = 256 * 1024
USER_FIELDS = frozenset({"key", "name", "enabled", "roles", "grants", "ha_user_ids", "time_windows"})


class EditorError(ValueError):
    """An unsafe, stale or malformed configuration change was attempted."""


def mode_conflicts(document: dict) -> set[tuple[str, str, str, str, str]]:
    """Find contradictory effective rules for the same signal and shared mode."""
    seen = {}
    conflicts = set()
    for area in parse_specs(document):
        for zone in area.zones:
            for policy in zone.mode_policies:
                identity = (zone.entity_id, zone.attribute or "", policy.mode)
                meaning = (frozenset(zone.active_states), frozenset(zone.normal_states),
                           policy.entry_delay_seconds, policy.follow_entry_delay,
                           policy.initial_policy or zone.initial_policy, zone.fault_policy, policy.arm_on_close)
                for old_area, old_meaning in seen.get(identity, []):
                    if old_meaning != meaning:
                        conflicts.add((*identity, *sorted((old_area, area.key))))
                seen.setdefault(identity, []).append((area.key, meaning))
    return conflicts


def revision_of(raw: str) -> str:
    return sha256(raw.encode("utf-8")).hexdigest()


def describe_changes(current_raw: str, proposed_raw: str) -> str:
    """Names and object kinds only; never put service data or credentials in audit."""
    before, after = json.loads(current_raw), json.loads(proposed_raw)
    labels = {"areas": "Bereich", "modes": "Modus", "sensors": "Sensor", "users": "Benutzer", "roles": "Rolle", "bindings": "Bediengerät"}
    changes = []
    for field, label in labels.items():
        old = {v["key"]: v for v in before.get(field, [])}
        new = {v["key"]: v for v in after.get(field, [])}
        for key in old.keys() | new.keys():
            if old.get(key) == new.get(key):
                continue
            item = new.get(key) or old[key]
            verb = "angelegt" if key not in old else "gelöscht" if key not in new else "geändert"
            changes.append(f"{label} {str(item.get('name') or key)[:80]} {verb}")
    if before.get("operation_pin") != after.get("operation_pin"):
        changes.append("PIN-Regeln geändert")
    if before.get("diagnostics") != after.get("diagnostics"):
        changes.append("Diagnoseeinstellungen geändert")
    return "; ".join(sorted(changes)[:8]) or "Konfiguration geprüft und gespeichert"


def visible_document(raw: str) -> dict:
    """Expose editable configuration but no local panel credential verifier."""
    config = with_sabotage_area(json.loads(raw))
    pin_users = []
    for user in config.get("users", []):
        if user.pop("panel_pin_hash", None):
            pin_users.append(user["key"])
    # Keep config clean: no fake placeholder fields round-tripped into options.
    return {"configuration": config, "revision": revision_of(raw), "pin_users": pin_users}


def prepare_configuration(current_raw: str, candidate: dict, expected_revision: str) -> str:
    """Validate a full editor proposal and preserve server-side existing hashes.

    A stale editor cannot overwrite a newer revision. A client cannot set,
    replace or clear PIN verifiers through the general configuration command.
    Removing a user explicitly removes their verifier, too.
    """
    if expected_revision != revision_of(current_raw):
        raise EditorError("Konfiguration wurde inzwischen geändert. Bitte neu laden.")
    if not isinstance(candidate, dict):
        raise EditorError("Ungültige Konfiguration")
    new = deepcopy(candidate)
    current = json.loads(current_raw)
    if current.get("schema") == 2 and any(a.get("key") == SABOTAGE_AREA for a in current.get("areas", [])):
        if not any(isinstance(a, dict) and a.get("key") == SABOTAGE_AREA for a in new.get("areas", [])):
            raise EditorError("Der feste Sabotagebereich kann nicht gelöscht werden")
    new = with_sabotage_area(new)
    known = {u["key"]: u.get("panel_pin_hash") for u in current.get("users", [])}
    if not isinstance(new.get("users", []), list):
        raise EditorError("Benutzerliste ist ungültig")
    for user in new.get("users", []):
        if not isinstance(user, dict) or set(user) - USER_FIELDS:
            raise EditorError("Unbekannte oder geheime Benutzerfelder dürfen nicht übermittelt werden")
        digest = known.get(user.get("key"))
        if digest:
            user["panel_pin_hash"] = digest
    try:
        rendered = json.dumps(new, ensure_ascii=False, indent=2, allow_nan=False)
        if len(rendered.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise EditorError("Konfiguration überschreitet das Größenlimit")
        parse_configuration_json(rendered)
        introduced = mode_conflicts(new) - mode_conflicts(current)
        if introduced:
            entity, attribute, mode, area_a, area_b = sorted(introduced)[0]
            raise EditorError(f"Widersprüchliche Regeln für {entity} im Modus {mode} in den Bereichen {area_a} und {area_b}")
    except (TypeError, ValueError) as exc:
        raise EditorError(f"Ungültige Konfiguration: {exc}") from exc
    return rendered


def prepare_pin_change(current_raw: str, user_key: str, pin: str, expected_revision: str) -> str:
    """Create or rotate a local HA panel credential without exposing its hash."""
    if expected_revision != revision_of(current_raw):
        raise EditorError("Konfiguration wurde inzwischen geändert. Bitte neu laden.")
    config = json.loads(current_raw)
    matched = [user for user in config.get("users", []) if user.get("key") == user_key]
    if len(matched) != 1:
        raise EditorError("Benutzer nicht gefunden")
    try:
        matched[0]["panel_pin_hash"] = make_pin_hash(pin)
        parse_configuration(config)
    except (TypeError, ValueError) as exc:
        raise EditorError("PIN konnte nicht eingerichtet werden") from exc
    return json.dumps(config, ensure_ascii=False, indent=2)


def prepare_pin_removal(current_raw: str, user_key: str, expected_revision: str) -> str:
    """Revoke a user's local credential; require explicit confirmation in UI."""
    if expected_revision != revision_of(current_raw):
        raise EditorError("Konfiguration wurde inzwischen geändert. Bitte neu laden.")
    config = json.loads(current_raw)
    matched = [user for user in config.get("users", []) if user.get("key") == user_key]
    if len(matched) != 1:
        raise EditorError("Benutzer nicht gefunden")
    matched[0].pop("panel_pin_hash", None)
    parse_configuration(config)
    return json.dumps(config, ensure_ascii=False, indent=2)
