"""Single-area assignments preserve existing installations and sabotage."""
from copy import deepcopy
import json
import unittest
from test_sabotage import config, editor, fixture


class AreaSensorTests(unittest.TestCase):
    def save(self, before, after):
        raw = json.dumps(before)
        return json.loads(editor.prepare_configuration(raw, after, editor.revision_of(raw)))

    def test_new_multi_area_assignment_rejected_even_with_identical_rules(self):
        before = fixture()
        after = deepcopy(before)
        after['areas'].append({'key': 'other', 'name': 'Other'})
        link = deepcopy(after['sensors'][0]['memberships'][0])
        link['area'] = 'other'
        for mode in after['modes']:
            mode['area_keys'].append('other')
        after['sensors'][0]['memberships'].append(link)
        with self.assertRaisesRegex(editor.EditorError, 'Nur ein Bereich'):
            self.save(before, after)

    def test_new_or_detached_sensor_requires_an_area(self):
        before = fixture()
        after = deepcopy(before)
        after['sensors'][0]['memberships'] = []
        with self.assertRaisesRegex(editor.EditorError, 'Bereich auswählen'):
            self.save(before, after)
        before['sensors'] = []
        with self.assertRaisesRegex(editor.EditorError, 'Bereich auswählen'):
            self.save(before, after)

    def test_legacy_multiple_assignments_are_not_silently_removed(self):
        before = fixture()
        before['areas'].append({'key': 'other', 'name': 'Other'})
        for mode in before['modes']:
            mode['area_keys'].append('other')
        link = deepcopy(before['sensors'][0]['memberships'][0])
        link['area'] = 'other'
        before['sensors'][0]['memberships'].append(link)
        self.assertEqual(len(self.save(before, before)['sensors'][0]['memberships']), 2)
        after = deepcopy(before)
        after['sensors'][0]['memberships'] = [link]
        saved = self.save(before, after)
        areas = {a.key: a for a in config.parse_specs(saved)}
        self.assertEqual(areas['home'].zones, ())
        self.assertEqual(len(areas['other'].zones), 1)
        self.assertEqual(len(areas['system_sabotage'].zones), 1)
        self.assertEqual(saved['sensors'][0]['sabotage'], before['sensors'][0]['sabotage'])

    def test_unassigned_sensor_survives_area_deletion_and_can_be_reassigned(self):
        before = fixture()
        after = deepcopy(before)
        after['areas'] = [a for a in after['areas'] if a['key'] != 'home']
        for mode in after['modes']:
            mode['area_keys'].remove('home')
        after['sensors'][0]['memberships'] = []
        saved = self.save(before, after)
        self.assertEqual(saved['sensors'][0]['entities'], before['sensors'][0]['entities'])
        reassigned = deepcopy(saved)
        reassigned['areas'].append(before['areas'][0])
        for mode in reassigned['modes']:
            mode['area_keys'].append('home')
        reassigned['sensors'][0]['memberships'] = before['sensors'][0]['memberships']
        self.assertEqual(len(self.save(saved, reassigned)['sensors'][0]['memberships']), 1)
