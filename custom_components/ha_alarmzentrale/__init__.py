"""Wachturm integration with central access checks and external input bindings.

Event-bus messages alone do not identify a person. Every human operation passes
the configured identity, permission and PIN checks before changing alarm state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import asyncio
from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.const import Platform
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.exceptions import HomeAssistantError

from .configuration import default_config_json, parse_configuration_json
from .diagnostic_adapter import DiagnosticAdapter, parse_diagnostics
from .diagnostic_policy import selected_reactions, arming_faults
from .const import DOMAIN, STORAGE_VERSION, NAME, LEGACY_DEFAULT_NAMES
from .engine import AlarmEngine, AlarmError
from .ha_actions import HAActionDispatcher
from .event_context import current_event
from uuid import uuid4
from .security import AccessDenied, Authorization
from .commands import AlarmCommands
from .audit import AuditTrail
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.ALARM_CONTROL_PANEL]


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register all human-command paths with per-area authorization."""
    runtimes = hass.data.setdefault(DOMAIN, {})

    async def execute_service(call: ServiceCall) -> None:
        if len(runtimes) != 1:
            raise HomeAssistantError("Exactly one alarm controller is required")
        runtime = next(iter(runtimes.values()))
        area_key = call.data["area_key"]
        operation = {
            "mute_actions": "mute", "acknowledge_alarm": "acknowledge",
            "arm_area": "arm", "disarm_area": "disarm",
        }[call.service]
        mode = call.data.get("mode") if operation == "arm" else None
        try:
            user_id = call.context.user_id
            # A removed/disabled HA account must lose authorization immediately.
            auth_api = getattr(hass, "auth", None)
            if auth_api is not None:
                account = await auth_api.async_get_user(user_id) if user_id else None
                if account is None or not getattr(account, "is_active", False):
                    runtime.commands._record(operation, "denied", "ha_account", area_key, mode)
                    raise AccessDenied("HA account inactive or missing")
            runtime.commands.from_ha_user(user_id, area_key, operation, mode)
        except (AccessDenied, AlarmError) as exc:
            raise HomeAssistantError(str(exc)) from exc

    area_schema = vol.Schema({vol.Required("area_key"): str})
    arm_schema = vol.Schema({vol.Required("area_key"): str, vol.Required("mode"): str})
    for service in ("mute_actions", "acknowledge_alarm", "disarm_area"):
        hass.services.async_register(DOMAIN, service, execute_service, schema=area_schema)
    hass.services.async_register(DOMAIN, "arm_area", execute_service, schema=arm_schema)
    return True


