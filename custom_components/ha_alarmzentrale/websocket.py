"""Administrator-only editor API and guarded temporary Homematic event learning.

The browser is not an authentication boundary: every command requires HA admin.
Never send local panel PIN hashes or raw PINs back to the browser.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .configuration import upgrade_legacy_config, default_config_json
from .const import DOMAIN
from .editor import (
    EditorError, prepare_configuration, prepare_pin_change, prepare_pin_removal,
    visible_document,
    describe_changes,
)
from .engine import Phase
from .engine import AlarmError
from .security import AccessDenied
from .presentation import runtime_view

PANEL_URL = "ha-alarmzentrale"
_ALLOWED_EVENT_TYPES = ("homematic.keypress", "homematic.impulse")


class ReloadFailed(EditorError):
    """Options were persisted, but HA did not successfully reload the entry."""


@asynccontextmanager
async def _edit_transaction(hass, runtime):
    async with runtime.editor_lock:
        if _entry_and_runtime(hass)[1] is not runtime:
            raise EditorError("Die Alarmzentrale wurde inzwischen neu geladen. Bitte aktualisieren.")
        _assert_safe_to_edit(runtime)
        runtime.commands.configuration_locked = True
        try:
            yield
        finally:
            runtime.commands.configuration_locked = False


def _entry_and_runtime(hass: HomeAssistant):
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1:
        raise EditorError("Genau eine Wachturm-Instanz erforderlich")
    entry = entries[0]
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        raise EditorError("Wachturm ist nicht betriebsbereit")
    return entry, runtime


def _current(entry) -> str:
    return entry.options.get("configuration_json", default_config_json())


def _assert_safe_to_edit(runtime) -> None:
    """Do not reset an armed, triggered or unreconciled controller on reload."""
    if runtime.commands.test_active():raise EditorError("Sensortest zuerst beenden")
    for area in runtime.engine.areas.values():
        if (area.phase != Phase.DISARMED or area.alarm_latched
                or area.reconciling or area.action_window_active):
            raise EditorError("Zum Ändern zuerst alle Bereiche unscharf schalten und Alarme quittieren")
    if runtime.actions.in_flight or any(not queue.empty() for queue in runtime.actions.queues.values()):
        raise EditorError("Alarmaktionen werden noch ausgeführt")


def _record_editor(runtime, connection, action: str, outcome: str, *, target: str | None = None) -> None:
    # HA WebSocket authenticates the administrator. Never treat a missing ID as
    # proof of a named human; never store the request document or a PIN.
    user = getattr(connection, "user", None)
    identity = getattr(user, "id", None)
    runtime.audit.record(action=action, outcome=outcome, source="configuration",
                         user_key=identity, user_name=getattr(user, "name", None) if identity else None,
                         identity_confidence="authenticated_account" if identity else "unverified",
                         detail=target)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/editor/get"})
@websocket_api.async_response
async def ws_editor_get(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        entry, runtime = _entry_and_runtime(hass)
        data = visible_document(_current(entry))
        data["areas_state"] = [{
            "key": key, "mode": area.mode, "phase": area.phase.value,
            "alarm": area.alarm_latched, "ready": area.monitoring_ready,
            "reconciling": area.reconciling,
        } for key, area in runtime.engine.areas.items()]
        data["diagnostics"] = getattr(runtime, "diagnostics", None).view() if getattr(runtime, "diagnostics", None) else {"enabled": False}
        # Read-only projection from actual HA auth registry, not user-submitted IDs.
        ha_auth = getattr(hass, "auth", None)
        ha_users = await ha_auth.async_get_users() if ha_auth is not None else []
        data["ha_users"] = [{"id": u.id, "name": u.name or u.id,
                             "active": bool(getattr(u, "is_active", False)),
                             "system_generated": bool(getattr(u, "system_generated", False))}
                            for u in ha_users if getattr(u, "id", None)]
        data["audit"] = runtime.audit.query(limit=100)
        data["safe_to_edit"] = all(
            area.phase == Phase.DISARMED and not area.alarm_latched and not area.reconciling
            for area in runtime.engine.areas.values()
        )
        connection.send_result(msg["id"], data)
    except (EditorError, ValueError) as exc:
        connection.send_error(msg["id"], "invalid_state", str(exc))


async def _save(hass: HomeAssistant, entry, updated: str) -> None:
    """Persist atomically as config-entry options, then reload to apply."""
    options = dict(entry.options)
    options["configuration_json"] = updated
    hass.config_entries.async_update_entry(entry, options=options)
    if not await hass.config_entries.async_reload(entry.entry_id):
        # State is not safe for production if reload fails; report, never imply success.
        raise ReloadFailed("Gespeichert, aber erneutes Laden fehlgeschlagen. Integration prüfen!")


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/editor/save",
    vol.Required("revision"): str,
    vol.Required("configuration"): dict,
})
@websocket_api.async_response
async def ws_editor_save(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        entry, runtime = _entry_and_runtime(hass)
        async with _edit_transaction(hass, runtime):
            _assert_safe_to_edit(runtime)
            before = _current(entry)
            proposed = prepare_configuration(before, msg["configuration"], msg["revision"])
            from homeassistant.helpers import entity_registry as er
            registry = er.async_get(hass)
            for sensor in json.loads(proposed).get("sensors", []):
                if sensor.get("connection", {}).get("method") == "availability":
                    entity_id = sensor.get("diagnostic_entities", {}).get("connectivity")
                    entity = registry.async_get(entity_id)
                    if entity is None or not sensor.get("device_id") or entity.device_id != sensor["device_id"]:
                        raise EditorError("Die Referenzentität zur Erreichbarkeit muss zum gewählten Gerät gehören")
            known = await hass.auth.async_get_users() if getattr(hass, "auth", None) else []
            allowed = {u.id for u in known if getattr(u, "is_active", False)}
            if getattr(hass, "auth", None) is not None:
                from .configuration import parse_security
                for principal in parse_security(json.loads(proposed)).users:
                    if any(uid not in allowed for uid in principal.ha_user_ids):
                        raise EditorError("Nur vorhandene, aktive Home-Assistant-Konten dürfen zugeordnet werden")
            if _entry_and_runtime(hass)[1] is not runtime or _current(entry) != before:
                raise EditorError("Konfiguration wurde inzwischen geändert. Bitte aktualisieren.")
            _assert_safe_to_edit(runtime)
            await _save(hass, entry, proposed)
            # Reload replaces the runtime in real HA; record against the NEW
            # snapshot owner so the success entry is persisted, not discarded.
            _, applied_runtime = _entry_and_runtime(hass)
            _record_editor(applied_runtime, connection, "configuration_save", "success", target=describe_changes(before, proposed))
        connection.send_result(msg["id"], {"saved": True})
    except (EditorError, ValueError) as exc:
        if "runtime" in locals():
            # A reload failure happens *after* options were written. It must
            # not be logged as a successful apply or an access denial.
            _record_editor(runtime, connection, "configuration_save",
                           "failed" if isinstance(exc, ReloadFailed) else "denied")
        connection.send_error(msg["id"], "invalid_configuration", str(exc))


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/editor/pin",
    vol.Required("revision"): str,
    vol.Required("user_key"): str,
    vol.Optional("pin"): str,
    vol.Required("operation"): vol.In(["set", "remove"]),
})
@websocket_api.async_response
async def ws_editor_pin(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        entry, runtime = _entry_and_runtime(hass)
        async with _edit_transaction(hass, runtime):
            _assert_safe_to_edit(runtime)
            current = _current(entry)
            if msg["operation"] == "set":
                # PBKDF2 must run outside HA's event loop.
                proposed = await hass.async_add_executor_job(
                    prepare_pin_change, current, msg["user_key"], msg.get("pin"), msg["revision"]
                )
            else:
                proposed = prepare_pin_removal(current, msg["user_key"], msg["revision"])
            if _entry_and_runtime(hass)[1] is not runtime or _current(entry) != current:
                raise EditorError("Konfiguration wurde inzwischen geändert. Bitte aktualisieren.")
            _assert_safe_to_edit(runtime)
            await _save(hass, entry, proposed)
            _, applied_runtime = _entry_and_runtime(hass)
            _record_editor(applied_runtime, connection,
                           "panel_pin_set" if msg["operation"] == "set" else "panel_pin_remove",
                           "success", target=msg["user_key"])
        connection.send_result(msg["id"], {"saved": True})
    except (EditorError, ValueError) as exc:
        if "runtime" in locals():
            _record_editor(runtime, connection, "panel_pin_change",
                           "failed" if isinstance(exc, ReloadFailed) else "denied")
        connection.send_error(msg["id"], "invalid_credential", str(exc))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/editor/learn"})
@callback
def ws_editor_learn(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return the NEXT event from a permitted type; never auto-bind it.

    Does not store raw event payload. HA event-bus events can be forged by
    privileged components; the admin must verify physical origin.
    """
    unsubs = []
    accepted = False

    @callback
    def receive(event) -> None:
        nonlocal accepted
        if accepted:
            return
        accepted = True
        data = event.data
        keys = ("device_id", "address", "type", "subtype", "interface_id")
        safe = {key: data.get(key) for key in keys if type(data.get(key)) in (str, int)}
        safe["event_type"] = event.event_type
        for unsub in unsubs:
            unsub()
        connection.subscriptions.pop(msg["id"], None)
        connection.send_message(websocket_api.event_message(msg["id"], safe))

    for event_type in _ALLOWED_EVENT_TYPES:
        unsubs.append(hass.bus.async_listen(event_type, receive))

    @callback
    def stop() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = stop
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/editor/upgrade_preview",
    vol.Required("revision"): str,
})
@callback
def ws_editor_upgrade_preview(hass: HomeAssistant, connection, msg: dict) -> None:
    """Explicit, read-only upgrade proposal; never changes the live configuration.

    Conflicting legacy modes are rejected by the safety-checked upgrade.
    A second, explicit /editor/save with the original revision is needed.
    """
    try:
        entry, runtime = _entry_and_runtime(hass)
        _assert_safe_to_edit(runtime)
        raw = _current(entry)
        from .editor import revision_of
        if msg["revision"] != revision_of(raw):
            raise EditorError("Konfiguration wurde inzwischen geändert. Bitte neu laden.")
        upgraded = upgrade_legacy_config(json.loads(raw))
        connection.send_result(msg["id"], {
            "configuration": visible_document(json.dumps(upgraded, ensure_ascii=False))["configuration"],
            "revision": msg["revision"],
        })
    except (EditorError, ValueError, TypeError) as exc:
        connection.send_error(msg["id"], "invalid_migration", str(exc))


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/editor/audit",
    vol.Optional("user_key"): str, vol.Optional("source"): str,
    vol.Optional("area"): str, vol.Optional("action"): str,
    vol.Optional("device"): str, vol.Optional("since"): str,
    vol.Optional("until"): str,
})
@callback
def ws_editor_audit(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        _, runtime = _entry_and_runtime(hass)
        options = {k: msg[k] for k in ("user_key", "source", "area", "action", "device", "since", "until") if k in msg}
        principal = next((u for u in runtime.authorization.spec.users if u.key == options.get("user_key")), None)
        if principal:
            events = []
            for key in (principal.key, *principal.ha_user_ids):
                events.extend(runtime.audit.query(**dict(options, user_key=key), limit=250))
            events = sorted({json.dumps(e, sort_keys=True): e for e in events}.values(), key=lambda e: e["at"], reverse=True)[:250]
        else:
            events = runtime.audit.query(**options, limit=250)
        connection.send_result(msg["id"], {"events": events})
    except (EditorError, ValueError) as exc:
        connection.send_error(msg["id"], "invalid_audit_query", str(exc))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/runtime/get"})
@websocket_api.async_response
async def ws_runtime_get(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        _, runtime = _entry_and_runtime(hass)
        user = await hass.auth.async_get_user(connection.user.id)
        if user is None or not user.is_active:
            raise AccessDenied("Home-Assistant-Konto ist nicht aktiv")
        _, runtime = _entry_and_runtime(hass)
        data = runtime_view(runtime, user.id, administrator=user.is_admin)
        if user.is_admin:
            data["diagnostics"] = runtime.diagnostics.view() if runtime.diagnostics is not None else {"enabled": False}
            data["audit"] = runtime.audit.query(limit=100)
        connection.send_result(msg["id"], data)
    except (EditorError, AccessDenied, ValueError) as exc:
        connection.send_error(msg["id"], "access_denied", str(exc))


async def execute_operation(hass,runtime,user_id,operation,mode=None,pin=None,area_key=None):
    from .control import pin_policy
    policy=pin_policy(runtime.configuration,runtime.authorization)
    async with runtime.editor_lock:
        if runtime.commands.retired:raise AccessDenied("Wachturm wurde neu geladen")
        user = await hass.auth.async_get_user(user_id) if user_id else None
        if user_id and (user is None or not user.is_active):raise AccessDenied("HA-Konto nicht aktiv")
        principal=next((u for u in runtime.authorization.spec.users if user_id and user_id in u.ha_user_ids),None)
        if policy[operation] or pin is not None:
            if area_key:
                # Notification mute is intentionally limited to its alarm area.
                async with runtime.commands._panel_pin_lock:
                    try:
                        person=await hass.async_add_executor_job(runtime.authorization.identify_panel_pin,pin)
                        if principal and principal.key!=person.key:raise AccessDenied("PIN gehört nicht zum angemeldeten Benutzer")
                        runtime.commands.execute(person,area_key,operation,mode)
                        runtime.commands._record(operation,"success","panel_pin",area_key,mode,person)
                    except (AccessDenied,AlarmError):
                        runtime.commands._record(operation,"denied","panel_pin",area_key,mode)
                        raise
            else:
                await runtime.commands.global_from_pin_async(hass.async_add_executor_job,pin,operation,mode,principal.key if principal else None)
        else:
            if principal is None:raise AccessDenied("HA account has no alarm identity")
            if area_key:
                runtime.commands.from_ha_user(user_id,area_key,operation,mode)
            else:runtime.commands.global_for_user(principal,operation,mode)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/runtime/command",
    vol.Required("operation"): vol.In(["arm", "disarm", "mute", "acknowledge"]),
    vol.Optional("area"): str, vol.Optional("mode"): str, vol.Optional("notification_token"): str, vol.Optional("pin"): str,
})
@websocket_api.async_response
async def ws_runtime_command(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        _, runtime = _entry_and_runtime(hass)
        user=await hass.auth.async_get_user(connection.user.id)
        if user is None or not user.is_active:raise AccessDenied("HA-Konto nicht aktiv")
        if msg.get("notification_token"):
            runtime.actions.notification_links.validate(msg["notification_token"],msg["operation"],msg.get("area"),runtime.engine)
        await execute_operation(hass,runtime,user.id,msg["operation"],msg.get("mode"),msg.get("pin"),msg.get("area"))
        connection.send_result(msg["id"],runtime_view(runtime,user.id,administrator=user.is_admin))
    except (EditorError, AccessDenied, AlarmError, ValueError) as exc:
        connection.send_error(msg["id"], "command_failed", str(exc))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"):f"{DOMAIN}/runtime/test",vol.Required("operation"):vol.In(["sensor_start","sensor_stop"])})
@websocket_api.async_response
async def ws_runtime_test(hass,connection,msg):
    from .control import SensorTest
    try:
        _,runtime=_entry_and_runtime(hass)
        async with runtime.editor_lock:
            if runtime.commands.retired or _entry_and_runtime(hass)[1] is not runtime:raise EditorError("Wachturm wurde neu geladen")
            if msg['operation']=='sensor_stop':
                runtime.sensor_test=None
                if not runtime.action_simulation or not runtime.action_simulation.view()['active']:runtime.commands.test_until=0
            elif msg['operation']=='sensor_start':
                _assert_safe_to_edit(runtime)
                runtime.sensor_test=SensorTest();runtime.commands.test_until=runtime.sensor_test.until
            else:raise EditorError('Benachrichtigungen ausschließlich im Aktionsdialog testen')
            _record_editor(runtime,connection,'test_'+msg['operation'],'success')
            connection.send_result(msg['id'],{'test_session':runtime.sensor_test.view() if runtime.sensor_test else {'active':False}})
    except (EditorError,ValueError,AccessDenied) as exc:connection.send_error(msg['id'],'test_failed',str(exc))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"):f"{DOMAIN}/runtime/action_test",vol.Required("operation"):vol.In(["start","mute","stop"]),vol.Optional("action"):dict,vol.Optional("area"):str,vol.Optional("mode"):str,vol.Optional("token"):str})
@websocket_api.async_response
async def ws_action_test(hass,connection,msg):
    import asyncio
    from .configuration import parse_specs
    from .ha_actions import HAActionBackend
    from .simulation import ActionSimulation
    try:
        _,runtime=_entry_and_runtime(hass)
        async with runtime.editor_lock:
            if runtime.commands.retired or _entry_and_runtime(hass)[1] is not runtime:raise EditorError('Wachturm wurde neu geladen')
            simulation=runtime.action_simulation
            if msg['operation']!='start':
                if not simulation or msg.get('token')!=simulation.token:raise EditorError('Test ist abgelaufen oder ungültig')
                if msg['operation']=='mute':await simulation.mute()
                else:
                    try:await simulation.stop()
                    finally:runtime.commands.test_until=0
            else:
                raw=runtime.configuration
                area=next((a for a in raw.get('areas',[]) if a['key']==msg.get('area')),None)
                mode=next((m for m in raw.get('modes',[]) if m['key']==msg.get('mode')),None)
                if not area or not mode:raise EditorError('Testbereich und Testmodus auswählen')
                if area['key'] not in mode.get('area_keys',[]):raise EditorError('Der Testmodus verwendet diesen Bereich nicht')
                action=parse_specs({'areas':[{'key':area['key'],'name':area['name'],'modes':[{'key':mode['key'],'name':mode['name']}],'actions':[msg.get('action',{})]}]})[0].actions[0]
                if not action.enabled:raise ValueError('Zum Testen die Aktion im Dialog aktivieren.')
                if action.mode_keys is not None and mode['key'] not in action.mode_keys:raise EditorError('Aktion ist in diesem Testmodus nicht aktiv')
                # Validate the new draft first, then terminate and drain the prior
                # test before any service for the new recipient can be sent.
                if simulation and (not simulation.done.is_set() or runtime.simulation_task and not runtime.simulation_task.done()):
                    await simulation.stop(notify=False)
                    if runtime.simulation_task:await runtime.simulation_task
                    runtime.commands.test_until=0
                _assert_safe_to_edit(runtime)
                controller=runtime.engine.areas.get(area['key'])
                simulation=ActionSimulation(action,HAActionBackend(hass),area['name'],mode['key'],mode['name'],controller.spec.zones if controller else ())
                runtime.action_simulation=simulation;runtime.commands.test_until=simulation.until
                async def run_test():
                    try:await simulation.run()
                    finally:
                        if runtime.action_simulation is simulation:runtime.commands.test_until=0
                try:await asyncio.wait_for(simulation.start(),timeout=60)
                except Exception:
                    await simulation.stop();runtime.commands.test_until=0
                    raise
                creator=getattr(hass,'async_create_background_task',None)
                runtime.simulation_task=creator(run_test(),'Wachturm action simulation') if creator else hass.async_create_task(run_test())
            _record_editor(runtime,connection,'test_action_'+msg['operation'],'success')
            connection.send_result(msg['id'],{'simulation':simulation.view()})
    except (EditorError,ValueError,AccessDenied) as exc:connection.send_error(msg['id'],'test_failed',str(exc))
    except Exception as exc:connection.send_error(msg['id'],'test_failed','Aktionstest fehlgeschlagen: '+type(exc).__name__)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"):f"{DOMAIN}/editor/backup"})
@websocket_api.async_response
async def ws_editor_backup(hass,connection,msg):
    entry,runtime=_entry_and_runtime(hass)
    # Full admin-requested local backup includes PIN hashes, never plaintext PINs.
    connection.send_result(msg["id"],{"configuration":json.loads(_current(entry)),"version":1})


def register_commands(hass: HomeAssistant) -> None:
    for command in (ws_editor_get, ws_editor_save, ws_editor_pin, ws_editor_learn, ws_editor_upgrade_preview, ws_editor_audit,
                    ws_runtime_get, ws_runtime_command, ws_runtime_test, ws_action_test, ws_editor_backup):
        websocket_api.async_register_command(hass, command)
