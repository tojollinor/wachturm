"""Framework-independent alarm and sensor-zone state machine.

No direct I/O, authentication or actuator control. Real sensors
are read by the HA adapter. A zone is active only in its assigned arming modes.
"""

from __future__ import annotations
from .event_context import correlated, current_event

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Callable, Iterable
import time


class Phase(StrEnum):
    DISARMED = "disarmed"
    ARMING = "arming"
    ARMED = "armed"
    PENDING = "pending"
    TRIGGERED = "triggered"


class Observation(StrEnum):
    NORMAL = "normal"
    ACTIVE = "active"
    FAULT = "fault"


class InitialPolicy(StrEnum):
    BLOCK = "block"
    DEFER = "defer"
    IGNORE = "ignore"


class FaultPolicy(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    ALARM = "alarm"


class AckPolicy(StrEnum):
    MANUAL = "manual"
    AUTO_CLEAR = "auto_clear"


_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Per-area HA action. Timed actions must be bounded turn_on/off services.

    Never infer a reverse action for an arbitrary service or turn off an output
    that was already on when this integration tried to start the action.
    """

    key: str
    name: str
    domain: str
    service: str
    entity_ids: tuple[str, ...] = ()
    data: dict = field(default_factory=dict)
    repeat_on_retrigger: bool = False
    timed: bool = False
    when: str = "alarm"
    notification: dict = field(default_factory=dict)
    mode_keys: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.name:
            raise ValueError("Action needs key and name")
        if self.mode_keys is not None and (not isinstance(self.mode_keys,tuple) or any(not isinstance(k,str) or not k for k in self.mode_keys) or len(set(self.mode_keys))!=len(self.mode_keys)):
            raise ValueError("Invalid action modes")
        if not isinstance(self.notification, dict) or set(self.notification) - {"platform", "critical", "persistent", "buttons", "tts", "tts_max", "tts_repeat_seconds", "tts_text", "screen_on", "open_alarm", "alarm_stream"}:
            raise ValueError("Invalid mobile notification options")
        if self.notification:
            if self.domain != "notify" or self.notification.get("platform") not in {"standard", "ios", "android"}:
                raise ValueError("Invalid notification platform")
            if any(type(v) is not bool for k, v in self.notification.items() if k not in {"platform", "tts_repeat_seconds", "tts_text"}):
                raise ValueError("Notification switches must be boolean")
            if not isinstance(self.notification.get("tts_text",""),str) or len(self.notification.get("tts_text",""))>10000:
                raise ValueError("Invalid TTS text")
            if self.notification.get("platform") != "standard" and (self.when != "alarm" or not self.service.startswith("mobile_app_")):
                raise ValueError("Mobile alarm profiles require an alarm action and Companion recipient")
        interval=self.notification.get("tts_repeat_seconds",0)
        if type(interval) is not int or interval != 0 and not 10 <= interval <= 3600:
            raise ValueError("TTS interval must be 0 or 10–3600 seconds")
        if self.when not in {"alarm", "armed", "disarmed", "battery", "offline", "unstable"}:
            raise ValueError("Invalid action event")
        if self.when != "alarm" and self.timed:
            raise ValueError("Timed outputs are only supported for alarm actions")
        if self.when in {"battery", "offline", "unstable"} and self.domain not in {"notify", "script"}:
            raise ValueError("Diagnostic actions must use notify or script")
        if not _SERVICE_NAME.fullmatch(self.domain) or not _SERVICE_NAME.fullmatch(self.service):
            raise ValueError("Action service must be a simple domain.service name")
        if not isinstance(self.data, dict):
            raise ValueError("Action data must be a dictionary")
        if any(not isinstance(e, str) or "." not in e for e in self.entity_ids):
            raise ValueError("Invalid action target entity")
        if len(self.entity_ids) != len(set(self.entity_ids)):
            raise ValueError("Duplicate action target entity")
        if type(self.repeat_on_retrigger) is not bool or type(self.timed) is not bool:
            raise ValueError("Invalid action flags")
        if self.timed and (
            self.domain not in {"light", "switch", "siren"}
            or self.service != "turn_on"
            or not self.entity_ids
            or any(not entity_id.startswith(self.domain + ".") for entity_id in self.entity_ids)
        ):
            raise ValueError("Timed actions require light/switch/siren.turn_on with explicit entities")
        if not self.entity_ids and not self.data:
            raise ValueError("An action must have a target or service data")


class AlarmError(Exception):
    """An invalid or unsafe transition was requested."""


@dataclass(frozen=True, slots=True)
class ModeSpec:
    key: str
    name: str
    exit_delay_seconds: int = 0
    delay_on_mode_change: bool = False
    mute_after_seconds: int | None = 1800
    restart_timer_on_retrigger: bool = True
    standby_seconds: int = 0

    def __post_init__(self) -> None:
        if not self.key or not self.name or self.exit_delay_seconds < 0 or type(self.standby_seconds) is not int or self.standby_seconds < 0:
            raise ValueError("Mode requires a name/key and a nonnegative exit delay")
        if self.mute_after_seconds is not None and self.mute_after_seconds <= 0:
            raise ValueError("Mute period must be positive or None")


@dataclass(frozen=True, slots=True)
class ZoneModeSpec:
    mode: str
    entry_delay_seconds: int = 0
    follow_entry_delay: bool = False
    initial_policy: InitialPolicy | None = None
    arm_on_close: bool = False

    def __post_init__(self) -> None:
        if not self.mode or self.entry_delay_seconds < 0:
            raise ValueError("Zone mode must be nonempty, delay nonnegative")
        if type(self.arm_on_close) is not bool:
            raise ValueError("arm_on_close must be boolean")
        if self.initial_policy is not None and not isinstance(self.initial_policy, InitialPolicy):
            raise ValueError("Invalid mode-specific initial policy")


@dataclass(frozen=True, slots=True)
class ZoneSpec:
    key: str
    name: str
    entity_id: str
    mode_policies: tuple[ZoneModeSpec, ...]
    active_states: tuple[str, ...] = ("on",)
    normal_states: tuple[str, ...] = ("off",)
    initial_policy: InitialPolicy = InitialPolicy.BLOCK
    fault_policy: FaultPolicy = FaultPolicy.BLOCK
    attribute: str | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.name or "." not in self.entity_id or not self.mode_policies:
            raise ValueError("Zone requires key, name, entity_id and mode policies")
        if not self.active_states or not self.normal_states:
            raise ValueError("Zone requires active and normal states")
        if self.attribute is not None and (not isinstance(self.attribute, str) or not self.attribute.strip()):
            raise ValueError("Zone attribute must be a nonempty string")
        a, n = set(self.active_states), set(self.normal_states)
        if a & n or {"unknown", "unavailable"} & (a | n):
            raise ValueError("Zone state sets overlap or include unavailable/unknown")
        keys = [p.mode for p in self.mode_policies]
        if len(keys) != len(set(keys)):
            raise ValueError("Zone cannot repeat a mode policy")
        if not isinstance(self.initial_policy, InitialPolicy):
            raise ValueError("Invalid initial policy")
        if not isinstance(self.fault_policy, FaultPolicy):
            raise ValueError("Invalid fault policy")

    def policy(self, mode: str | None) -> ZoneModeSpec | None:
        if mode is None:
            return None
        return next((p for p in self.mode_policies if p.mode == mode), None)

    def classify(self, state: str | None, attributes: dict | None = None) -> Observation:
        if self.attribute is not None:
            # The entity itself must still be available. An absent or complex
            # attribute is a fault, never the configured normal state.
            if state in (None, "unknown", "unavailable") or not isinstance(attributes, dict):
                return Observation.FAULT
            value = attributes.get(self.attribute)
            if isinstance(value, bool):
                state = str(value).lower()
            elif isinstance(value, (int, float, str)) and value is not None:
                state = str(value)
            else:
                return Observation.FAULT
        if state in self.active_states:
            return Observation.ACTIVE
        if state in self.normal_states:
            return Observation.NORMAL
        return Observation.FAULT


@dataclass(frozen=True, slots=True)
class AreaSpec:
    key: str
    name: str
    modes: tuple[ModeSpec, ...]
    zones: tuple[ZoneSpec, ...] = ()
    recovery_fault_grace_seconds: int = 30
    actions: tuple[ActionSpec, ...] = ()
    ack_policy: AckPolicy = AckPolicy.MANUAL

    def __post_init__(self) -> None:
        if not self.key or not self.name or not self.modes:
            raise ValueError("Area requires key, name, modes")
        keys = [mode.key for mode in self.modes]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate modes within area")
        zone_keys = [zone.key for zone in self.zones]
        if len(zone_keys) != len(set(zone_keys)):
            raise ValueError("Duplicate zones within area")
        identity = [(zone.entity_id, zone.attribute) for zone in self.zones]
        if len(identity) != len(set(identity)):
            raise ValueError("The same entity and attribute cannot be configured twice within one area")
        for zone in self.zones:
            if any(p.mode not in keys for p in zone.mode_policies):
                raise ValueError(f"Zone {zone.key} refers to unknown mode")
        if self.recovery_fault_grace_seconds < 0:
            raise ValueError("Recovery grace cannot be negative")
        action_keys = [action.key for action in self.actions]
        if len(action_keys) != len(set(action_keys)):
            raise ValueError("Duplicate actions in area")
        if not isinstance(self.ack_policy, AckPolicy):
            raise ValueError("Invalid acknowledgement policy")

    def mode(self, key: str) -> ModeSpec:
        for mode in self.modes:
            if mode.key == key:
                return mode
        raise AlarmError(f"Unknown mode: {key}")


@dataclass(frozen=True, slots=True)
class AlarmEvent:
    area: str
    kind: str
    mode: str | None
    when: float
    info: str = ""
    event_id: str | None = None


@dataclass(slots=True)
class ZoneRuntime:
    observation: Observation = Observation.FAULT
    verified: bool = False
    seen_sample: bool = False
    deferred_until_normal: bool = False
    ignored_for_arming: bool = False
    handled_active: bool = False
    handled_fault: bool = False


class AreaController:
    """One area, one active mode, zones and observable state transitions."""

    def __init__(self, spec: AreaSpec, *, clock: Callable[[], float] = time.time) -> None:
        self.spec = spec
        self._clock = clock
        self.mode: str | None = None
        self.phase = Phase.DISARMED
        self.phase_deadline: float | None = None
        self.alarm_deadline: float | None = None
        self.alarm_latched = False
        self.alarm_started_at: float | None = None
        self.action_window_active = False
        self.trigger_count = 0
        self.pending_zone: str | None = None
        self.recovery_fault_until: float | None = None
        self.reconciling = False
        self.arming_block_reason = ""
        self.zones = {zone.key: ZoneRuntime() for zone in spec.zones}
        self.last_event: AlarmEvent | None = None
        self.history: list[AlarmEvent] = []
        self.alarm_sources: set[str] = set()
        self._listeners: list[Callable[[AlarmEvent], None]] = []
        # Optional Block-3 diagnostic preflight injected by the HA adapter.
        self.diagnostic_guard: Callable[[str], tuple[tuple[str, str], ...]] | None = None

    def subscribe(self, callback: Callable[[AlarmEvent], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _emit(self, kind: str, info: str = "", *, mode: str | None = None) -> None:
        evt = AlarmEvent(self.spec.key, kind, mode or self.mode, self._clock(), info, current_event.get())
        self.last_event = evt
        self.history.append(evt)
        if len(self.history) > 250:
            del self.history[:-250]
        for listener in tuple(self._listeners):
            listener(evt)

    def _zones_for(self, mode: str | None) -> tuple[ZoneSpec, ...]:
        return tuple(zone for zone in self.spec.zones if zone.policy(mode) is not None
                     and not (mode == self.mode and self.phase != Phase.DISARMED
                              and self.zones[zone.key].ignored_for_arming))

    def _initial_policy(self, zone: ZoneSpec, mode: str) -> InitialPolicy:
        policy = zone.policy(mode)
        return policy.initial_policy if policy is not None and policy.initial_policy is not None else zone.initial_policy

    @property
    def monitoring_ready(self) -> bool:
        """Never claim readiness while required zones are unverified."""
        return not self.reconciling and all(
            self.zones[zone.key].verified for zone in self._zones_for(self.mode)
        )

    def _fault_under_grace(self) -> bool:
        return self.recovery_fault_until is not None and self._clock() < self.recovery_fault_until

    def _preflight(self, mode: str) -> set[str]:
        """Never change mode until all blocking zones have been checked."""
        if self.diagnostic_guard is not None:
            for device, decision in self.diagnostic_guard(mode):
                if decision == "block":
                    raise AlarmError(f"Diagnostic reachability not verified: {device}")
        deferred: set[str] = set()
        for zone in self._zones_for(mode):
            runtime = self.zones[zone.key]
            if runtime.observation == Observation.FAULT:
                if zone.fault_policy == FaultPolicy.BLOCK:
                    raise AlarmError(f"Zone not available: {zone.name}")
            elif runtime.observation == Observation.ACTIVE:
                if self._initial_policy(zone, mode) == InitialPolicy.BLOCK:
                    raise AlarmError(f"Zone already active: {zone.name}")
                deferred.add(zone.key)
        return deferred

    def arm(self, mode_key: str) -> None:
        target = self.spec.mode(mode_key)
        if self.mode == mode_key and self.phase != Phase.DISARMED:
            return
        deferred = self._preflight(mode_key)
        was_armed = self.phase != Phase.DISARMED
        self.mode = mode_key
        self.pending_zone = None
        self.arming_block_reason = ""
        for zone in self.spec.zones:
            rt = self.zones[zone.key]
            rt.ignored_for_arming = zone.key in deferred and self._initial_policy(zone, mode_key) == InitialPolicy.IGNORE
            rt.deferred_until_normal = zone.key in deferred and not rt.ignored_for_arming
            # An active zone accepted on arming must not trip until it clears.
            rt.handled_active = False
            rt.handled_fault = False
        if self.alarm_latched:
            self.phase = Phase.TRIGGERED
            self.phase_deadline = None
            self._emit("mode_changed_with_alarm")
            return
        delay = target.exit_delay_seconds if (not was_armed or target.delay_on_mode_change) else 0
        self.phase = Phase.ARMING if delay else Phase.ARMED
        self.phase_deadline = self._clock() + delay if delay else None
        self._emit("arming" if delay else "armed")
        # WARN is a visible fault state, never an implicit healthy sensor.
        for zone in self._zones_for(self.mode):
            if zone.fault_policy == FaultPolicy.WARN and self.zones[zone.key].observation == Observation.FAULT:
                self._emit("zone_fault_warning", zone.key)
        if not delay:
            self._evaluate_fault_alarms()
            self._evaluate_diagnostic_arming()

    def _evaluate_diagnostic_arming(self) -> None:
        """Only user-enabled warning/alarm policies after completed arming."""
        if self.mode is None or self.phase != Phase.ARMED or self.diagnostic_guard is None:
            return
        for device, decision in self.diagnostic_guard(self.mode):
            if decision == "warn":
                self._emit("diagnostic_fault_warning", device)
            elif decision == "alarm" and not self.alarm_latched:
                self.trigger(f"diagnostic_arm_fault:{device}")

    def disarm(self) -> None:
        if self.phase == Phase.DISARMED:
            return
        previous_mode = self.mode
        self.mode = None
        self.phase = Phase.DISARMED
        self.phase_deadline = None
        self.pending_zone = None
        self.alarm_deadline = None
        self.alarm_latched = False
        self.alarm_started_at = None
        self.action_window_active = False
        self.trigger_count = 0
        self.alarm_sources.clear()
        self.reconciling = False
        self.arming_block_reason = ""
        self.recovery_fault_until = None
        for runtime in self.zones.values():
            runtime.deferred_until_normal = False
            runtime.ignored_for_arming = False
            runtime.handled_active = False
            runtime.handled_fault = False
        self._emit("disarmed",mode=previous_mode)

    def begin_entry_delay(self, zone_key: str, seconds: int) -> bool:
        if seconds <= 0:
            return self.trigger(zone_key)
        if self.phase != Phase.ARMED:
            return False
        self.phase = Phase.PENDING
        self.pending_zone = zone_key
        self.phase_deadline = self._clock() + seconds
        self._emit("entry_delay_started", zone_key)
        return True

    def trigger(self, source: str = "manual") -> bool:
        if self.mode is None or self.phase in (Phase.DISARMED, Phase.ARMING):
            return False
        mode = self.spec.mode(self.mode)
        fresh = not self.alarm_latched
        if fresh:self.alarm_started_at=self._clock()
        self.alarm_latched = True
        self.phase = Phase.TRIGGERED
        self.pending_zone = None
        self.phase_deadline = None
        self.trigger_count += 1
        self.alarm_sources.add(source)
        if fresh or mode.restart_timer_on_retrigger:
            self.action_window_active = True
            self.alarm_deadline = (
                self._clock() + mode.mute_after_seconds if mode.mute_after_seconds is not None else None
            )
        self._emit("alarm_started" if fresh else "alarm_retriggered", source)
        return True

    def mute_actions(self) -> None:
        """Stop the shared *mode* action window, preserving the alarm latch."""
        if not self.alarm_latched or not self.action_window_active:
            return
        self.action_window_active = False
        self.alarm_deadline = None
        self._emit("actions_muted")

    def acknowledge(self, *, automatic: bool = False) -> None:
        if not self.alarm_latched:
            return
        self.alarm_latched = False
        self.alarm_started_at = None
        self.action_window_active = False
        self.alarm_deadline = None
        self.trigger_count = 0
        self.alarm_sources.clear()
        self.phase = Phase.ARMED if self.mode else Phase.DISARMED
        self._emit("alarm_acknowledged", "automatic" if automatic else "manual")

    @correlated
    def update_sensor(self, entity_id: str, state: str | None, *, initial: bool = False,
                      attributes: dict | None = None) -> None:
        """Ingest observed HA state; unknown/unavailable/None are FAULT, never NORMAL.

        initial means baseline read after HA startup. It is NOT a fresh edge;
        however a confirmed active state still trips a zone armed before a
        restart unless it was intentionally deferred or already handled.
        """
        for zone in self.spec.zones:
            if zone.entity_id != entity_id:
                continue
            rt = self.zones[zone.key]
            next_obs = zone.classify(state, attributes)
            old_obs = rt.observation
            first_sample = not rt.seen_sample
            rt.observation = next_obs
            rt.seen_sample = True
            rt.verified = next_obs != Observation.FAULT
            if next_obs == Observation.NORMAL:
                rt.deferred_until_normal = False
                rt.handled_active = False
                rt.handled_fault = False
                if old_obs != next_obs:
                    self._emit("zone_normal", zone.key)
                self._maybe_auto_ack()
                if self.phase == Phase.ARMING and self.phase_deadline is None:
                    self._complete_exit_delay()
                continue
            if next_obs == Observation.ACTIVE:
                if old_obs != Observation.ACTIVE:
                    self._emit("zone_active", zone.key)
                    # A first state read after restore is a baseline, not
                    # necessarily a new event. Preserve already handled alarms.
                    if not first_sample:
                        rt.handled_active = False
                # A deferred zone stays ignored until a real NORMAL observation.
                if rt.deferred_until_normal or rt.ignored_for_arming:
                    continue
                if not rt.handled_active and zone.policy(self.mode):
                    if self.phase in (Phase.ARMED, Phase.PENDING, Phase.TRIGGERED):
                        if first_sample and self.alarm_latched:
                            rt.handled_active = True
                        else:
                            self._activate_zone(zone)
                continue
            if old_obs != Observation.FAULT:
                rt.handled_fault = False
                self._emit("sensor_fault", zone.key)
            if self.mode is not None and zone.policy(self.mode) and not rt.ignored_for_arming:
                self._handle_fault(zone, initial=initial)

    def _maybe_auto_ack(self) -> None:
        """Auto-clear only when every *known alarm source* is confirmed normal."""
        if self.spec.ack_policy != AckPolicy.AUTO_CLEAR or not self.alarm_latched:
            return
        if not self.alarm_sources or not self.monitoring_ready:
            return
        for source in self.alarm_sources:
            key = source.removeprefix("sensor_fault:").removeprefix("entry_timeout:")
            if key not in self.zones or self.zones[key].observation != Observation.NORMAL:
                return
        self.acknowledge(automatic=True)

    def record_action_result(self, action_key: str, *, success: bool, info: str = "") -> None:
        self._emit("action_succeeded" if success else "action_failed", f"{action_key}: {info}"[:300])

    def _activate_zone(self, zone: ZoneSpec) -> None:
        rt = self.zones[zone.key]
        policy = zone.policy(self.mode)
        if policy is None or rt.handled_active or rt.ignored_for_arming:
            return
        if self.phase == Phase.PENDING and policy.follow_entry_delay:
            rt.handled_active = True
            self._emit("zone_following_entry_delay", zone.key)
            return
        if self.phase == Phase.ARMED and policy.entry_delay_seconds:
            if self.begin_entry_delay(zone.key, policy.entry_delay_seconds):
                rt.handled_active = True
            return
        if self.trigger(zone.key):
            rt.handled_active = True

    def _handle_fault(self, zone: ZoneSpec, *, initial: bool) -> None:
        rt = self.zones[zone.key]
        if zone.fault_policy != FaultPolicy.ALARM or rt.handled_fault or rt.ignored_for_arming:
            return
        if self.phase not in (Phase.ARMED, Phase.PENDING, Phase.TRIGGERED):
            return
        if self._fault_under_grace() and (initial or self.reconciling):
            return
        if self.trigger(f"sensor_fault:{zone.key}"):
            rt.handled_fault = True

    def _evaluate_fault_alarms(self) -> None:
        for zone in self._zones_for(self.mode):
            if self.zones[zone.key].observation == Observation.FAULT:
                self._handle_fault(zone, initial=False)

    def _complete_exit_delay(self) -> None:
        if self.phase != Phase.ARMING or self.mode is None:
            return
        try:
            deferred = self._preflight(self.mode)
        except AlarmError as exc:
            self.phase_deadline = None
            self.arming_block_reason = str(exc)
            self._emit("arming_waiting_for_sensor", str(exc))
            return
        for zone in self._zones_for(self.mode):
            rt = self.zones[zone.key]
            # An exemption chosen at activation survives closing during exit.
            rt.ignored_for_arming = zone.key in deferred and self._initial_policy(zone, self.mode) == InitialPolicy.IGNORE
            rt.deferred_until_normal = zone.key in deferred and not rt.ignored_for_arming
        self.phase = Phase.ARMED
        self.arming_block_reason = ""
        self.phase_deadline = None
        self._emit("armed")
        self._evaluate_fault_alarms()
        self._evaluate_diagnostic_arming()

    def finish_recovery(self) -> None:
        """Mark baseline sensor readings complete; fault grace remains in force."""
        if not self.reconciling:
            return
        self.reconciling = False
        self._emit("recovery_checked")
        if self.recovery_fault_until is None:
            self._evaluate_fault_alarms()

    @correlated
    def tick(self) -> None:
        now = self._clock()
        if self.phase == Phase.ARMING and self.phase_deadline is not None and now >= self.phase_deadline:
            self._complete_exit_delay()
        if self.phase == Phase.PENDING and self.phase_deadline is not None and now >= self.phase_deadline:
            self.trigger(self.pending_zone or "entry_timeout")
        if self.recovery_fault_until is not None and now >= self.recovery_fault_until:
            self.recovery_fault_until = None
            if not self.reconciling:
                self._evaluate_fault_alarms()
        if self.alarm_latched and self.action_window_active and self.alarm_deadline is not None:
            if now >= self.alarm_deadline:
                self.action_window_active = False
                self.alarm_deadline = None
                self._emit("action_window_expired")

    def snapshot(self) -> dict:
        return {
            "schema": 4,
            "mode": self.mode,
            "phase": self.phase.value,
            "phase_deadline": self.phase_deadline,
            "pending_zone": self.pending_zone,
            "arming_block_reason": self.arming_block_reason,
            "alarm_deadline": self.alarm_deadline,
            "alarm_latched": self.alarm_latched,
            "alarm_started_at": self.alarm_started_at,
            "action_window_active": self.action_window_active,
            "trigger_count": self.trigger_count,
            "alarm_sources": sorted(self.alarm_sources),
            "history": [
                {"kind": evt.kind, "mode": evt.mode, "when": evt.when, "info": evt.info, "event_id": evt.event_id}
                for evt in self.history
            ],
            "zones": {
                key: {
                    "deferred": rt.deferred_until_normal,
                    "ignored": rt.ignored_for_arming,
                    "handled_active": rt.handled_active,
                    "handled_fault": rt.handled_fault,
                }
                for key, rt in self.zones.items()
            },
        }

    def restore(self, saved: dict) -> None:
        """Restore old snapshots and persist one-activation sensor exclusions."""
        if not isinstance(saved, dict) or saved.get("schema") not in (1, 2, 3, 4):
            raise AlarmError("Unsupported persisted schema")
        mode = saved.get("mode")
        if mode is not None:
            self.spec.mode(mode)
        try:
            phase = Phase(saved.get("phase"))
        except (ValueError, TypeError) as exc:
            raise AlarmError("Invalid stored phase") from exc
        latched = saved.get("alarm_latched") is True
        if (phase == Phase.DISARMED) != (mode is None):
            raise AlarmError("Inconsistent snapshot: mode and phase")
        if (phase == Phase.TRIGGERED) != latched:
            raise AlarmError("Inconsistent snapshot: alarm latch")
        for key in ("phase_deadline", "alarm_deadline"):
            if saved.get(key) is not None and (isinstance(saved[key], bool) or not isinstance(saved[key], (float, int))):
                raise AlarmError("Invalid persisted deadline")
        count = saved.get("trigger_count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise AlarmError("Invalid trigger count")
        if saved.get("schema") in (2, 3, 4):
            saved_zones = saved.get("zones")
            if not isinstance(saved_zones, dict):
                raise AlarmError("Invalid zone snapshot")
            for zone_key, zone_data in saved_zones.items():
                if zone_key in self.zones and (
                    not isinstance(zone_data, dict)
                    or any(type(zone_data.get(k)) is not bool for k in ("deferred", "handled_active", "handled_fault"))
                    or (saved.get("schema") == 4 and type(zone_data.get("ignored")) is not bool)
                ):
                    raise AlarmError("Invalid saved zone flags")
            pending_zone = saved.get("pending_zone")
            if pending_zone is not None and pending_zone not in self.zones:
                raise AlarmError("Unknown pending zone")
        else:
            saved_zones = {}
            pending_zone = None
        if phase == Phase.PENDING and pending_zone is None and saved.get("schema") in (2, 3, 4):
            raise AlarmError("Pending phase without pending zone")
        history: list[AlarmEvent] = []
        alarm_sources: set[str] = set()
        if saved.get("schema") in (3, 4):
            stored_sources = saved.get("alarm_sources")
            stored_history = saved.get("history")
            if (not isinstance(stored_sources, list) or not all(
                isinstance(x, str) and 0 < len(x) <= 300 for x in stored_sources
            ) or not isinstance(stored_history, list) or len(stored_history) > 250):
                raise AlarmError("Invalid saved history or alarm sources")
            alarm_sources = set(stored_sources)
            if alarm_sources and not latched:
                raise AlarmError("Alarm sources without active alarm")
            for record in stored_history:
                if not isinstance(record, dict):
                    raise AlarmError("Invalid history item")
                kind, info, tm, record_mode = (record.get(k) for k in ("kind", "info", "when", "mode"))
                if (not isinstance(kind, str) or not isinstance(info, str)
                    or len(kind) > 100 or len(info) > 300
                    or type(tm) not in (int, float) or not float("-inf") < tm < float("inf")
                    or (record_mode is not None and not isinstance(record_mode, str))):
                    raise AlarmError("Invalid history fields")
                event_id = record.get("event_id")
                if event_id is not None and (not isinstance(event_id, str) or len(event_id) > 100):
                    raise AlarmError("Invalid event correlation")
                history.append(AlarmEvent(self.spec.key, kind, record_mode, tm, info, event_id))
        started=saved.get('alarm_started_at')
        if started is not None and (type(started) not in (int,float) or not float('-inf')<started<float('inf')):
            raise AlarmError('Invalid saved alarm start')
        if started is None:started=next((e.when for e in reversed(history) if e.kind=='alarm_started'),None)
        self.alarm_started_at=started if latched else None
        self.history = history
        self.alarm_sources = alarm_sources
        self.mode = mode
        self.phase = phase
        self.phase_deadline = saved.get("phase_deadline")
        self.pending_zone = pending_zone
        reason = saved.get("arming_block_reason", "")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise AlarmError("Invalid stored arming reason")
        self.arming_block_reason = reason if phase == Phase.ARMING else ""
        self.alarm_deadline = saved.get("alarm_deadline")
        self.alarm_latched = latched
        self.action_window_active = latched and saved.get("action_window_active") is True
        self.trigger_count = count
        for key, runtime in self.zones.items():
            zone_data = saved_zones.get(key, {})
            runtime.deferred_until_normal = zone_data.get("deferred", False)
            runtime.ignored_for_arming = zone_data.get("ignored", False) if saved.get("schema") == 4 else False
            zone = next(z for z in self.spec.zones if z.key == key)
            if runtime.ignored_for_arming and (mode is None or self._initial_policy(zone, mode) != InitialPolicy.IGNORE):
                raise AlarmError("Stored sensor exclusion does not match the armed configuration")
            runtime.handled_active = zone_data.get("handled_active", False)
            runtime.handled_fault = zone_data.get("handled_fault", False)
            runtime.observation = Observation.FAULT
            runtime.verified = False
            runtime.seen_sample = False
        self.reconciling = mode is not None
        self.recovery_fault_until = (
            self._clock() + self.spec.recovery_fault_grace_seconds if mode is not None else None
        )
        # Deadlines from a prior run are still authoritative.
        self.tick()
        self._emit("runtime_restored")

    def zone_summary(self) -> dict[str, dict]:
        return {
            zone.key: {
                "entity_id": zone.entity_id,"attribute":zone.attribute,
                "observation": self.zones[zone.key].observation.value,
                "verified": self.zones[zone.key].verified,
                "active_in_mode": zone.policy(self.mode) is not None,
                "deferred": self.zones[zone.key].deferred_until_normal,
                "ignored": self.zones[zone.key].ignored_for_arming,
                "acknowledged_active": self.zones[zone.key].handled_active and not self.alarm_latched and self.zones[zone.key].observation == Observation.ACTIVE,
            }
            for zone in self.spec.zones
        }


class AlarmEngine:
    """Container for independent alarm areas with shared sensor routing."""

    def __init__(self, specs: Iterable[AreaSpec], *, clock: Callable[[], float] = time.time) -> None:
        self.areas: dict[str, AreaController] = {}
        self._entity_areas: dict[str, list[AreaController]] = {}
        for spec in specs:
            if spec.key in self.areas:
                raise ValueError(f"Duplicate alarm area {spec.key}")
            controller = AreaController(spec, clock=clock)
            self.areas[spec.key] = controller
            for zone in spec.zones:
                targets = self._entity_areas.setdefault(zone.entity_id, [])
                if controller not in targets:
                    targets.append(controller)


    def arm_mode(self, mode_key: str) -> tuple[str, ...]:
        """Arm one shared mode across all assigned areas (all-or-nothing preflight).

        A preflight refusal never partially changes another area. Successful
        arm commands still emit events and are handled by the existing adapter.
        """
        targets = [area for area in self.areas.values()
                   if any(mode.key == mode_key for mode in area.spec.modes)]
        if not targets:
            raise AlarmError(f"Unknown global mode: {mode_key}")
        for target in targets:
            if target.mode != mode_key or target.phase == Phase.DISARMED:
                target._preflight(mode_key)
        for target in targets:
            target.arm(mode_key)
        return tuple(area.spec.key for area in targets)

    def disarm_mode(self, mode_key: str) -> tuple[str, ...]:
        """Disarm areas currently using a mode, without changing others."""
        targets = [area for area in self.areas.values() if area.mode == mode_key]
        for target in targets:
            target.disarm()
        return tuple(area.spec.key for area in targets)

    @property
    def watched_entities(self) -> tuple[str, ...]:
        return tuple(self._entity_areas)

    @correlated
    def update_sensor(self, entity_id: str, state: str | None, *, initial: bool = False,
                      attributes: dict | None = None) -> None:
        shortcuts = {controller.mode for controller in self._entity_areas.get(entity_id, ()) if controller.phase==Phase.ARMING and not initial and any(z.entity_id==entity_id and z.policy(controller.mode) and z.policy(controller.mode).arm_on_close and controller.zones[z.key].seen_sample and controller.zones[z.key].observation==Observation.ACTIVE and z.classify(state,attributes)==Observation.NORMAL for z in controller.spec.zones)}
        for controller in self._entity_areas.get(entity_id, ()):
            controller.update_sensor(entity_id, state, initial=initial, attributes=attributes)

        for mode in shortcuts:
            targets=[a for a in self.areas.values() if a.mode==mode and a.phase==Phase.ARMING]
            try:
                for area in targets:area._preflight(mode)
            except AlarmError:
                continue
            for area in targets:area._complete_exit_delay()

    def finish_recovery(self) -> None:
        for controller in self.areas.values():
            controller.finish_recovery()

    @correlated
    def tick(self) -> None:
        for area in self.areas.values():
            area.tick()

    def snapshot(self) -> dict:
        return {"schema": 3, "areas": {key: area.snapshot() for key, area in self.areas.items()}}

    def restore(self, data: dict) -> None:
        if not isinstance(data, dict) or data.get("schema") not in (1, 2, 3):
            raise AlarmError("Unsupported engine snapshot")
        saved_areas = data.get("areas")
        if not isinstance(saved_areas, dict):
            raise AlarmError("Invalid area snapshot")
        missing = set(saved_areas) - set(self.areas)
        for key in missing:
            old = saved_areas[key]
            if (
                not isinstance(old, dict)
                or old.get("mode") is not None
                or old.get("phase") != Phase.DISARMED.value
                or old.get("alarm_latched") is not False
            ):
                raise AlarmError(f"Active or invalid saved alarm area {key} removed; migration required")
        for key, saved in saved_areas.items():
            if key in self.areas:
                self.areas[key].restore(saved)

