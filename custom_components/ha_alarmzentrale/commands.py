"""Single audited, authorized entry point for all human alarm commands."""
from __future__ import annotations

import asyncio
import time

from .engine import AlarmEngine, AlarmError
from .security import AccessDenied, AlarmUser, Authorization
from .audit import AuditTrail
from .event_context import correlated


class AlarmCommands:
    def __init__(self, engine: AlarmEngine, authorization: Authorization, audit: AuditTrail | None = None):
        self.engine = engine
        self.authorization = authorization
        self.audit = audit if audit is not None else AuditTrail()
        # Serialize the entire credential verification path, including lockout
        # accounting. PBKDF2 itself runs on HA's executor, never its event loop.
        self._panel_pin_lock = asyncio.Lock()
        self.configuration_locked = False
        self.retired = False
        self.test_until = 0.0

    def test_active(self):
        return time.time() < self.test_until

    @correlated
    def global_for_user(self, user, operation, mode=None, source="ha_account", device=None):
        if self.configuration_locked or self.retired or self.test_active():
            raise AccessDenied("Bedienung während Konfiguration oder Sensortest gesperrt")
        if not self.authorization._enabled(user):
            raise AccessDenied("Benutzer deaktiviert oder außerhalb erlaubter Zeiten")
        if operation not in {"arm","disarm","mute","acknowledge"}:
            raise AccessDenied("Invalid operation")
        targets = [a for a in self.engine.areas.values() if (any(m.key == mode for m in a.spec.modes) if operation == "arm" else a.phase.value != "disarmed" or a.alarm_latched)]
        if operation == "arm" and not targets:
            raise AlarmError("Dieser Modus hat keine Überwachungsbereiche")
        leaving = [a for a in self.engine.areas.values() if operation=="arm" and a not in targets and a.phase.value!="disarmed"]
        try:
            for area in targets:
                self.authorization._check(user,area.spec.key,operation,mode if operation=="arm" else None)
                if operation=="arm" and (area.mode!=mode or area.phase.value=="disarmed"):area._preflight(mode)
            for area in leaving:
                self.authorization._check(user,area.spec.key,"disarm",None)
                if area.alarm_latched:raise AlarmError("Aktiven Alarm zuerst deaktivieren oder quittieren")
        except (AccessDenied,AlarmError):
            self._record(operation,"denied",source,None,mode,user)
            raise
        for area in leaving:
            self.execute(user,area.spec.key,"disarm")
            self._record("disarm","success",source,area.spec.key,None,user,device)
        for area in targets:
            self.execute(user,area.spec.key,operation,mode if operation=="arm" else None)
            self._record(operation,"success",source,area.spec.key,mode,user,device)
        return tuple(a.spec.key for a in targets)

    @correlated
    async def global_from_pin_async(self, executor, pin, operation, mode=None, expected_user=None):
        async with self._panel_pin_lock:
            try:
                user = await executor(self.authorization.identify_panel_pin,pin)
                if expected_user is not None and user.key != expected_user:
                    raise AccessDenied("PIN gehört nicht zum angemeldeten Alarmbenutzer")
                return self.global_for_user(user,operation,mode,"panel_pin")
            except (AccessDenied,AlarmError):
                self._record(operation,"denied","panel_pin",None,mode)
                raise

    def _area(self, area_key: str):
        area = self.engine.areas.get(area_key)
        if area is None:
            raise AccessDenied("Unknown alarm area")
        return area

    def execute(self, user: AlarmUser, area_key: str, operation: str, mode: str | None = None) -> None:
        if self.configuration_locked or self.retired or self.test_active():
            raise AccessDenied("Konfiguration wird gespeichert. Bitte kurz warten.")
        area = self._area(area_key)
        # Check again immediately before mutating, even for a trusted binding.
        self.authorization._check(user, area_key, operation, mode)
        if operation == "arm":
            if mode is None:
                raise AccessDenied("A target mode is required")
            area.arm(mode)
        elif operation == "disarm":
            area.disarm()
        elif operation == "mute":
            area.mute_actions()
        elif operation == "acknowledge":
            area.acknowledge()
        else:
            raise AccessDenied("Invalid operation")
        area._emit("access_granted", f"{user.key}:{operation}:{mode or ''}")

    def _record(self, action, outcome, source, area, mode=None, user=None, device=None):
        self.audit.record(action=action, outcome=outcome, source=source,
                          area=area, mode=mode,
                          user_key=user.key if user else None,
                          user_name=user.name if user else None, device=device,
                          identity_confidence=('authenticated_account' if source == 'ha_account' and user
                                               else 'credential_match' if source == 'panel_pin' and user
                                               else 'device_assignment_only' if source in {'homematic','entity','event'}
                                               else 'unverified'))

    @correlated
    def mode_from_ha_user(self, ha_user_id: str | None, mode_key: str, operation: str) -> tuple[str, ...]:
        """Authorize ALL affected areas and preflight before the first mutation."""
        if self.configuration_locked or self.retired or self.test_active():
            raise AccessDenied("Konfiguration wird gespeichert. Bitte kurz warten.")
        if operation not in {"arm", "disarm"}:
            raise AccessDenied("Invalid mode operation")
        targets = [a for a in self.engine.areas.values()
                   if (any(m.key == mode_key for m in a.spec.modes) if operation == "arm" else a.mode == mode_key)]
        if not targets:
            raise AlarmError("Kein passender aktiver Alarmbereich für diesen Modus")
        matched = [u for u in self.authorization.spec.users if ha_user_id and ha_user_id in u.ha_user_ids]
        attempted = matched[0] if len(matched) == 1 else None
        principals = []
        try:
            for area in targets:
                principals.append(self.authorization.from_ha_user(ha_user_id, area.spec.key, operation,
                                                                  mode_key if operation == "arm" else None))
            if operation == "arm":
                for area in targets:
                    if area.mode != mode_key or area.phase.value == "disarmed":
                        area._preflight(mode_key)
        except (AccessDenied, AlarmError):
            for area in targets:
                self._record(operation, "denied", "ha_account", area.spec.key, mode_key, attempted)
            raise
        for area, principal in zip(targets, principals):
            self.execute(principal, area.spec.key, operation, mode_key if operation == "arm" else None)
            self._record(operation, "success", "ha_account", area.spec.key, mode_key, principal)
        return tuple(a.spec.key for a in targets)

    @correlated
    def disarm_all_from_ha_user(self, ha_user_id: str | None) -> tuple[str, ...]:
        if self.configuration_locked or self.retired or self.test_active():
            raise AccessDenied("Konfiguration wird gespeichert")
        targets = [a for a in self.engine.areas.values() if a.phase.value != "disarmed"]
        matched = [u for u in self.authorization.spec.users if ha_user_id and ha_user_id in u.ha_user_ids]
        attempted = matched[0] if len(matched) == 1 else None
        principals = []
        try:
            for area in targets:
                principals.append(self.authorization.from_ha_user(ha_user_id, area.spec.key, "disarm"))
        except AccessDenied:
            for area in targets:
                self._record("disarm", "denied", "ha_account", area.spec.key, user=attempted)
            raise
        for area, principal in zip(targets, principals):
            self.execute(principal, area.spec.key, "disarm")
            self._record("disarm", "success", "ha_account", area.spec.key, user=principal)
        return tuple(a.spec.key for a in targets)

    @correlated
    def from_ha_user(self, ha_user_id: str | None, area_key: str, operation: str, mode: str | None = None) -> None:
        area = self._area(area_key)
        user = next((u for u in self.authorization.spec.users if ha_user_id and ha_user_id in u.ha_user_ids), None)
        try:
            user = self.authorization.from_ha_user(ha_user_id, area_key, operation, mode)
            self.execute(user, area_key, operation, mode)
        except (AccessDenied, AlarmError):
            self._record(operation, 'denied', 'ha_account', area_key, mode, user)
            area._emit("access_denied", f"ha_user:{operation}")
            raise
        self._record(operation, 'success', 'ha_account', area_key, mode, user)

    @correlated
    def from_panel_pin(self, pin: str | None, area_key: str, operation: str, mode: str | None = None) -> None:
        area = self._area(area_key)
        try:
            user = self.authorization.from_panel_pin(pin, area_key, operation, mode)
            self.execute(user, area_key, operation, mode)
        except (AccessDenied, AlarmError):
            self._record(operation, 'denied', 'panel_pin', area_key, mode)
            if self.authorization.lockout_until > self.authorization.epoch():
                self._record('panel_pin_lockout', 'denied', 'panel_pin', area_key)
            area._emit("access_denied", f"panel:{operation}")
            raise
        self._record(operation, 'success', 'panel_pin', area_key, mode, user)

    @correlated
    async def from_panel_pin_async(self, executor, pin: str | None,
                                   area_key: str, operation: str,
                                   mode: str | None = None) -> None:
        """Verify credential in an executor, mutate alarm state on HA event loop.

        Concurrent PIN attempts share a lock so their failed-attempt counters
        and lockout window cannot race. Neither credentials nor hash material
        are passed into audit records.
        """
        area = self._area(area_key)
        async with self._panel_pin_lock:
            try:
                user = await executor(self.authorization.from_panel_pin,
                                      pin, area_key, operation, mode)
                self.execute(user, area_key, operation, mode)
            except (AccessDenied, AlarmError):
                self._record(operation, 'denied', 'panel_pin', area_key, mode)
                if self.authorization.lockout_until > self.authorization.epoch():
                    self._record('panel_pin_lockout', 'denied', 'panel_pin', area_key)
                area._emit('access_denied', f'panel:{operation}')
                raise
            self._record(operation, 'success', 'panel_pin', area_key, mode, user)

    @correlated
    def from_event(self, event_type: str, data: dict) -> bool:
        try:
            match = self.authorization.from_event(event_type, data)
        except AccessDenied:
            for binding in self.authorization.spec.bindings:
                if binding.matches(event_type, data):
                    if binding.area in self.engine.areas:
                        self._area(binding.area)._emit("access_denied", f"device:{binding.key}")
                    self._record(binding.operation, 'denied', ('homematic' if binding.input_type=='homematic' else 'event' if binding.input_type=='event' else 'entity'), binding.area,
                                 binding.mode, device=binding.key)
            return False
        if match is None:
            return False
        user, binding = match
        try:
            if binding.scope == "mode":
                self.global_for_user(user,binding.operation,binding.mode,('homematic' if binding.input_type=='homematic' else 'event' if binding.input_type=='event' else 'entity'),binding.key)
                return True
            self.execute(user, binding.area, binding.operation, binding.mode)
        except (AccessDenied, AlarmError):
            if binding.area in self.engine.areas:
                self._area(binding.area)._emit("access_denied", f"device:{binding.key}")
            self._record(binding.operation, 'denied', ('homematic' if binding.input_type=='homematic' else 'event' if binding.input_type=='event' else 'entity'), binding.area,
                         binding.mode, user, binding.key)
            return False
        self._record(binding.operation, 'success', ('homematic' if binding.input_type=='homematic' else 'event' if binding.input_type=='event' else 'entity'), binding.area,
                     binding.mode, user, binding.key)
        return True
