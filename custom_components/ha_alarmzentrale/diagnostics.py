"""Pure-Python, Home Assistant-independent diagnostics.

This module does not trigger actuators or authorize alarm operations. Consumers must
explicitly configure timing, thresholds and retention. Events are observations,
not assertions of a known root cause. All timestamps are timezone-aware UTC.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Deque, Literal


def require_time(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamp must have timezone")
    return moment.astimezone(timezone.utc)


class Connectivity(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class Battery(str, Enum):
    GOOD = "good"
    LOW = "low"
    EMPTY = "empty"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Settings:
    offline_grace: timedelta
    flap_window: timedelta
    flap_count: int
    recovery_duration: timedelta
    common_outage_window: timedelta
    common_outage_count: int
    history_limit: int
    battery_freshness: timedelta

    def __post_init__(self) -> None:
        for name in ("offline_grace", "flap_window", "recovery_duration", "common_outage_window", "battery_freshness"):
            if getattr(self, name) < timedelta(0):
                raise ValueError(f"{name} must not be negative")
        if self.flap_window == timedelta(0) or self.common_outage_window == timedelta(0):
            raise ValueError("observation windows must be positive")
        if self.flap_count < 2 or self.common_outage_count < 2 or self.history_limit < 1:
            raise ValueError("counts and history_limit out of range")


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str
    source: str
    detail: str = ""


@dataclass(frozen=True)
class RadioSample:
    at: datetime
    interface: str
    duty_cycle: float | None
    carrier_sense: float | None


@dataclass(frozen=True)
class RssiSample:
    at: datetime
    device: str
    rssi_device: float | None
    rssi_peer: float | None


@dataclass
class Device:
    connectivity: Connectivity = Connectivity.UNKNOWN
    battery: Battery = Battery.UNKNOWN
    battery_at: datetime | None = None
    last_seen: datetime | None = None
    offline_since: datetime | None = None
    online_since: datetime | None = None
    losses: Deque[datetime] = field(default_factory=deque)
    unstable: bool = False
    issued_offline: bool = False
    issued_recovery: bool = False


class Diagnostics:
    """Deterministic event processor; all policy choices remain outside this core."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.devices: dict[str, Device] = {}
        self.history: deque[Event] = deque(maxlen=settings.history_limit)
        self.rssi: dict[str, deque[RssiSample]] = {}
        self.radio: dict[str, deque[RadioSample]] = {}
        self._latest_at: datetime | None = None
        self._common_episode: frozenset[str] = frozenset()

    def _check(self, at: datetime) -> datetime:
        now = require_time(at)
        if self._latest_at is not None and now < self._latest_at:
            raise ValueError("out-of-order timestamp; reconcile source before applying")
        self._latest_at = now
        return now

    def _emit(self, at: datetime, kind: str, source: str, detail: str = "") -> Event:
        event = Event(at, kind, source, detail)
        self.history.append(event)
        return event

    def battery_update(self, device: str, value: Battery | str, at: datetime) -> list[Event]:
        at = self._check(at)
        state = self.devices.setdefault(device, Device())
        value = Battery(value)
        if state.battery == value:
            state.battery_at = at
            return []
        state.battery = value
        state.battery_at = at
        return [self._emit(at, "battery", device, value.value)]

    def connectivity_update(self, device: str, value: Connectivity | str, at: datetime) -> list[Event]:
        at = self._check(at)
        state = self.devices.setdefault(device, Device())
        value = Connectivity(value)
        if state.connectivity == value:
            if value == Connectivity.ONLINE:
                state.last_seen = at
            return []
        previous = state.connectivity
        state.connectivity = value
        events = [self._emit(at, "connectivity", device, value.value)]
        if value == Connectivity.ONLINE:
            state.online_since = at
            state.offline_since = None
            state.last_seen = at
            state.issued_offline = False
            state.issued_recovery = False
        else:
            state.online_since = None
            state.issued_recovery = False
            if value == Connectivity.OFFLINE:
                state.offline_since = at
                state.issued_offline = False
                if previous == Connectivity.ONLINE:
                    state.losses.append(at)
                    self._trim_losses(state, at)
                    if len(state.losses) >= self.settings.flap_count and not state.unstable:
                        state.unstable = True
                        events.append(self._emit(at, "unstable", device, "frequent connectivity losses"))
            else:
                state.offline_since = None
                state.issued_offline = False
        return events

    def _trim_losses(self, state: Device, at: datetime) -> None:
        while state.losses and at - state.losses[0] > self.settings.flap_window:
            state.losses.popleft()

    def _outage_evidence(self, state: Device, at: datetime) -> str:
        if state.battery_at is None or at - state.battery_at > self.settings.battery_freshness:
            return "battery evidence missing or stale; cause unknown"
        if state.battery in (Battery.LOW, Battery.EMPTY):
            return "previous low-battery indication; cause unconfirmed"
        return "previous battery indication non-low; cause unknown"

    def evaluate(self, at: datetime) -> list[Event]:
        at = self._check(at)
        events: list[Event] = []
        for device, state in self.devices.items():
            self._trim_losses(state, at)
            if state.connectivity == Connectivity.OFFLINE and state.offline_since is not None:
                if not state.issued_offline and at - state.offline_since >= self.settings.offline_grace:
                    state.issued_offline = True
                    kind = "offline_unstable" if state.unstable else "offline"
                    events.append(self._emit(at, kind, device, self._outage_evidence(state, at)))
            if state.connectivity == Connectivity.ONLINE and state.unstable and state.online_since is not None:
                if at - state.online_since >= self.settings.recovery_duration:
                    state.unstable = False
                    state.losses.clear()
                    events.append(self._emit(at, "stable_recovery", device))
        recent_offline = frozenset(device for device, state in self.devices.items()
                                   if state.connectivity == Connectivity.OFFLINE
                                   and state.offline_since is not None
                                   and at - state.offline_since <= self.settings.common_outage_window)
        if len(recent_offline) >= self.settings.common_outage_count:
            if not self._common_episode or not recent_offline.issubset(self._common_episode):
                events.append(self._emit(at, "common_outage_suspected", "system", ",".join(sorted(recent_offline))))
            self._common_episode = self._common_episode | recent_offline
        else:
            self._common_episode = frozenset()
        return events

    def rssi_update(self, device: str, at: datetime, *, rssi_device: float | None, rssi_peer: float | None) -> None:
        at = self._check(at)
        self.rssi.setdefault(device, deque(maxlen=self.settings.history_limit)).append(
            RssiSample(at, device, rssi_device, rssi_peer))

    def radio_update(self, interface: str, at: datetime, *, duty_cycle: float | None, carrier_sense: float | None) -> None:
        at = self._check(at)
        self.radio.setdefault(interface, deque(maxlen=self.settings.history_limit)).append(
            RadioSample(at, interface, duty_cycle, carrier_sense))

    def snapshot(self) -> dict:
        """JSON-serializable snapshot for an adapter-controlled persistence layer."""
        return {
            "schema": 1,
            "devices": {
                key: {
                    "connectivity": value.connectivity.value,
                    "battery": value.battery.value,
                    "battery_at": value.battery_at.isoformat() if value.battery_at else None,
                    "last_seen": value.last_seen.isoformat() if value.last_seen else None,
                    "offline_since": value.offline_since.isoformat() if value.offline_since else None,
                    "online_since": value.online_since.isoformat() if value.online_since else None,
                    "losses": [dt.isoformat() for dt in value.losses],
                    "unstable": value.unstable,
                    "issued_offline": value.issued_offline,
                }
                for key, value in self.devices.items()
            },
        }
