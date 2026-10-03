"""Integration checks use temporary storage and loopback HTTP; GitHub is stubbed."""
import base64
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from unittest.mock import patch

from patchpilot import github
from patchpilot.evaluation import demo
from patchpilot.server import AppServer, MAX_BODY
from patchpilot.store import Store


class StoreTests(unittest.TestCase):
    def test_feedback_persists_updates_and_cascades_on_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/reviews.sqlite3'
            store = Store(path)
            saved = store.save('Example', {'mode': 'static', 'findings': [{'id': 'finding'}], 'files': ['private source']})
            self.assertNotIn('files', saved)
            store.vote(saved['id'], 'finding', 'useful')
            reopened = Store(path)
            self.assertEqual(reopened.get(saved['id'])['feedback'], {'finding': 'useful'})
            reopened.vote(saved['id'], 'finding', 'incorrect')
            self.assertEqual(reopened.get(saved['id'])['feedback'], {'finding': 'incorrect'})
            self.assertEqual(reopened.list()[0]['id'], saved['id'])
            self.assertTrue(reopened.delete(saved['id']))
            self.assertFalse(reopened.delete(saved['id']))
            self.assertIsNone(reopened.get(saved['id']))
            self.assertEqual(reopened.list(), [])
            with reopened.connect() as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM feedback').fetchone()[0], 0)

    def test_feedback_rejects_unknown_finding_and_invalid_vote(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + '/reviews.sqlite3')
            saved = store.save('Example', {'mode': 'static', 'findings': [{'id': 'finding'}]})
            for args in [(saved['id'], 'missing', 'useful'), (saved['id'], 'finding', 'bad'), ('missing', 'finding', 'useful')]:
                with self.assertRaises(ValueError):
                    store.vote(*args)