@dataclass(slots=True)
class AlarmRuntime:
    engine: AlarmEngine
    store: Store
    signal: str
    actions: HAActionDispatcher
    authorization: Authorization
    commands: AlarmCommands
    audit: AuditTrail = field(default_factory=AuditTrail)
    diagnostics: DiagnosticAdapter | None = None
    editor_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    configuration: dict = field(default_factory=dict)
    sensor_test: object | None = None
    action_simulation: object | None = None
    simulation_task: object | None = None

    def snapshot(self) -> dict:
        payload = self.engine.snapshot()
        payload["authorization"] = self.authorization.snapshot()
        payload["audit"] = self.audit.snapshot()
        if self.diagnostics is not None:
            payload["diagnostics"] = self.diagnostics.snapshot()
        return payload


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    config_json = entry.options.get("configuration_json", default_config_json())
    try:
        specs, security = parse_configuration_json(config_json)
    except (ValueError, TypeError) as exc:
        _LOGGER.error("Invalid area/zone configuration; startup refused: %s", exc)
        return False

    if entry.title in LEGACY_DEFAULT_NAMES:
        hass.config_entries.async_update_entry(entry,title=NAME)

    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    engine = AlarmEngine(specs)
    loaded = await store.async_load()
    if loaded is not None:
        try:
            engine.restore(loaded)
        except (AlarmError, ValueError, TypeError) as exc:
            _LOGGER.error("Stored alarm state invalid; refusing integration startup: %s", exc)
            return False
    actions = HAActionDispatcher(hass, engine)
    authorization = Authorization(security, local_now=dt_util.now)
    audit = AuditTrail()
    if loaded is not None and "audit" in loaded:
        try:
            audit.restore(loaded["audit"])
        except (TypeError, ValueError) as exc:
            _LOGGER.error("Stored audit invalid; refusing startup to preserve history: %s", exc)
            return False
    commands = AlarmCommands(engine, authorization, audit)
    if loaded is not None and "authorization" in loaded:
        try:
            authorization.restore(loaded["authorization"])
        except (TypeError, ValueError) as exc:
            _LOGGER.error("Invalid saved authorization state; refusing startup: %s", exc)
            return False
    full_config = json.loads(config_json)
    diagnostics_config = parse_diagnostics(full_config)
    diagnostics = DiagnosticAdapter(diagnostics_config) if diagnostics_config.enabled else None
    if diagnostics is not None and loaded and "diagnostics" in loaded:
        try:
            diagnostics.restore(loaded["diagnostics"])
        except (TypeError, ValueError, KeyError) as exc:
            _LOGGER.warning("Diagnostic history invalid; continuing without old diagnostic data: %s", exc)
            diagnostics = DiagnosticAdapter(diagnostics_config)
    runtime = AlarmRuntime(engine, store, f"{DOMAIN}_change_{entry.entry_id}",
                           actions, authorization, commands, audit, diagnostics)
    runtime.configuration = full_config
    audit.changed = lambda: store.async_delay_save(runtime.snapshot, 1.0)
    if diagnostics is not None:
        for area_key, area in engine.areas.items():
            area.diagnostic_guard = lambda mode, area_key=area_key: arming_faults(
                full_config, diagnostics, area_key, mode)
    actions.start()
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    @callback
    def on_state_change(_event) -> None:
        if not commands.test_active():actions.enqueue(_event)
        store.async_delay_save(runtime.snapshot, 1.0)
        async_dispatcher_send(hass, runtime.signal)

    for area in engine.areas.values():
        entry.async_on_unload(area.subscribe(on_state_change))

    # Only listen to entities explicitly configured as zones. No generic event
    # listener and no write access to sensors or outputs.
    test_entity_ids={source.get("entity_id") for sensor in full_config.get("sensors",[]) for source in [*sensor.get("entities",[]),*([sensor["sabotage"]] if sensor.get("sabotage") else [])] if source.get("entity_id")}
    observed_entities=tuple(set(engine.watched_entities)|test_entity_ids)
    if observed_entities:
        @callback
        def on_sensor_event(event) -> None:
            entity_id = event.data.get("entity_id")
            new_state = event.data.get("new_state")
            if entity_id:
                if commands.test_active() and runtime.sensor_test:
                    runtime.sensor_test.observe_config(full_config,entity_id,new_state.state if new_state is not None else None,getattr(new_state,"attributes",None) if new_state is not None else None)
                context=getattr(event,'context',None)
                token=current_event.set(getattr(context,'parent_id',None) or getattr(context,'id',None) or uuid4().hex)
                try:
                    engine.update_sensor(entity_id, new_state.state if new_state is not None else None,
                                         attributes=getattr(new_state, "attributes", None) if new_state is not None else None)
                finally:
                    current_event.reset(token)


        entry.async_on_unload(async_track_state_change_event(hass, observed_entities, on_sensor_event))

    if diagnostics is not None:
        @callback
        def handle_diagnostic_events() -> None:
            # A multi-device radio outage must not masquerade as many intrusions.
            common_outage = any(e.kind == "common_outage_suspected" for e in diagnostics.last_events)
            for event in diagnostics.last_events:
                token=current_event.set(current_event.get() or uuid4().hex)
                try:
                    if event.kind not in {"offline", "offline_unstable", "battery", "unstable", "battery_recovered", "online", "stable_recovery"}:
                        continue
                    if event.kind == "battery" and event.detail not in {"low", "empty"}:
                        continue
                    # Explicit diagnostic notifications are independent of arming.
                    # They cannot use timed sirens or mutate the alarm latch.
                    notification_kind = "unstable" if event.kind == "offline_unstable" else event.kind
                    for area_key, area in engine.areas.items():
                        belongs = any((sensor.get("device_id") or sensor.get("key")) == event.source and (
                            any(m.get("area") == area_key for m in sensor.get("memberships", [])) or
                            (area_key == "system_sabotage" and sensor.get("sabotage") and sensor["sabotage"].get("enabled", True)))
                            for sensor in full_config.get("sensors", []))
                        action_kind = {"battery_recovered": "battery", "online": "offline", "stable_recovery": "unstable"}.get(notification_kind, notification_kind)
                        if belongs and any(action.when == action_kind for action in area.spec.actions):
                            area._emit(f"diagnostic_{notification_kind}", event.source)
                    for key, area in engine.areas.items():
                        if (area.mode is None or area.reconciling or
                                area.phase.value not in {"armed", "pending", "triggered"}):
                            continue
                        choices = selected_reactions(
                            full_config, event.source, key, area.mode, event.kind,
                            low_battery="previous low-battery indication" in event.detail,
                        )
                        if "event" in choices and hasattr(hass.bus, "async_fire"):
                            hass.bus.async_fire(f"{DOMAIN}_diagnostic", {
                                "area_key": key, "mode": area.mode, "device_id": event.source,
                                "kind": event.kind, "detail": event.detail,
                            })
                        if ("alarm" in choices and event.kind == "offline" and
                                not common_outage and area.monitoring_ready):
                            area.trigger(f"diagnostic_offline:{event.source}")
                finally:
                    current_event.reset(token)

        @callback
        def on_diagnostic_state(_event) -> None:
            if diagnostics.refresh(hass.states, dt_util.utcnow()):
                handle_diagnostic_events()
                store.async_delay_save(runtime.snapshot, 1.0)
                # If an exit delay was blocked by temporary loss of reachability,
                # complete the existing preflight once the diagnostic is restored.
                for area in engine.areas.values():
                    if area.phase.value == "arming" and area.phase_deadline is None:
                        area._complete_exit_delay()

        if diagnostics_config.entity_ids:
            entry.async_on_unload(async_track_state_change_event(
                hass, diagnostics_config.entity_ids, on_diagnostic_state))
        # On startup only observe baseline, never launch alarm actions.
        diagnostics.refresh(hass.states, dt_util.utcnow())

    # Device events MUST match every configured identifying field. Never
    # subscribe to generic state changes as a disarm credential.
    if security.bindings:
        @callback
        def on_controller_event(event) -> None:
            commands.from_event(event.event_type, event.data)

        for event_type in sorted({binding.event_type for binding in security.bindings if binding.input_type in {"homematic", "event"}}):
            entry.async_on_unload(hass.bus.async_listen(event_type, on_controller_event))

    controls = tuple({b.entity_id for b in security.bindings if b.input_type in {"entity", "switch"}})
    if controls:
        @callback
        def on_control_state(event):
            old, new = event.data.get("old_state"), event.data.get("new_state")
            # State baseline and restored/unavailable recovery never act as a command.
            if old is None or new is None or old.state in {"unknown", "unavailable"} or old.state == new.state or new.state in {"unknown", "unavailable"}:
                return
            commands.from_event("alarmzentrale_entity", {"entity_id": event.data.get("entity_id"), "state": new.state})
        entry.async_on_unload(async_track_state_change_event(hass, controls, on_control_state))

    startup_token=current_event.set(uuid4().hex)
    try:
        for entity_id in engine.watched_entities:
            current = hass.states.get(entity_id)
            engine.update_sensor(entity_id, current.state if current is not None else None, initial=True,
                                 attributes=getattr(current, "attributes", None) if current is not None else None)
        engine.finish_recovery()
        for area in engine.areas.values():
            if area.alarm_latched:
                area._emit("notification_refresh")
    
    finally:
        current_event.reset(startup_token)

    @callback
    def on_tick(_now) -> None:
        engine.tick()
        if diagnostics is not None:
            if diagnostics.refresh(hass.states, dt_util.utcnow()):
                handle_diagnostic_events()
                store.async_delay_save(runtime.snapshot, 1.0)

    entry.async_on_unload(async_track_time_interval(hass, on_tick, timedelta(seconds=1)))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        from .panel import async_register_editor
        await async_register_editor(hass)
    except Exception:
        commands.retired = True
        await actions.stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime: AlarmRuntime = entry.runtime_data
    runtime.commands.retired = True
    if runtime.action_simulation:
        await runtime.action_simulation.stop()
    if runtime.simulation_task:
        await runtime.simulation_task
    await runtime.actions.stop()
    await runtime.store.async_save(runtime.snapshot())
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    from .panel import async_unregister_editor
    await async_unregister_editor(hass)
    return True
