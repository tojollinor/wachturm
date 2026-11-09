"""Action timing compatibility, event routing and diagnostic recovery."""
import importlib
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from test_sabotage import config, engine, fixture
from test_workflows import FakeBackend, actions


class TimingTests(unittest.IsolatedAsyncioTestCase):
    def area(self, when, timings):
        spec=config.parse_specs(fixture(False))[0]
        spec=replace(spec, actions=tuple(engine.ActionSpec(t,t,'script','test',data={'test':True},when=when,timing=t) for t in timings))
        area=engine.AreaController(spec);area.update_sensor('binary_sensor.door','off')
        return area, actions.ActionRunner(area,FakeBackend())

    async def test_arm_request_exit_delay_and_completion_are_distinct(self):
        area,runner=self.area('armed',['before','during','after'])
        area.spec=replace(area.spec,modes=tuple(replace(m,exit_delay_seconds=10) for m in area.spec.modes))
        events=[];area.subscribe(events.append)
        area.arm('night')
        self.assertEqual([e.kind for e in events],['arm_requested','arming'])
        for event in events:await runner.handle(event)
        self.assertEqual(len(runner.backend.calls),2)
        now=area.phase_deadline+1
        area._clock=lambda:now
        area.tick()
        await runner.handle(area.last_event)
        self.assertEqual(len(runner.backend.calls),3)

    async def test_failed_preflight_has_no_before_action(self):
        area,runner=self.area('armed',['before'])
        area.update_sensor('binary_sensor.door','on')
        events=[];area.subscribe(events.append)
        with self.assertRaises(engine.AlarmError):area.arm('night')
        self.assertNotIn('arm_requested',[e.kind for e in events])

    async def test_no_exit_delay_has_no_during_action(self):
        area,runner=self.area('armed',['before','during','after'])
        area.spec=replace(area.spec,modes=tuple(replace(m,exit_delay_seconds=0) for m in area.spec.modes))
        events=[];area.subscribe(events.append)
        area.arm('night')
        self.assertEqual([e.kind for e in events],['arm_requested','armed'])
        for event in list(events):await runner.handle(event)
        self.assertEqual(len(runner.backend.calls),2)

    async def test_alarm_before_only_runs_for_actual_entry_delay(self):
        area,runner=self.area('alarm',['before','during'])
        area.arm('night')
        events=[];area.subscribe(events.append)
        area.begin_entry_delay(area.spec.zones[0].key,10)
        await runner.handle(events[0])
        self.assertEqual(len(runner.backend.calls),1)
        area.trigger()
        await runner.handle(area.last_event)
        self.assertEqual(len(runner.backend.calls),2)

    async def test_diagnostic_problem_and_recovery_route_to_separate_actions(self):
        for when,problem,recovery in [('battery','battery','battery_recovered'),
                                       ('offline','offline','online'),
                                       ('unstable','unstable','stable_recovery')]:
            area,runner=self.area(when,['during','after'])
            area._emit('diagnostic_'+problem,'device')
            await runner.handle(area.last_event)
            self.assertEqual(len(runner.backend.calls),1)
            area._emit('diagnostic_'+recovery,'device')
            await runner.handle(area.last_event)
            self.assertEqual(len(runner.backend.calls),2)

    async def test_alarm_end_runs_once_for_disarm_or_acknowledgement(self):
        for end in ['disarm','acknowledge']:
            area,runner=self.area('alarm',['during','after'])
            events=[];area.subscribe(events.append)
            area.arm('night');area.trigger()
            for event in events:await runner.handle(event)
            self.assertEqual(len(runner.backend.calls),1)
            events.clear();getattr(area,end)()
            for event in events:await runner.handle(event)
            self.assertEqual(len(runner.backend.calls),2)
            events.clear();getattr(area,end)()
            for event in events:await runner.handle(event)
            self.assertEqual(len(runner.backend.calls),2)

    def test_defaults_and_impossible_combinations(self):
        for when,expected in [('armed','after'),('disarmed','after'),('alarm','during'),('unstable','during')]:
            self.assertEqual(engine.ActionSpec('a','A','script','test',data={'test':True},when=when).effective_timing,expected)
        for when in ['battery','offline','unstable','disarmed']:
            with self.assertRaises(ValueError):engine.ActionSpec('a','A','script','test',data={'test':True},when=when,timing='before')
        with self.assertRaises(ValueError):engine.ActionSpec('a','A','switch','turn_on',when='alarm',timing='after',timed=True)
        raw=fixture(False);raw['areas'][0]['actions']=[{'key':'a','name':'A','domain':'script','service':'test','data':{'test':True},'when':'armed','timing':'during'}]
        self.assertEqual(config.parse_specs(raw)[0].actions[0].timing,'during')

    def test_diagnostic_recovery_is_an_edge_not_initial_or_repeated_health(self):
        diagnostic=importlib.import_module('alarm_core_tests.diagnostics')
        delta=timedelta(seconds=10)
        monitor=diagnostic.Diagnostics(diagnostic.Settings(delta,delta,2,delta,delta,2,100,delta))
        now=datetime.now(timezone.utc)
        self.assertEqual([e.kind for e in monitor.battery_update('d','good',now)],['battery'])
        monitor.battery_update('d','low',now)
        self.assertEqual([e.kind for e in monitor.battery_update('d','good',now)],['battery','battery_recovered'])
        self.assertEqual(monitor.battery_update('d','good',now),[])
        self.assertNotIn('online',[e.kind for e in monitor.connectivity_update('d','online',now)])
        monitor.connectivity_update('d','offline',now)
        self.assertIn('online',[e.kind for e in monitor.connectivity_update('d','online',now)])
        self.assertEqual(monitor.connectivity_update('d','online',now),[])
