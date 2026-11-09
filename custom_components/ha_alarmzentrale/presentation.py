"""Credential-free runtime view with server-side per-person area filtering."""
from .engine import AlarmError
from .security import AccessDenied


def runtime_view(runtime, user_id, *, administrator=False):
    auth = runtime.authorization
    people = [u for u in auth.spec.users if user_id and user_id in u.ha_user_ids]
    person = people[0] if len(people) == 1 else None
    device_names = {(s.get("device_id") or s.get("key")): s.get("name") for s in getattr(runtime, "configuration", {}).get("sensors", [])}
    def readable_info(area, value):
        value = value.removeprefix("entry_timeout:")
        for zone in area.spec.zones:
            if value == zone.key:
                return zone.name
        if value in device_names:
            return device_names[value]
        for action in area.spec.actions:
            if value.startswith(action.key + ":"):
                return action.name
        return value
    areas = []
    for area in sorted(runtime.engine.areas.values(), key=lambda a: a.spec.key != "system_sabotage"): 
        permissions = []
        mode_permissions = []
        for operation in ("arm", "disarm", "mute", "acknowledge"):
            if operation == "arm":
                for mode in area.spec.modes:
                    try:
                        auth.from_ha_user(user_id, area.spec.key, operation, mode.key)
                        mode_permissions.append(mode.key)
                    except AccessDenied:
                        pass
                if mode_permissions:
                    permissions.append(operation)
            else:
                try:
                    auth.from_ha_user(user_id, area.spec.key, operation)
                    permissions.append(operation)
                except AccessDenied:
                    pass
        if not administrator and not permissions:
            continue
        checks = {}
        for mode in area.spec.modes:
            if administrator or mode.key in mode_permissions:
                try:
                    if area.mode != mode.key or area.phase.value == "disarmed":
                        area._preflight(mode.key)
                    checks[mode.key] = {"ready": True, "reason": "", "sources": sum(z.policy(mode.key) is not None for z in area.spec.zones),
                                        "exceptions":[{"name":z.name,"entity_id":z.entity_id,"attribute":z.attribute,"policy":area._initial_policy(z,mode.key).value} for z in area.spec.zones if z.policy(mode.key) is not None and area.zones[z.key].observation.value=="active" and area._initial_policy(z,mode.key).value in {"defer","ignore"}]}
                except AlarmError as exc:
                    reason = str(exc)
                    for device, name in device_names.items():
                        if reason.endswith(": " + device):
                            reason = reason[:-len(device)] + name
                            break
                    checks[mode.key] = {"ready": False, "reason": reason}
        areas.append({"key": area.spec.key, "name": area.spec.name,
                      "icon": next((a.get("icon") for a in getattr(runtime,"configuration",{}).get("areas",[]) if a["key"]==area.spec.key), None),
                      "mode": area.mode, "phase": area.phase.value,
                      "alarm": area.alarm_latched, "ready": area.monitoring_ready,
                      "reconciling": area.reconciling, "arming_block_reason": area.arming_block_reason,
                      "phase_deadline": area.phase_deadline, "alarm_deadline": area.alarm_deadline,
                      "pending_source":next((z.name for z in area.spec.zones if z.key==area.pending_zone),None),
                      "action_window_active": area.action_window_active, "alarm_actions": sum(a.when=="alarm" for a in area.spec.actions),
                      "permissions": permissions, "arm_modes": mode_permissions, "checks": checks,
                      "zones": {key: dict(value, name=next(z.name for z in area.spec.zones if z.key == key))
                                for key, value in area.zone_summary().items()},
                      "alarm_sources": sorted(area.alarm_sources),
                      "events": [{"at": e.when, "kind": e.kind, "info": readable_info(area, e.info), "event_id": e.event_id} for e in area.history[-12:]],
                      "modes": [{"key": m.key, "name": m.name, "icon": next((v.get("icon") for v in getattr(runtime,"configuration",{}).get("modes",[]) if v["key"]==m.key), None)} for m in area.spec.modes]})
    mode_access = {}
    mode_issues = {}
    for key in {m.key for area in runtime.engine.areas.values() for m in area.spec.modes}:
        targets = [area for area in runtime.engine.areas.values() if any(m.key == key for m in area.spec.modes)]
        try:
            for area in targets:
                auth.from_ha_user(user_id, area.spec.key, "arm", key)
            mode_access[key] = True
        except AccessDenied:
            mode_access[key] = False
            missing = [a.spec.name for a in targets if a.spec.key not in {v["key"] for v in areas} or key not in next((v["arm_modes"] for v in areas if v["key"] == a.spec.key), [])]
            mode_issues[key] = "Berechtigung fehlt" + (": " + ", ".join(missing) if administrator else " für einen zugeordneten Bereich")
    visible_modes = {m["key"] for area in areas for m in area["modes"]}
    active = [area for area in areas if area["phase"] != "disarmed"]
    from .control import pin_policy, aggregate
    policy=pin_policy(getattr(runtime,"configuration",{}),auth)
    allowed_ops={op:bool(areas) and all(op in a["permissions"] for a in areas if a["phase"]!="disarmed" or a["alarm"]) and any(a["phase"]!="disarmed" or a["alarm"] for a in areas) for op in ("disarm","mute","acknowledge")}
    return {"pin_available":any(u.enabled and u.panel_pin_hash for u in auth.spec.users),"summary":aggregate([runtime.engine.areas[a["key"]] for a in areas]),"pin_required":policy,
            "operation_access":allowed_ops,"test_session":getattr(runtime,"sensor_test",None).view() if getattr(runtime,"sensor_test",None) else {"active":False},
            "simulation":getattr(runtime,"action_simulation",None).view() if administrator and getattr(runtime,"action_simulation",None) else {"active":False},
            "areas_state": areas, "identity": person.name if person else None,
            "mode_issues": {key: issue for key, issue in mode_issues.items() if key in visible_modes},
            "mode_access": {key: allowed for key, allowed in mode_access.items() if key in visible_modes},
            "global_disarm_allowed": len(areas) == len(runtime.engine.areas) and bool(active)
                                      and all("disarm" in a["permissions"] for a in active),
            "can_administer": administrator,
            "safe_to_edit": administrator and not runtime.commands.test_active() and all(a.phase.value == "disarmed" and not a.alarm_latched and not a.reconciling
                                and not a.action_window_active for a in runtime.engine.areas.values())
                            and not runtime.actions.in_flight and not any(not q.empty() for q in runtime.actions.queues.values())}
