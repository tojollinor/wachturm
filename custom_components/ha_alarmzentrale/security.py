"""Fail-closed, Home Assistant-independent access control for alarm commands.

A Homematic keypad validates its OWN PIN. Only device/channel events are mapped
here; keypad PINs are never requested or saved. Local panel PINs are separate,
optional credentials and are stored only as salted PBKDF2 digests.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac
import base64
import binascii
import hmac
import math
import os
import re
import time
from typing import Callable

ALLOWED_OPERATIONS = frozenset({"arm", "disarm", "mute", "acknowledge"})
ALLOWED_EVENTS = frozenset({"homematic.keypress", "homematic.impulse"})
HASH_ITERATIONS = 480_000
_MAX_PIN_LENGTH = 128
_HASH_RE = re.compile(r"^pbkdf2_sha256\$(\d+)\$([a-zA-Z0-9_-]+)\$([a-zA-Z0-9_-]+)$")


class AccessDenied(Exception):
    """Access denied; details and credentials must not be echoed to HA."""


def make_pin_hash(pin: str) -> str:
    if not isinstance(pin, str) or not (4 <= len(pin) <= _MAX_PIN_LENGTH) or not pin.isdigit():
        raise ValueError("Local panel PIN must contain 4-128 digits")
    salt = os.urandom(16)
    digest = pbkdf2_hmac("sha256", pin.encode(), salt, HASH_ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        HASH_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_pin_hash(encoded: str) -> None:
    match = _HASH_RE.fullmatch(encoded) if isinstance(encoded, str) else None
    if match is None:
        raise ValueError("PIN must be a salted pbkdf2_sha256 hash, never plaintext")
    try:
        iterations, salt, digest = int(match[1]), _decode(match[2]), _decode(match[3])
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Malformed PIN hash") from exc
    if not 200_000 <= iterations <= 1_000_000 or len(salt) < 16 or len(digest) != 32:
        raise ValueError("Invalid PIN hash parameters")


def matches_pin(pin: str | None, encoded: str) -> bool:
    if not isinstance(pin, str) or not 1 <= len(pin) <= _MAX_PIN_LENGTH or not pin.isdigit():
        return False
    match = _HASH_RE.fullmatch(encoded)
    if match is None:
        return False
    candidate = pbkdf2_hmac("sha256", pin.encode(), _decode(match[2]), int(match[1]))
    return hmac.compare_digest(candidate, _decode(match[3]))


@dataclass(frozen=True, slots=True)
class Grant:
    area: str
    operations: tuple[str, ...]
    modes: tuple[str, ...] = ()  # Only restricts arming; empty means any defined mode.

    def __post_init__(self):
        if not self.area or not self.operations or not set(self.operations) <= ALLOWED_OPERATIONS:
            raise ValueError("Invalid area grant")
        if len(set(self.operations)) != len(self.operations) or len(set(self.modes)) != len(self.modes):
            raise ValueError("Duplicate permissions/modes")

    def permits(self, area: str, operation: str, mode: str | None) -> bool:
        return (self.area == area and operation in self.operations and
                (operation != "arm" or not self.modes or mode in self.modes))


@dataclass(frozen=True, slots=True)
class Role:
    key: str
    grants: tuple[Grant, ...]


@dataclass(frozen=True, slots=True)
class Window:
    """Local HA timezone; weekday 0=Monday; half-open, midnight-crossing allowed."""
    weekdays: tuple[int, ...]
    start_minute: int
    end_minute: int

    def __post_init__(self):
        if (not self.weekdays or any(type(day) is not int or day not in range(7) for day in self.weekdays)
                or len(set(self.weekdays)) != len(self.weekdays) or not 0 <= self.start_minute < 1440
                or not 0 <= self.end_minute < 1440 or self.start_minute == self.end_minute):
            raise ValueError("Invalid time window")

    def contains(self, now: datetime) -> bool:
        minute = now.hour * 60 + now.minute
        weekday = now.weekday()
        if self.start_minute < self.end_minute:
            return weekday in self.weekdays and self.start_minute <= minute < self.end_minute
        return ((weekday in self.weekdays and minute >= self.start_minute) or
                ((weekday - 1) % 7 in self.weekdays and minute < self.end_minute))


@dataclass(frozen=True, slots=True)
class AlarmUser:
    key: str
    name: str
    enabled: bool = True
    roles: tuple[str, ...] = ()
    grants: tuple[Grant, ...] = ()
    ha_user_ids: tuple[str, ...] = ()
    panel_pin_hash: str | None = None
    time_windows: tuple[Window, ...] = ()

    def __post_init__(self):
        if not self.key or not self.name or len(set(self.ha_user_ids)) != len(self.ha_user_ids):
            raise ValueError("Invalid alarm user")
        if self.panel_pin_hash is not None:
            validate_pin_hash(self.panel_pin_hash)


@dataclass(frozen=True, slots=True)
class EventBinding:
    """Explicit physical device ID + address + event channel, not a HA state value."""
    key: str
    user: str
    event_type: str
    device_id: str
    address: str
    event_action: str  # e.g. press_short, press_long, sequence_ok
    channel: int
    area: str
    operation: str
    mode: str | None = None
    interface_id: str | None = None
    scope: str = "area"
    input_type: str = "homematic"
    entity_id: str = ""
    trigger_states: tuple[str, ...] = ("on",)
    event_data: dict = field(default_factory=dict)


    def __post_init__(self):
        if self.input_type != "homematic":
            if (self.input_type not in {"entity", "switch", "event"} or not self.key or not self.user
                    or self.scope != "mode" or self.operation not in {"arm", "disarm"}
                    or (self.operation == "arm" and not self.mode)):
                raise ValueError("Invalid entity/event control")
            if self.input_type in {"entity", "switch"}:
                if not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", self.entity_id) or not self.trigger_states or any(v in {"unknown", "unavailable", ""} for v in self.trigger_states):
                    raise ValueError("Explicit entity and available trigger states required")
            elif not self.event_type or self.event_type == "state_changed" or not isinstance(self.event_data, dict) or not self.event_data:
                raise ValueError("Event control requires identifying event fields")
            return
        if (not all((self.key, self.user, self.device_id, self.address, self.event_action, self.area))
                or self.event_type not in ALLOWED_EVENTS or self.operation not in ALLOWED_OPERATIONS
                or type(self.channel) is not int or self.channel < 0
                or self.scope not in {"area", "mode"}
                or (self.scope == "mode" and self.operation not in {"arm", "disarm"})
                or (self.operation == "arm" and self.mode is None)
                or (self.mode is not None and not self.mode)
                or (self.interface_id is not None and not self.interface_id)):
            raise ValueError("Invalid device event binding")

    @property
    def match_key(self) -> tuple:
        if self.input_type != "homematic":
            return (self.input_type, self.entity_id, self.event_type, repr(sorted(self.event_data.items())), self.trigger_states, self.operation)
        return (self.event_type, self.device_id, self.address, self.event_action,
                self.channel, self.interface_id)

    def matches(self, event_type: str, data: dict) -> bool:
        if self.input_type in {"entity", "switch"}:
            return event_type == "alarmzentrale_entity" and data.get("entity_id") == self.entity_id and data.get("state") not in {None,"unknown","unavailable"} and (data.get("state") in (("on", "off") if self.input_type == "switch" else self.trigger_states) or self.input_type == "entity" and "*" in self.trigger_states)
        if self.input_type == "event":
            return event_type == self.event_type and all(data.get(k) == v for k, v in self.event_data.items())
        # The integration does not infer a successful PIN from a generic event.
        return (event_type == self.event_type and data.get("device_id") == self.device_id
                and data.get("address") == self.address
                and data.get("type") == self.event_action
                and type(data.get("subtype")) is int and data["subtype"] == self.channel
                and (self.interface_id is None or data.get("interface_id") == self.interface_id))


@dataclass(frozen=True, slots=True)
class SecuritySpec:
    roles: tuple[Role, ...] = ()
    users: tuple[AlarmUser, ...] = ()
    bindings: tuple[EventBinding, ...] = ()

    def __post_init__(self):
        for collection, label in ((self.roles, "role"), (self.users, "user"), (self.bindings, "binding")):
            keys = [x.key for x in collection]
            if len(set(keys)) != len(keys):
                raise ValueError(f"Duplicate {label} keys")
        user_keys = {user.key for user in self.users}
        role_keys = {role.key for role in self.roles}
        for user in self.users:
            if any(role not in role_keys for role in user.roles):
                raise ValueError("Unknown user role")
        ha_ids = [id for user in self.users for id in user.ha_user_ids]
        if len(set(ha_ids)) != len(ha_ids):
            raise ValueError("One HA account cannot identify two alarm users")
        pin_hashes = [u.panel_pin_hash for u in self.users if u.panel_pin_hash]
        if len(set(pin_hashes)) != len(pin_hashes):
            raise ValueError("Duplicate panel credential")
        for binding in self.bindings:
            if binding.user not in user_keys:
                raise ValueError("Device binding refers to unknown user")
        matching = [b.match_key for b in self.bindings]
        if len(set(matching)) != len(matching):
            raise ValueError("Ambiguous device bindings")
        # A wildcard interface and a specific interface must not both accept
        # the same device/channel event.
        for index, a in enumerate(self.bindings):
            for b in self.bindings[index + 1:]:
                if a.input_type == b.input_type == "homematic" and a.match_key[:5] == b.match_key[:5] and (a.interface_id is None or b.interface_id is None):
                    raise ValueError("Overlapping device event bindings")

    def validate_areas(self, areas) -> None:
        available = {a.key: {m.key for m in a.modes} for a in areas}
        for grant in [g for role in self.roles for g in role.grants] + [g for user in self.users for g in user.grants]:
            if grant.area not in available or not set(grant.modes) <= available[grant.area]:
                raise ValueError("Grant references unknown area/mode")
        for binding in self.bindings:
            if binding.scope == "area" and (binding.area not in available or (binding.mode is not None and binding.mode not in available[binding.area])):
                raise ValueError("Device binding references unknown area/mode")


@dataclass(slots=True)
class Authorization:
    """Auth decisions are independent of the alarm engine and fail closed."""
    spec: SecuritySpec
    epoch: Callable[[], float] = time.time
    local_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    failed_attempts: int = 0
    lockout_until: float = 0.0
    # An invalid local panel PIN cannot be attributed to a known principal.
    max_failures: int = 5
    lockout_seconds: int = 300

    def _enabled(self, user: AlarmUser) -> bool:
        return user.enabled and (not user.time_windows or any(w.contains(self.local_now()) for w in user.time_windows))

    def _has_right(self, user: AlarmUser, area: str, operation: str, mode: str | None) -> bool:
        grants = list(user.grants)
        grants.extend(grant for role in self.spec.roles if role.key in user.roles for grant in role.grants)
        return any(grant.permits(area, operation, mode) for grant in grants)

    def _check(self, user: AlarmUser, area: str, operation: str, mode: str | None) -> AlarmUser:
        if not self._enabled(user) or not self._has_right(user, area, operation, mode):
            raise AccessDenied("Operation not authorized")
        return user

    def from_ha_user(self, ha_user_id: str | None, area: str, operation: str, mode: str | None = None) -> AlarmUser:
        if not isinstance(ha_user_id, str) or not ha_user_id:
            raise AccessDenied("Authenticated HA user required")
        matches = [user for user in self.spec.users if ha_user_id in user.ha_user_ids]
        if len(matches) != 1:
            raise AccessDenied("HA account has no alarm identity")
        return self._check(matches[0], area, operation, mode)

    def identify_panel_pin(self, pin: str | None) -> AlarmUser:
        if self.epoch() < self.lockout_until:
            raise AccessDenied("Panel temporarily locked")
        # Do not log the code; compare only with provisioned hashes.
        matches = [u for u in self.spec.users if u.panel_pin_hash and matches_pin(pin, u.panel_pin_hash)]
        if len(matches) != 1:
            self.failed_attempts += 1
            if self.failed_attempts >= self.max_failures:
                self.lockout_until = self.epoch() + self.lockout_seconds
                self.failed_attempts = 0
            raise AccessDenied("Invalid panel credential")
        user = matches[0]
        if not self._enabled(user):
            raise AccessDenied("User disabled or outside allowed time")
        self.failed_attempts = 0
        return user

    def from_panel_pin(self, pin: str | None, area: str, operation: str, mode: str | None = None) -> AlarmUser:
        return self._check(self.identify_panel_pin(pin), area, operation, mode)

    def from_event(self, event_type: str, data: dict) -> tuple[AlarmUser, EventBinding] | None:
        if not isinstance(data, dict):
            return None
        matching = [b for b in self.spec.bindings if b.matches(event_type, data)]
        if not matching:
            return None
        if len(matching) != 1:
            raise AccessDenied("Ambiguous device event")
        binding = matching[0]
        user = next(u for u in self.spec.users if u.key == binding.user)
        if binding.input_type == "switch":
            binding = replace(binding, operation="arm" if data["state"] == "on" else "disarm")
        if not self._enabled(user):
            raise AccessDenied("User disabled or outside allowed time")
        if binding.scope == "area":
            self._check(user, binding.area, binding.operation, binding.mode)
        return user, binding

    def snapshot(self) -> dict:
        """Persist throttling without storing a PIN or any raw credential."""
        return {"schema": 1, "failed_attempts": self.failed_attempts, "lockout_until": self.lockout_until}

    def restore(self, saved: dict) -> None:
        if not isinstance(saved, dict) or saved.get("schema") != 1:
            raise ValueError("Invalid authorization snapshot")
        failures, until = saved.get("failed_attempts"), saved.get("lockout_until")
        if (type(failures) is not int or not 0 <= failures < self.max_failures
                or type(until) not in (int, float) or not math.isfinite(until) or until < 0):
            raise ValueError("Invalid authorization lockout data")
        self.failed_attempts = failures
        self.lockout_until = float(until)
