"""Home Assistant service adapter for alarm actions."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .notifications import NotificationLinks
from .actions import ActionRunner
from .engine import AlarmEngine, AlarmEvent

_LOGGER = logging.getLogger(__name__)


class HAActionBackend:
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.notification_links = NotificationLinks()
        self.notification_timezone = getattr(getattr(hass,"config",None),"time_zone",None)

    def read_state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.state if state is not None else None

    def read_revision(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.context.id if state is not None else None

    async def call(self, domain: str, service: str, *, entity_ids: tuple[str, ...], data: dict) -> None:
        if not self.hass.services.has_service(domain, service):
            raise ValueError(f"Unavailable HA service {domain}.{service}")
        await self.hass.services.async_call(
            domain, service, dict(data),
            target={"entity_id": list(entity_ids)} if entity_ids else None,
            blocking=True,
        )


class HAActionDispatcher:
    """FIFO per alarm area; action failures cannot crash alarm state transitions."""

    def __init__(self, hass: HomeAssistant, engine: AlarmEngine):
        self.hass = hass
        backend = HAActionBackend(hass)
        self.notification_links = backend.notification_links
        self.runners = {key: ActionRunner(area, backend) for key, area in engine.areas.items()}
        for runner in self.runners.values():
            if runner.area.alarm_latched:
                runner.mobile_active = {a.key for a in runner.area.spec.actions if a.notification.get("platform") in {"ios", "android"} and (a.mode_keys is None or runner.area.mode in a.mode_keys)}
        self.queues: dict[str, asyncio.Queue[AlarmEvent | None]] = {}
        self.tasks: list[asyncio.Task] = []
        self.stopping = False
        self.in_flight: set[str] = set()

    def start(self) -> None:
        for key, runner in self.runners.items():
            if runner.area.spec.actions:
                q: asyncio.Queue[AlarmEvent | None] = asyncio.Queue()
                self.queues[key] = q
                self.tasks.append(self.hass.async_create_task(self._worker(key, q)))

    def enqueue(self, event: AlarmEvent) -> None:
        if self.stopping:
            return
        q = self.queues.get(event.area)
        if q is not None and event.kind in {
            "arm_requested", "arming", "entry_delay_started", "alarm_ended",
            "diagnostic_battery_recovered", "diagnostic_online", "diagnostic_stable_recovery",
            "alarm_started", "alarm_retriggered", "actions_muted", "armed",
            "diagnostic_battery", "diagnostic_offline", "diagnostic_unstable", "zone_normal", "notification_refresh",
            "action_window_expired", "alarm_acknowledged", "disarmed",
        }:
            q.put_nowait(event)

    async def _worker(self, key: str, q: asyncio.Queue[AlarmEvent | None]) -> None:
        runner = self.runners[key]
        while True:
            try:
                evt=await asyncio.wait_for(q.get(),timeout=1)
            except asyncio.TimeoutError:
                if not self.stopping:
                    await runner.repeat_tts()
                continue
            if evt is None:
                break
            try:
                self.in_flight.add(key)
                await runner.handle(evt)
            except Exception:
                _LOGGER.exception("Unhandled action dispatch error for area %s", key)
            finally:
                self.in_flight.discard(key)
                q.task_done()
        q.task_done()

    async def stop(self) -> None:
        self.stopping = True
        for q in self.queues.values():
            q.put_nowait(None)
        if self.tasks:
            await asyncio.gather(*self.tasks)
        # On normal unload, try stopping outputs started by this integration.
        # Never stop an output that was already on before we touched it.
        for runner in self.runners.values():
            runner.tts_due.clear()
            await runner.stop_timed_outputs()
