"""Built-in admin covers current/future areas without assigning anyone rights."""
import importlib
import json
import unittest
from test_sabotage import config, editor, fixture

security = importlib.import_module('alarm_core_tests.security')
defaults = importlib.import_module('alarm_core_tests.default_roles')


class AdminRoleTests(unittest.TestCase):
    def test_default_and_existing_documents_expose_admin_without_user_assignment(self):
        raw = fixture()
        visible = editor.visible_document(json.dumps(raw))['configuration']
        self.assertEqual(visible['roles'][0]['name'], 'Admin')
        self.assertEqual(visible.get('users', []), [])
        self.assertEqual(json.loads(config.default_config_json())['roles'][0]['builtin'], 'admin')
        self.assertNotIn('roles', raw)

    def test_assigned_admin_has_all_operations_including_new_and_sabotage_areas(self):
        raw = fixture()
        raw['areas'].append({'key': 'future', 'name': 'Future'})
        raw['users'] = [{'key': 'alice', 'name': 'Alice', 'enabled': True,
                         'roles': [defaults.ADMIN_ROLE], 'ha_user_ids': ['ha-alice']}]
        _, spec = config.parse_configuration(raw)
        auth = security.Authorization(spec)
        for area in ['home', 'future', 'system_sabotage']:
            for op in security.ALLOWED_OPERATIONS:
                self.assertTrue(auth._has_right(spec.users[0], area, op, 'night'))
        disabled = security.AlarmUser(key='disabled', name='Disabled', enabled=False,
                                      roles=(defaults.ADMIN_ROLE,))
        with self.assertRaises(security.AccessDenied):
            auth._check(disabled, 'home', 'arm', 'night')

    def test_existing_same_key_custom_role_is_not_elevated(self):
        raw = fixture()
        raw['roles'] = [{'key': defaults.ADMIN_ROLE, 'name': 'Existing',
                         'grants': [{'area': 'home', 'operations': ['arm']}]}]
        normalized = defaults.with_admin_role(raw)
        self.assertNotEqual(normalized['roles'][0]['key'], defaults.ADMIN_ROLE)
        self.assertEqual(normalized['roles'][1], raw['roles'][0])
        self.assertEqual(defaults.with_admin_role(normalized), normalized)
