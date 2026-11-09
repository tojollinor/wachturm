"""Pure asynchronous area action executor, separated from Home Assistant I/O.

Only a new alarm event or a *new sensor edge* produces an action run.
One shared mute window belongs to the active arming mode, never to actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
import time
from hashlib import sha256
from .event_context import current_event

from .notifications import notification_payload, tts_payload
from .engine import ActionSpec, AlarmEvent, AreaController, Phase


class ActionBackend(Protocol):
    async def call(self, domain: str, service: str, *, entity_ids: tuple[str, ...], data: dict) -> None: ...
    def read_state(self, entity_id: str) -> str | None: ...
    def read_revision(self, entity_id: str) -> str | None: ...


@dataclass(slots=True)
class ActionRunner:
    area: AreaController
    backend: ActionBackend
    # Only stop outputs observed OFF before OUR successful turn_on call.
    owned: dict[str, dict[str, str | None]] = field(default_factory=dict)
    executed: set[str] = field(default_factory=set)
    mobile_active: set[str] = field(default_factory=set)

    output_events: dict = field(default_factory=dict)
    output_due: dict = field(default_factory=dict)
    seen: dict = field(default_factory=dict)
    tts_due: dict = field(default_factory=dict)
    tts_started: set = field(default_factory=set)

    async def tick(self):
        for key, due in tuple(self.output_due.items()):
            if time.monotonic() >= due:
                token=current_event.set(self.output_events.get(key))
                try:
                    await self.stop_timed_outputs(only={key})
                finally:
                    current_event.reset(token)
                if key not in self.owned:self.output_due.pop(key,None)
        await self.repeat_tts()

    async def handle(self, event: AlarmEvent) -> None:
        token=current_event.set(event.event_id)
        try:
            await self._handle(event)
        finally:
            current_event.reset(token)

    async def repeat_tts(self):
        if not self.area.alarm_latched or not self.area.action_window_active:
            self.tts_due.clear()
            return
        for key, (due, action, text) in tuple(self.tts_due.items()):
            if action.mode_keys is not None and self.area.mode not in action.mode_keys:
                self.tts_due.pop(key,None)
                continue
            if time.monotonic() < due:
                continue
            self.tts_due[key]=(time.monotonic()+action.notification["tts_repeat_seconds"],action,text)
            try:
                await self.backend.call(action.domain,action.service,entity_ids=(),data=tts_payload(text,action.notification.get("tts_max",False)))
            except Exception as exc:
                self.area.record_action_result(key,success=False,info=f"TTS: {type(exc).__name__}")

    async def _handle(self, event: AlarmEvent) -> None:
        if event.kind in {"zone_normal", "actions_muted", "action_window_expired", "disarmed", "alarm_ended", "alarm_acknowledged", "notification_refresh"}:
            for action in self.area.spec.actions:
                if action.enabled and action.key in self.mobile_active:
                    await self._notify(action, event, quiet=True)
            if event.kind in {"disarmed", "alarm_ended", "alarm_acknowledged"}:
                self.mobile_active.clear()
        if event.kind in ("alarm_acknowledged", "alarm_ended", "disarmed", "actions_muted", "action_window_expired"):
            self.tts_due.clear()
            for action in self.area.spec.actions:
                if action.key in self.tts_started:
                    try:
                        await self.backend.call(action.domain,action.service,entity_ids=(),data={"message":"command_stop_tts","data":{"priority":"high","ttl":0}})
                    except Exception as exc:
                        self.area.record_action_result(action.key,success=False,info=f"TTS stop: {type(exc).__name__}")
            self.tts_started.clear()
            await self.stop_timed_outputs(only={a.key for a in self.area.spec.actions if a.duration_seconds is None} if event.kind=="action_window_expired" else None)
            if event.kind in ("alarm_acknowledged", "alarm_ended", "disarmed"):
                self.executed.clear()
        operation={"actions_muted":"mute","alarm_acknowledged":"acknowledge"}.get(event.kind)
        if operation:
            for action in self.area.spec.actions:
                if action.when==operation and (action.mode_keys is None or event.mode in action.mode_keys):
                    await self._start(action,event)
        special = {"arm_requested": ("armed", "before"), "arming": ("armed", "during"),
                   "entry_delay_started": ("alarm", "before"),
                   "alarm_acknowledged": ("alarm", "after"), "alarm_ended": ("alarm", "after"),
                   "diagnostic_battery_recovered": ("battery", "after"),
                   "diagnostic_online": ("offline", "after"),
                   "diagnostic_stable_recovery": ("unstable", "after")}.get(event.kind)
        if special:
            if event.kind == "arming" and self.area.phase != Phase.ARMING:
                return
            if event.kind == "entry_delay_started" and self.area.phase != Phase.PENDING:
                return
            if event.kind == "arm_requested" and (self.area.phase == Phase.DISARMED or self.area.mode != event.mode):
                return
            for action in self.area.spec.actions:
                if (action.when, action.effective_timing) == special and (action.mode_keys is None or event.mode in action.mode_keys):
                    await self._start(action, event)
            return
        route = {"alarm_started": "alarm", "alarm_retriggered": "alarm", "armed": "armed",
                 "disarmed": "disarmed", "diagnostic_battery": "battery",
                 "diagnostic_offline": "offline", "diagnostic_unstable": "unstable"}.get(event.kind)
        if route is None:
            return
        if route != "alarm":
            if route == "armed" and self.area.phase != Phase.ARMED:
                return
            if route == "disarmed" and self.area.phase != Phase.DISARMED:
                return
            for action in self.area.spec.actions:
                if route == 'armed' and self.area.phase != Phase.ARMED or route == 'disarmed' and self.area.phase != Phase.DISARMED:
                    break
                if action.when == route and action.effective_timing == ("after" if route in {"armed", "disarmed"} else "during") and (action.mode_keys is None or event.mode in action.mode_keys):
                    await self._start(action, event)
            return
        # A sensor can trigger and be disarmed before the async worker wakes.
        # Never energize an output for an alarm that is no longer active.
        if not self.area.alarm_latched or self.area.phase != Phase.TRIGGERED:
            return
        if event.kind == "alarm_started":
            self.executed.clear()
        for action in self.area.spec.actions:
            if action.when != "alarm" or action.effective_timing != "during":
                continue
            if action.mode_keys is not None and event.mode not in action.mode_keys:
                continue
            # Recheck after every awaited service call; human disarm may have
            # occurred while an earlier action was executing.
            if not self.area.alarm_latched or self.area.phase != Phase.TRIGGERED:
                break
            mobile = action.notification.get("platform") in {"ios", "android"}
            if event.kind == "alarm_retriggered" and not action.repeat_on_retrigger and not mobile:
                continue
            if action.timed and action.duration_seconds is None and not self.area.action_window_active:
                continue
            if action.key in self.executed and not action.repeat_on_retrigger and not mobile:
                continue
            await self._start(action, event)
            self.executed.add(action.key)

    async def _notify(self, action, event, quiet=False):
        try:
            data = notification_payload(action, self.area, event, links=getattr(self.backend, "notification_links", None), quiet=quiet, timezone_name=getattr(self.backend,"notification_timezone",None))
            speech=notification_payload(action,self.area,event,quiet=quiet,timezone_name=getattr(self.backend,'notification_timezone',None),speech=True).get('message','')
            if quiet and self.area.alarm_latched and self.area.action_window_active and action.notification.get("platform")=="android" and action.notification.get("tts") and action.notification.get("tts_repeat_seconds"):
                due=self.tts_due.get(action.key,(time.monotonic()+action.notification["tts_repeat_seconds"],))[0]
                self.tts_due[action.key]=(due,action,speech)
            if action.notification.get("platform")=="android" and not quiet and self.area.action_window_active:
                for option,command in (("screen_on","command_screen_on"),("open_alarm","command_webview")):
                    if action.notification.get(option):
                        extra={"priority":"high","ttl":0}
                        if option=="open_alarm":extra["command"]="/ha-alarmzentrale?alarm=1"
                        await self.backend.call(action.domain,action.service,entity_ids=(),data={"message":command,"data":extra})
            await self.backend.call(action.domain, action.service, entity_ids=(), data=data)
            if action.notification.get("platform")=="ios" and action.notification.get("critical") and not quiet:
                status=notification_payload(action,self.area,event,links=getattr(self.backend,"notification_links",None),quiet=True,timezone_name=getattr(self.backend,"notification_timezone",None))
                await self.backend.call(action.domain,action.service,entity_ids=(),data=status)
            if action.notification.get("platform")=="android" and action.notification.get("tts") and not quiet and self.area.action_window_active:
                await self.backend.call(action.domain,action.service,entity_ids=(),data=tts_payload(speech,action.notification.get("tts_max",False)))
                self.tts_started.add(action.key)
                if action.notification.get("tts_repeat_seconds"):
                    self.tts_due[action.key]=(time.monotonic()+action.notification["tts_repeat_seconds"],action,speech)
        except Exception as exc:
            self.area.record_action_result(action.key, success=False, info=f"notify: {type(exc).__name__}")
        else:
            if action.notification.get("platform") in {"ios", "android"} and self.area.alarm_latched:
                self.mobile_active.add(action.key)
            self.area.record_action_result(action.key, success=True, info="notify")

    async def _start(self, action: ActionSpec, event: AlarmEvent) -> None:
        if not action.enabled:return
        identity=(event.event_id,event.kind,action.key,sha256(repr(action).encode()).digest())
        if event.event_id:
            if identity in self.seen:return
            self.seen[identity]=True
            if len(self.seen)>2048:self.seen.pop(next(iter(self.seen)))
        if action.domain == "notify":
            await self._notify(action, event, quiet=event.kind=="alarm_retriggered" and not action.repeat_on_retrigger)
            return
        if not action.timed:
            try:
                await self.backend.call(action.domain, action.service, entity_ids=action.entity_ids, data=action.data)
            except Exception as exc:
                self.area.record_action_result(action.key, success=False, info=f"start: {type(exc).__name__}")
            else:
                self.area.record_action_result(action.key, success=True, info="start")
            return
        # Timed actions are individually owned per entity, not as an entire
        # HA target group. An already-on light/siren must NEVER be auto-stopped.
        for entity_id in action.entity_ids:
            state = self.backend.read_state(entity_id)
            if state not in ("on", "off"):
                self.area.record_action_result(action.key, success=False, info=f"unknown target: {entity_id}")
                continue
            owned = self.owned.get(action.key, {})
            # A different state-context means another HA actor has touched
            # this output since our last successful service call.
            if entity_id in owned and owned[entity_id] is not None:
                if self.backend.read_revision(entity_id) != owned[entity_id]:
                    owned.pop(entity_id)
                    self.area.record_action_result(action.key, success=False, info=f"externally changed: {entity_id}")
                    continue
            if state == "on" and entity_id not in owned:
                self.area.record_action_result(action.key, success=True, info=f"already on, untouched: {entity_id}")
                continue
            try:
                await self.backend.call(action.domain, action.service, entity_ids=(entity_id,), data=action.data)
            except Exception as exc:
                self.area.record_action_result(action.key, success=False, info=f"start {entity_id}: {type(exc).__name__}")
            else:
                self.owned.setdefault(action.key, {})[entity_id] = self.backend.read_revision(entity_id)
                if action.duration_seconds is not None:
                    self.output_due[action.key]=time.monotonic()+action.duration_seconds
                    self.output_events[action.key]=event.event_id
                self.area.record_action_result(action.key, success=True, info=f"started {entity_id}")

    async def stop_timed_outputs(self, only=None) -> None:
        for action in self.area.spec.actions:
            if not action.timed or only is not None and action.key not in only:
                continue
            entity_ids = self.owned.get(action.key)
            if not entity_ids:
                continue
            for entity_id in tuple(entity_ids):
                state = self.backend.read_state(entity_id)
                if state == "on" and entity_ids[entity_id] is not None:
                    if self.backend.read_revision(entity_id) != entity_ids[entity_id]:
                        entity_ids.pop(entity_id)
                        self.area.record_action_result(action.key, success=False, info=f"external change, not stopped: {entity_id}")
                        continue
                if state == "off":
                    entity_ids.pop(entity_id)
                    continue
                if state != "on":
                    self.area.record_action_result(action.key, success=False, info=f"cannot safely stop unknown state: {entity_id}")
                    continue
                try:
                    await self.backend.call(action.domain, "turn_off", entity_ids=(entity_id,), data={})
                except Exception as exc:
                    self.area.record_action_result(action.key, success=False, info=f"stop {entity_id}: {type(exc).__name__}")
                else:
                    entity_ids.pop(entity_id)
                    self.area.record_action_result(action.key, success=True, info=f"stopped {entity_id}")
            if not entity_ids:
                self.owned.pop(action.key, None)
                self.output_due.pop(action.key,None)
                self.output_events.pop(action.key,None)
