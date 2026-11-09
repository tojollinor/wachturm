"""Bounded, isolated action simulation. Never operates the real alarm engine."""
import asyncio
import time
from dataclasses import replace
from secrets import token_urlsafe
from urllib.parse import urlencode

from .actions import ActionRunner
from .engine import AreaController, AreaSpec, ModeSpec, ZoneSpec, ZoneModeSpec, Phase, Observation


class SimulationBackend:
    def __init__(self, backend, token, action):
        self.backend=backend
        self.token=token
        self.action=action
        self.notification_links=self
        self.notification_timezone=getattr(backend,'notification_timezone',None)

    def url(self, operation, area):
        return '/ha-alarmzentrale?'+urlencode({'az_test':self.token,'az_test_action':'mute' if operation=='mute' else 'stop'})

    def read_state(self, entity):return self.backend.read_state(entity)
    def read_revision(self, entity):return self.backend.read_revision(entity)

    async def call(self, domain, service, *, entity_ids, data):
        allowed=domain==self.action.domain and (service==self.action.service or self.action.timed and service=='turn_off')
        if not allowed:raise ValueError('Test darf ausschließlich die ausgewählte Aktion ausführen')
        if any(e not in self.action.entity_ids for e in entity_ids):raise ValueError('Testziel gehört nicht zur ausgewählten Aktion')
        if domain=='notify' and data.get('message')=='TTS':
            data={**data,'data':{**data.get('data',{}),'tts_text':'TEST · '+str(data.get('data',{}).get('tts_text',''))}}
        if domain=='notify' and data.get('message')=='command_webview':
            data={**data,'data':{**data.get('data',{}),'command':'/ha-alarmzentrale?alarm=1&az_simulation='+self.token}}
        if domain=='notify' and not str(data.get('message','')).startswith('command_') and data.get('message') not in {'TTS','clear_notification'}:
            data={**data,'title':'TEST · '+str(data.get('title') or 'Wachturm'),'message':'TEST · '+str(data.get('message',''))}
            extra={**data.get('data',{})}
            extra.update(tag='alarmzentrale_test_'+self.token,group='alarmzentrale_test')
            extra.update(url='/ha-alarmzentrale?alarm=1&az_simulation='+self.token,clickAction='/ha-alarmzentrale?alarm=1&az_simulation='+self.token)
            data['data']=extra
        await self.backend.call(domain,service,entity_ids=entity_ids,data=data)


class ActionSimulation:
    def __init__(self, action, backend, area_name, mode_key, mode_name, zones=(), *, duration=60):
        self.token=token_urlsafe(24)
        self.until=time.time()+duration
        self.done=asyncio.Event()
        self.error=''
        # A real on/off output is owned only if it was OFF before the test.
        if action.domain in {'siren','light','switch'} and action.service=='turn_on':
            action=replace(action,timed=True)
        test_zones=tuple(replace(z,mode_policies=(ZoneModeSpec(mode_key),)) for z in zones) if zones else (ZoneSpec('test_contact','Testkontakt','binary_sensor.alarmzentrale_test',(ZoneModeSpec(mode_key),)),)
        zone=test_zones[0]
        spec=AreaSpec('simulation',area_name,(ModeSpec(mode_key,mode_name,mute_after_seconds=None),),zones=test_zones,actions=(action,))
        self.area=AreaController(spec)
        self.area.mode=mode_key;self.area.phase=Phase.ARMED
        for runtime in self.area.zones.values():runtime.observation=Observation.NORMAL;runtime.verified=True
        self.area.zones[zone.key].observation=Observation.ACTIVE
        self.area.trigger(zone.key)
        self.runner=ActionRunner(self.area,SimulationBackend(backend,self.token,action))
        self.action=action

    async def start(self):
        event=self.area.last_event
        kind={'armed':'armed','disarmed':'disarmed','battery':'diagnostic_battery','offline':'diagnostic_offline','unstable':'diagnostic_unstable'}.get(self.action.when,'alarm_started')
        await self.runner._start(self.action,replace(event,kind=kind))
        if self.area.last_event.kind=='action_failed':raise ValueError('Testaktion fehlgeschlagen; Dienst und Empfänger prüfen')

    async def mute(self):
        if self.done.is_set() or time.time()>=self.until:raise ValueError('Test ist bereits beendet')
        self.area.mute_actions()
        await self.runner.handle(self.area.last_event)

    async def stop(self, *, notify=True):
        if self.done.is_set():return
        self.done.set()
        self.area.disarm()
        # Do not dispatch a configured disarmed action a second time on cleanup.
        self.runner.tts_due.clear()
        try:
            if self.runner.tts_started:
                await self.runner.backend.call('notify',self.action.service,entity_ids=(),data={'message':'command_stop_tts','data':{'priority':'high','ttl':0}})
                self.runner.tts_started.clear()
        finally:
            await self.runner.stop_timed_outputs()
        if notify and self.action.domain=='notify' and self.action.notification.get('platform') in {'android','ios'}:
            await self.runner._notify(self.action,self.area.last_event,quiet=True)
        elif not notify and self.action.domain=='notify':
            await self.runner.backend.call('notify',self.action.service,entity_ids=(),data={'message':'clear_notification','data':{'tag':'alarmzentrale_test_'+self.token,'priority':'high','ttl':0}})

    async def run(self):
        try:
            while not self.done.is_set() and time.time()<self.until:
                try:await asyncio.wait_for(self.done.wait(),timeout=min(1,max(.01,self.until-time.time())))
                except asyncio.TimeoutError:await self.runner.repeat_tts()
        finally:
            await self.stop()

    def view(self):
        from .control import aggregate
        area=self.area
        preview={'summary':aggregate([area]),'pin_required':{},'operation_access':{'disarm':True,'mute':True,'acknowledge':True},'test_session':{'active':False},'mode_access':{},'areas_state':[{'key':area.spec.key,'name':area.spec.name,'phase':area.phase.value,'mode':area.mode,'alarm':area.alarm_latched,'alarm_sources':sorted(area.alarm_sources),'action_window_active':area.action_window_active,'zones':{z.key:{**area.zone_summary()[z.key],'name':z.name} for z in area.spec.zones},'modes':[{'key':m.key,'name':m.name} for m in area.spec.modes]}]}
        return {'active':not self.done.is_set(),'token':self.token,'until':self.until,'muted':not area.action_window_active,'preview':preview}
