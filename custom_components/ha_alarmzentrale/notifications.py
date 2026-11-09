"""Limited text tokens and authenticated, alarm-bound mobile action links."""
from copy import deepcopy
from datetime import datetime, timezone
from secrets import token_urlsafe
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

TOKENS = ("alarm_message", "sensor", "area", "mode", "time")


class NotificationLinks:
    def __init__(self):
        self.pending = {}

    @staticmethod
    def generation(area):
        starts = [e.event_id or e.when for e in area.history if e.kind == "alarm_started"]
        return (area.mode, starts[-1] if starts else None, area.trigger_count)

    def url(self, operation, area):
        generation = self.generation(area)
        self.pending = {k: v for k, v in self.pending.items() if v[0] != area.spec.key or v[1] == generation}
        existing = next((k for k, v in self.pending.items() if v == (area.spec.key, generation, operation)), None)
        token = existing or token_urlsafe(32)
        if len(self.pending) >= 1000:
            self.pending.clear()
        self.pending[token] = (area.spec.key, generation, operation)
        return "/ha-alarmzentrale?" + urlencode({"az_action": operation, "az_area": area.spec.key, "az_token": token})

    def validate(self, token, operation, area_key, engine):
        record = self.pending.get(token)
        if not record or record[2] != operation:
            raise ValueError("Benachrichtigungsaktion ist abgelaufen oder ungültig")
        area = engine.areas.get(record[0])
        if not area or not area.alarm_latched or self.generation(area) != record[1]:
            raise ValueError("Dieser Alarm ist nicht mehr aktiv")
        if operation=="mute" and not area.action_window_active:
            raise ValueError("Alarm ist bereits stumm")
        if (operation == "mute" and area_key != record[0]) or (operation == "disarm" and area_key is not None):
            raise ValueError("Ungültiger Geltungsbereich der Benachrichtigung")


def notification_payload(action, area, event, *, links=None, quiet=False, timezone_name=None, speech=False):
    data = deepcopy(action.data)
    if speech and action.notification.get('tts_text','').strip():data['message']=action.notification['tts_text']
    source_keys = {key.removeprefix("entry_timeout:") for key in area.alarm_sources}
    sources = [z.name for z in area.spec.zones if z.key in source_keys]
    sensor = next((z.name for z in area.spec.zones if z.key == event.info), ", ".join(sources))
    mode = next((m.name for m in area.spec.modes if m.key == (event.mode or area.mode)), "—")
    if event.kind == "disarmed":
        message = "Anlage deaktiviert. Alarm beendet."
    elif event.kind == "alarm_acknowledged":
        message = "Alarm quittiert."
    elif event.kind == "actions_muted":
        message = "Sirene stummgeschaltet. Alarm bleibt aktiv."
    elif event.kind == "zone_normal":
        message = f"{sensor}: Normalzustand. Alarm bleibt bis zur Quittierung aktiv."
    else:
        message = "Ausgelöst: " + (", ".join(sources) or sensor or event.info or area.spec.name)
    incident=next((e for e in reversed(area.history) if e.kind=="alarm_started"),None)
    keep_alarm=quiet and area.alarm_latched
    status=message if event.kind in {"actions_muted","zone_normal"} else ""
    if keep_alarm:
        message="Ausgelöst: "+(", ".join(sources) or (incident.info if incident else sensor) or area.spec.name)
        sensor=", ".join(sources) or sensor
        if incident:mode=next((m.name for m in area.spec.modes if m.key==incident.mode),mode)
    stamp=area.alarm_started_at if keep_alarm and area.alarm_started_at is not None else incident.when if keep_alarm and incident else event.when
    values = {"alarm_message": message, "sensor": sensor or "—", "area": area.spec.name,
              "mode": mode, "time": datetime.fromtimestamp(stamp, timezone.utc).astimezone(ZoneInfo(timezone_name) if timezone_name else None).strftime("%d.%m.%Y %H:%M:%S")}
    for field in ("message", "title"):
        text = str(data.get(field, ""))
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        if field in data:
            data[field] = text
    options = action.notification
    if speech:return data
    if not options or options.get("platform", "standard") == "standard":
        return data
    extra = data.setdefault("data", {})
    extra.update(tag=f"alarmzentrale_{area.spec.key}_{action.key}", group="alarmzentrale", url="/ha-alarmzentrale?alarm=1", clickAction="/ha-alarmzentrale?alarm=1")
    if quiet:
        if keep_alarm:
            if status:data["message"]=data.get("message",message)+"\n"+status
            if not area.action_window_active and event.kind!="actions_muted":data["message"]=data.get("message",message)+"\nSirene stummgeschaltet."
        else:data["message"] = message
    critical = options.get("critical", False) and not quiet
    if options["platform"] == "ios":
        extra["push"] = {"interruption-level":"critical", "sound": {"name": "default", "critical": 1, "volume": 1.0}} if critical else {"sound": "none", "interruption-level": "passive"} if quiet else {"sound": "default"}
    else:
        extra.update(priority="high", ttl=0, persistent=options.get("persistent", True) and area.alarm_latched,
                     sticky=options.get("persistent", True) and area.alarm_latched,
                     visibility="public", notification_icon="mdi:shield-alert",
                     channel="Alarmzentrale Status" if quiet else "alarm_stream" if options.get("alarm_stream") else "Alarmzentrale Alarm" if critical else "Alarmzentrale Hinweise", importance="high" if not quiet else "low",
                     alert_once=quiet)
    buttons = []
    if options.get("buttons", True) and area.alarm_latched and links:
        for operation, title in (("mute", "Stumm"), ("disarm", "Deaktivieren")):
            if operation=="mute" and not area.action_window_active:continue
            buttons.append({"action": "URI", "title": title, "uri": links.url(operation, area), "authenticationRequired": True})
    if options["platform"]=="ios" and critical:
        extra["tag"] += "_critical_" + str(event.event_id or event.when)
    extra["actions"] = buttons
    return data


def tts_payload(text, maximum=False):
    return {"message":"TTS","data":{"ttl":0,"priority":"high","media_stream":"alarm_stream_max" if maximum else "alarm_stream","tts_text":text}}
