"""Action lifecycle, independent output timers and causal history."""
from dataclasses import replace
import importlib
import unittest
from unittest.mock import patch
from test_sabotage import config, engine, fixture
from test_workflows import actions, FakeBackend

context = importlib.import_module('alarm_core_tests.event_context')


class ActionControlTests(unittest.IsolatedAsyncioTestCase):
    def setup_area(self, *specs):
        area = engine.AreaController(replace(config.parse_specs(fixture(False))[0], actions=specs))
        area.update_sensor('binary_sensor.door', 'off')
        area.arm('night')
        backend = FakeBackend()
        return area, actions.ActionRunner(area, backend)

    async def test_disabled_action_never_calls_backend(self):
        spec=engine.ActionSpec('a','A','script','test',data={'x':1},enabled=False)
        area,runner=self.setup_area(spec)
        area.trigger()
        await runner.handle(area.last_event)
        self.assertEqual(runner.backend.calls,[])
        self.assertEqual(area.action_results,{})

    async def test_mute_and_acknowledge_have_separate_actions(self):
        specs=[engine.ActionSpec(k,k,'script',k,data={'x':1},when=k) for k in ['mute','acknowledge']]
        area,runner=self.setup_area(*specs)
        area.trigger();area.mute_actions()
        await runner.handle(area.last_event)
        self.assertEqual([c[1] for c in runner.backend.calls],['mute'])
        area.acknowledge();await runner.handle(area.last_event)
        self.assertEqual([c[1] for c in runner.backend.calls],['mute','acknowledge'])

    async def test_independent_duration_stops_only_owned_output(self):
        spec=engine.ActionSpec('a','A','siren','turn_on',entity_ids=('siren.alarm',),when='armed',timed=True,duration_seconds=10)
        area,runner=self.setup_area(spec)
        with patch.object(actions.time,'monotonic',return_value=100):
            await runner.handle(area.last_event)
        self.assertEqual(runner.backend.read_state('siren.alarm'),'on')
        with patch.object(actions.time,'monotonic',return_value=111):await runner.tick()
        self.assertEqual(runner.backend.read_state('siren.alarm'),'off')
        area,runner=self.setup_area(spec)
        runner.backend.states['siren.alarm']='on'
        await runner.handle(area.last_event)
        with patch.object(actions.time,'monotonic',return_value=999):await runner.tick()
        self.assertNotIn(('siren','turn_off',('siren.alarm',)),runner.backend.calls)

    async def test_results_keep_original_event_id_and_survive_restore(self):
        spec=engine.ActionSpec('a','A','script','test',data={'x':1})
        area,runner=self.setup_area(spec)
        event=engine.AlarmEvent(area.spec.key,'alarm_started','night',123,'door','origin')
        area.trigger()
        await runner.handle(event)
        self.assertEqual(area.last_event.event_id,'origin')
        self.assertIsNone(context.current_event.get())
        restored=engine.AreaController(area.spec);restored.restore(area.snapshot())
        self.assertEqual(restored.action_results,area.action_results)

    async def test_shared_mode_event_runs_identical_action_once(self):
        spec=engine.ActionSpec('a','A','script','test',data={'x':1},when='armed')
        area,runner=self.setup_area(spec);other,second=self.setup_area(spec)
        runner.seen=second.seen={}
        for a,r in [(area,runner),(other,second)]:
            await r.handle(engine.AlarmEvent(a.spec.key,'armed','night',123,'','shared'))
        self.assertEqual(len(runner.backend.calls)+len(second.backend.calls),1)

    def test_invalid_flags_durations_and_timings_fail_closed(self):
        for fields in [{'enabled':1},{'duration_seconds':0,'timed':True}, {'when':'mute','timing':'before'}, {'when':'armed','timed':True}]:
            with self.assertRaises(ValueError):
                engine.ActionSpec('a','A','siren','turn_on',entity_ids=('siren.alarm',),**fields)

    def test_exit_delay_retains_original_cause_across_ticks_and_storage(self):
        spec=config.parse_specs(fixture(False))[0]
        spec=replace(spec,modes=tuple(replace(m,exit_delay_seconds=10) for m in spec.modes))
        area=engine.AreaController(spec,clock=lambda:100)
        area.update_sensor('binary_sensor.door','off')
        token=context.current_event.set('activation')
        try:area.arm('night')
        finally:context.current_event.reset(token)
        saved=area.snapshot()
        restored=engine.AreaController(spec,clock=lambda:105);restored.restore(saved)
        self.assertEqual(restored.phase_event_id,'activation')
        area._clock=lambda:111;area.tick()
        self.assertEqual(area.last_event.kind,'armed')
        self.assertEqual(area.last_event.event_id,'activation')

    def test_startup_sensor_batch_shares_one_cause_without_time_heuristics(self):
        machine=engine.AlarmEngine(config.parse_specs(fixture()))
        token=context.current_event.set('startup')
        try:
            machine.update_sensor('binary_sensor.door','off',initial=True)
            machine.update_sensor('binary_sensor.tamper','off',initial=True)
            machine.finish_recovery()
        finally:context.current_event.reset(token)
        normals=[e for area in machine.areas.values() for e in area.history if e.kind=='zone_normal']
        self.assertEqual(len(normals),2)
        self.assertEqual({e.event_id for e in normals},{'startup'})