class GitHubTests(unittest.TestCase):
    HEAD = 'a' * 40
    BASE = 'b' * 40
    URL = 'https://github.com/owner/project/pull/12'
    ROOT = '/repos/owner/project/pulls/12'

    def metadata(self, count=1, head=None):
        return {'title': 'Example PR', 'changed_files': count,
                'head': {'sha': head or self.HEAD, 'repo': {'full_name': 'fork/project'}},
                'base': {'sha': self.BASE}}

    def entry(self, name='cart.py', patch_text='@@ -0,0 +1 @@\n+value = 1'):
        result = {'filename': name, 'status': 'added'}
        if patch_text is not None:
            result['patch'] = patch_text
        return result

    def stub(self, entries, source='value = 1\n', changed_head=False):
        calls = []
        def get(path, deadline):
            calls.append(path)
            if path == self.ROOT:
                return self.metadata(len(entries), 'c' * 40 if changed_head and calls.count(path) > 1 else None)
            if path == self.ROOT + '/files?per_page=100':
                return entries
            if path.startswith('/repos/fork/project/contents/'):
                return {'type': 'file', 'encoding': 'base64', 'size': len(source.encode()),
                        'content': base64.b64encode(source.encode()).decode()}
            raise AssertionError('Unexpected API path: ' + path)
        return get, calls

    def test_import_pins_full_source_to_fork_commit(self):
        get, calls = self.stub([self.entry('src/cart.py')])
        with patch.object(github, '_get', side_effect=get):
            result = github.import_pr(self.URL)
        self.assertEqual(result['files'], {'src/cart.py': 'value = 1\n'})
        self.assertIn('/repos/fork/project/contents/src/cart.py?ref=' + self.HEAD, calls)
        self.assertEqual(calls.count(self.ROOT), 2)
        self.assertEqual(result['origin']['head_sha'], self.HEAD)
        self.assertIn('+++ b/src/cart.py', result['diff'])

    def test_changed_head_rejects_inconsistent_snapshot(self):
        get, _ = self.stub([self.entry()], changed_head=True)
        with patch.object(github, '_get', side_effect=get), self.assertRaisesRegex(ValueError, 'changed during import'):
            github.import_pr(self.URL)

    def test_arbitrary_urls_are_rejected_before_network(self):
        with patch.object(github, '_get') as get:
            for url in ['http://127.0.0.1/private', 'https://evil.example/pull/12',
                        self.URL + '?redirect=1', 'https://github.com/owner/../pull/12',
                        'https://github.com/owner/project/pull/0', None]:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    github.import_pr(url)
            get.assert_not_called()

    def test_omitted_non_python_and_missing_patch_have_explicit_warnings(self):
        entries = [self.entry(), self.entry('app.js'), self.entry('large.py', None)]
        get, calls = self.stub(entries)
        with patch.object(github, '_get', side_effect=get):
            result = github.import_pr(self.URL)
        self.assertTrue(any('app.js' in w and 'unsupported' in w for w in result['warnings']))
        self.assertTrue(any('large.py' in w and 'no textual patch' in w for w in result['warnings']))
        self.assertFalse(any('large.py?ref=' in path for path in calls))

    def test_no_reviewable_patch_is_a_clear_error(self):
        get, _ = self.stub([self.entry('cart.py', None)])
        with patch.object(github, '_get', side_effect=get), self.assertRaisesRegex(ValueError, 'No reviewable Python patches'):
            github.import_pr(self.URL)

    def test_removed_file_does_not_request_head_source(self):
        entry = {'filename': 'cart.py', 'status': 'removed', 'patch': '@@ -1 +0,0 @@\n-value = 1'}
        get, calls = self.stub([entry])
        with patch.object(github, '_get', side_effect=get):
            result = github.import_pr(self.URL)
        self.assertEqual(result['files'], {})
        self.assertIn('+++ /dev/null', result['diff'])
        self.assertFalse(any('/contents/' in path for path in calls))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        try:
            cls.server = AppServer(port=0, data_dir=cls.temp.name)
        except Exception:
            cls.temp.cleanup()
            raise
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_address[1])
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def request(self, path, payload=None, headers=None, raw=None):
        outgoing = {'X-PatchPilot-Token': self.server.token}
        data = None
        if payload is not None or raw is not None:
            data = raw if raw is not None else json.dumps(payload).encode()
            outgoing['Content-Type'] = 'application/json'
        outgoing.update(headers or {})
        req = Request(self.base + path, data=data, headers=outgoing)
        try:
            response = self.opener.open(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            content_type = response.headers.get('Content-Type', '')
            return response.status, json.loads(body) if 'application/json' in content_type else body.decode(), response.headers

    def test_demo_review_feedback_export_history_and_delete(self):
        status, sample, _ = self.request('/api/demo')
        self.assertEqual(status, 200)
        status, result, _ = self.request('/api/review', sample)
        self.assertEqual(status, 200)
        self.assertEqual(len(result['findings']), 3)
        source = sample['files']['shopping/cart.py'].splitlines()
        for finding in result['findings']:
            self.assertEqual(finding['path'], 'shopping/cart.py')
            self.assertEqual(finding['excerpt'], source[finding['line'] - 1])
        review_id = result['id']
        status, _, _ = self.request('/api/feedback', {'review_id': review_id, 'finding_id': result['findings'][0]['id'], 'vote': 'useful'})
        self.assertEqual(status, 200)
        status, stored, _ = self.request('/api/reviews/' + review_id)
        self.assertEqual(status, 200)
        self.assertNotIn('files', stored)
        self.assertEqual(stored['feedback'][result['findings'][0]['id']], 'useful')
        status, exported, headers = self.request('/api/reviews/' + review_id + '/export')
        self.assertEqual(status, 200)
        self.assertIn('shopping/cart.py:', exported)
        self.assertIn('attachment', headers['Content-Disposition'])
        _, history, _ = self.request('/api/history')
        self.assertIn(review_id, {row['id'] for row in history})
        status, deleted, _ = self.request('/api/delete', {'review_id': review_id})
        self.assertEqual((status, deleted), (200, {'deleted': True}))
        self.assertEqual(self.request('/api/reviews/' + review_id)[0], 404)

    def test_trusted_import_snapshot_and_input_integrity(self):
        sample = demo()
        origin = {'url': 'https://github.com/owner/project/pull/12',
                  'head_sha': 'a' * 40, 'base_sha': 'b' * 40,
                  'coverage': 'Python textual patches only'}
        snapshot = dict(sample, origin=origin, warnings=['app.js: unsupported file type'])
        with patch('patchpilot.server.import_pr', return_value=snapshot):
            status, imported, _ = self.request('/api/import', {'url': origin['url']})
        self.assertEqual(status, 200)
        self.assertTrue(imported['snapshot_id'])
        status, reviewed, _ = self.request('/api/review', imported)
        self.assertEqual(status, 200)
        self.assertEqual(reviewed['origin'], origin)
        self.assertIn(snapshot['warnings'][0], reviewed['warnings'])
        altered_diff = dict(imported, diff=imported['diff'] + '\n')
        self.assertEqual(self.request('/api/review', altered_diff)[0], 400)
        altered_files = dict(imported, files={'shopping/cart.py': 'forged source'})
        self.assertEqual(self.request('/api/review', altered_files)[0], 400)
        self.assertEqual(self.request('/api/review', dict(imported, snapshot_id='unknown'))[0], 400)
        forged = dict(sample, origin=dict(origin, head_sha='c' * 40))
        status, manual, _ = self.request('/api/review', forged)
        self.assertEqual(status, 200)
        self.assertNotIn('origin', manual)

    def test_csrf_token_host_and_origin_boundaries(self):
        for headers in [{'X-PatchPilot-Token': ''}, {'Host': 'attacker.example'}, {'Origin': 'https://attacker.example'}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/delete', {'review_id': 'x'}, headers=headers)[0], 403)
        self.assertEqual(self.request('/api/config', headers={'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request('/api/config', headers={'Origin': 'https://attacker.example'})[0], 403)
        self.assertEqual(self.request('/api/config', headers={'Origin': self.base})[0], 200)

    def test_json_validation_and_body_budget(self):
        self.assertEqual(self.request('/api/review', raw=b'{broken')[0], 400)
        self.assertEqual(self.request('/api/review', payload=[])[0], 400)
        self.assertEqual(self.request('/api/review', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/api/review', raw=b'{}', headers={'Content-Length': str(MAX_BODY + 1)})[0], 413)
        sample = demo()
        sample['use_ai'] = 'false'
        self.assertEqual(self.request('/api/review', sample)[0], 400)

    def test_feedback_and_snapshot_identifiers_reject_non_scalar_values(self):
        sample = demo()
        status, result, _ = self.request('/api/review', sample)
        self.assertEqual(status, 200)
        valid = {'review_id': result['id'], 'finding_id': result['findings'][0]['id'], 'vote': 'useful'}
        for field in valid:
            for invalid in [[], {}, 17, None]:
                with self.subTest(field=field, value=invalid):
                    self.assertEqual(self.request('/api/feedback', dict(valid, **{field: invalid}))[0], 400)
        self.assertEqual(self.request('/api/review', dict(sample, snapshot_id=['forged']))[0], 400)

    def test_evaluation_returns_labeled_development_metrics(self):
        status, result, _ = self.request('/api/evaluate', {})
        self.assertEqual(status, 200)
        self.assertEqual(result['mode'], 'static')
        self.assertGreaterEqual(len(result['cases']), 12)
        self.assertTrue(result['limitations'])
        for key in ['precision', 'recall', 'f1', 'clean_pass_rate']:
            self.assertGreaterEqual(result[key], 0)
            self.assertLessEqual(result[key], 1)


if __name__ == '__main__':
    unittest.main()
