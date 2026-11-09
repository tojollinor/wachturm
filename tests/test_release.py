"""Release guards prevent publishing stale or unversioned builds."""
import base64
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('release_tool', Path(__file__).resolve().parents[1] / 'tools/publish_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def api(self, head='a' * 40, version='0.8.5', existing=None):
        self.posts = []

        def request(method, path, data=None, optional=False):
            if method == 'POST':
                self.posts.append(data)
                return {'html_url': 'https://github.com/example/repo/releases/tag/v' + version}
            if path == 'git/ref/heads/main':
                return {'object': {'sha': head}}
            if path.startswith('releases/tags/'):
                return existing
            body = json.dumps({'domain': 'ha_alarmzentrale', 'version': version}) if 'manifest.json' in path else '# Release'
            return {'content': base64.b64encode(body.encode()).decode()}
        return request

    def test_only_current_main_can_publish(self):
        release.publish_release(self.api(head='b' * 40), 'a' * 40)
        self.assertEqual(self.posts, [])

    def test_existing_release_is_never_overwritten(self):
        release.publish_release(self.api(existing={'id': 1}), 'a' * 40)
        self.assertEqual(self.posts, [])

    def test_stable_manifest_version_matches_tag_and_checked_commit(self):
        release.publish_release(self.api(), 'a' * 40)
        self.assertEqual(self.posts[0]['tag_name'], 'v0.8.5')
        self.assertEqual(self.posts[0]['target_commitish'], 'a' * 40)
        self.assertFalse(self.posts[0]['prerelease'])
        self.assertFalse(self.posts[0]['draft'])
        with self.assertRaises(ValueError):
            release.publish_release(self.api(version='0.8.5.dev1'), 'a' * 40)
