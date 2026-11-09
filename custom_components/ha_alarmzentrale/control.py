"""Central operation policy, status and explicit non-operational sensor tests."""
import time
from dataclasses import dataclass, field
from .engine import Phase

OPERATIONS = ("arm", "disarm", "mute", "acknowledge")


def pin_policy(document, authorization):
    raw = document.get("operation_pin", {})
    if not isinstance(raw, dict) or set(raw) - set(OPERATIONS) or any(type(v) is not bool for v in raw.values()):
        raise ValueError("Invalid operation PIN policy")
    defaults = any(u.panel_pin_hash for u in authorization.spec.users)
    return {op: raw.get(op, defaults) for op in OPERATIONS}


def aggregate(areas, modes=()):
    active = [a for a in areas if a.phase != Phase.DISARMED]
    phase = next((p for p in (Phase.TRIGGERED, Phase.PENDING, Phase.ARMING, Phase.ARMED) if any(a.phase == p for a in active)), Phase.DISARMED)
    keys = {a.mode for a in active}
    mode = next(iter(keys)) if len(keys) == 1 else None
    starts=[a.alarm_started_at for a in areas if a.alarm_latched]
    starts=[value for value in starts if value is not None]
    return {"phase": phase.value, "mode": mode, "mixed": len(keys) > 1,
            "alarm": any(a.alarm_latched for a in areas),
            "muted":any(a.alarm_latched for a in areas) and not any(a.alarm_latched and a.action_window_active for a in areas),
            "alarm_since":min(starts) if starts else None,
            "deadline": min((a.phase_deadline for a in active if a.phase == phase and a.phase_deadline is not None), default=None)}


@dataclass
class SensorTest:
    started: float = field(default_factory=time.time)
    until: float = field(default_factory=lambda: time.time()+900)
    samples: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)

    def observe(self, area, event):
        if event.kind not in {"zone_active", "zone_normal"}:
            return
        zone = next((z for z in area.spec.zones if z.key == event.info), None)
        if zone is None:
            return
        key = zone.entity_id + ":" + (zone.attribute or "")
        item = self.samples.setdefault(key, {"name":zone.name,"entity_id":zone.entity_id,"opened":False,"closed":False})
        if event.kind == "zone_active":item["opened"]=True
        elif item["opened"]:item["closed"]=True

    def observe_config(self, document, entity_id, state, attributes=None):
        for sensor in document.get("sensors",[]):
            sources=list(sensor.get("entities",[]))
            if sensor.get("sabotage"):sources.append({**sensor["sabotage"],"name":sensor["name"]+" · Sabotage"})
            for source in sources:
                if source.get("entity_id")!=entity_id:continue
                attribute=source.get("attribute") or ""
                value=(attributes or {}).get(attribute) if attribute else state
                if isinstance(value,bool):value=str(value).lower()
                elif value is not None:value=str(value)
                observation="fault" if state in {None,"unknown","unavailable"} else "active" if value in source.get("active_states",[]) else "normal" if value in source.get("normal_states",[]) else "fault"
                key=entity_id+":"+attribute
                previous=self.last.get(key);self.last[key]=observation
                item=self.samples.setdefault(key,{"name":source.get("name") or sensor["name"],"entity_id":entity_id,"attribute":attribute,"opened":False,"closed":False})
                if observation=="active" and previous!="active":item["opened"]=True
                elif observation=="normal" and previous=="active" and item["opened"]:item["closed"]=True

    def view(self):
        return {"active":time.time()<self.until,"until":self.until,"samples":list(self.samples.values())}
