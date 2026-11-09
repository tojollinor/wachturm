"""Single central native panel; card commands use the same server authorization."""
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity, AlarmControlPanelEntityFeature, AlarmControlPanelState, CodeFormat
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.exceptions import HomeAssistantError
from .control import aggregate, pin_policy
from .const import VERSION, NAME, DEFAULT_ICON, ALARM_ICON, LEGACY_DEFAULT_NAMES
from .security import AccessDenied
from .engine import AlarmError


async def async_setup_entry(hass, entry, async_add_entities):
    registry=er.async_get(hass)
    central=f"{entry.entry_id}_central"
    # Only retired panels owned by this exact integration entry are removed.
    for entity in list(er.async_entries_for_config_entry(registry,entry.entry_id)):
        if entity.domain=="alarm_control_panel" and entity.platform=="ha_alarmzentrale" and entity.unique_id.startswith(entry.entry_id+"_") and entity.unique_id!=central:
            registry.async_remove(entity.entity_id)
    async_add_entities([AlarmCentralEntity(entry,entry.runtime_data)])


class AlarmCentralEntity(AlarmControlPanelEntity):
    _attr_has_entity_name=False
    _attr_should_poll=False
    def __init__(self,entry,runtime):
        self._runtime=runtime
        self._attr_unique_id=f"{entry.entry_id}_central"
        self._attr_name=NAME if not entry.title or entry.title in LEGACY_DEFAULT_NAMES else entry.title
        keys={m["key"] for m in runtime.configuration.get("modes",[])}
        features=AlarmControlPanelEntityFeature(0)
        for key,feature in (("home","ARM_HOME"),("away","ARM_AWAY"),("night","ARM_NIGHT")):
            if key in keys:features|=getattr(AlarmControlPanelEntityFeature,feature)
        self._attr_supported_features=features

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass,self._runtime.signal,self.async_write_ha_state))

    @property
    def icon(self):
        status=aggregate(list(self._runtime.engine.areas.values()))
        if status['alarm']:return ALARM_ICON
        mode=next((m for m in self._runtime.configuration.get('modes',[]) if m['key']==status['mode']),None)
        return mode.get('icon') or DEFAULT_ICON if mode else DEFAULT_ICON
    @property
    def code_arm_required(self):return pin_policy(self._runtime.configuration,self._runtime.authorization)["arm"]
    @property
    def code_format(self):return CodeFormat.NUMBER if any(pin_policy(self._runtime.configuration,self._runtime.authorization).values()) else None
    @property
    def alarm_state(self):
        status=aggregate(list(self._runtime.engine.areas.values()))
        if status["phase"]!="armed":return {"disarmed":AlarmControlPanelState.DISARMED,"arming":AlarmControlPanelState.ARMING,"pending":AlarmControlPanelState.PENDING,"triggered":AlarmControlPanelState.TRIGGERED}[status["phase"]]
        return {"home":AlarmControlPanelState.ARMED_HOME,"away":AlarmControlPanelState.ARMED_AWAY,"night":AlarmControlPanelState.ARMED_NIGHT}.get(status["mode"],AlarmControlPanelState.ARMED_CUSTOM_BYPASS)
    @property
    def extra_state_attributes(self):
        return {"alarmzentrale_panel":True,"integration":"ha_alarmzentrale","version":VERSION,
                "gesamtstatus":aggregate(list(self._runtime.engine.areas.values())),
                "aktiver_modus":aggregate(list(self._runtime.engine.areas.values()))["mode"],
                "pin_required":pin_policy(self._runtime.configuration,self._runtime.authorization),
                "modi":[{"key":m["key"],"name":m["name"],"icon":m.get("icon")} for m in self._runtime.configuration.get("modes",[])]}

    async def _command(self,operation,mode=None,code=None):
        from .websocket import execute_operation
        try:await execute_operation(self.hass,self._runtime,getattr(self._context,"user_id",None),operation,mode,code)
        except (AccessDenied,AlarmError,ValueError) as exc:raise HomeAssistantError(str(exc)) from exc
    async def async_alarm_arm_away(self,code=None):await self._command("arm","away",code)
    async def async_alarm_arm_home(self,code=None):await self._command("arm","home",code)
    async def async_alarm_arm_night(self,code=None):await self._command("arm","night",code)
    async def async_alarm_disarm(self,code=None):await self._command("disarm",code=code)
