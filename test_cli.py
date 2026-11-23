import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import stem_project_review as cli


class ReviewCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / 'downloaded project'
        self.project.mkdir()
        self.output = self.base / 'report'

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main([str(a) for a in args])

    def read_report(self):
        return json.loads((self.output / 'report.json').read_text())

    def test_local_audit_does_not_execute_or_export_source_text(self):
        (self.project / 'README.md').write_text('Setup\npytest and DO-NOT-EXPORT\n')
        (self.project / 'pyproject.toml').write_text('[project]\n')
        (self.project / 'tests').mkdir()
        (self.project / 'tests/test_demo.py').write_text('raise RuntimeError("do not execute")\n')
        (self.project / '.env.private').write_text('DO-NOT-EXPORT')
        (self.project / 'node_modules').mkdir()
        (self.project / 'node_modules/package.json').write_text('DO-NOT-EXPORT')
        with patch.object(cli.subprocess, 'run', side_effect=AssertionError('unexpected process')):
            self.assertEqual(self.invoke('audit', self.project, '--out', self.output), 0)
        report = self.read_report()
        self.assertNotIn('DO-NOT-EXPORT', json.dumps(report))
        self.assertEqual(report['target']['inventoried_files'], 3)
        self.assertEqual(report['target']['omissions']['excluded_entries'], 2)
        checks = {row['id']: row for row in report['checks']}
        self.assertEqual(checks['runtime']['status'], 'not_assessed')
        self.assertEqual(checks['test_inventory']['status'], 'checked')
        self.assertIn(2, [entry.get('line') for entry in checks['test_inventory']['evidence']])

    def test_missing_material_produces_actionable_ids(self):
        (self.project / 'package.json').write_text('{}')
        self.assertEqual(self.invoke('audit', self.project, '--out', self.output), 0)
        findings = {f['id']: f for f in self.read_report()['findings']}
        self.assertEqual(set(findings), {'DOC-001', 'DOC-002', 'TEST-001'})
        self.assertTrue(all(f['acceptance_checks'] and f['evidence'] for f in findings.values()))

    def test_rejects_in_tree_output_and_existing_directory(self):
        for out in (self.project / 'reports', self.base):
            self.assertEqual(self.invoke('audit', self.project, '--out', out), 2)
        self.assertFalse((self.project / 'reports').exists())

    def test_skips_links_and_special_files(self):
        source = self.base / 'outside'
        source.write_text('pytest')
        (self.project / 'README.md').symlink_to(source)
        (self.project / 'elsewhere').symlink_to(self.base, target_is_directory=True)
        self.assertEqual(self.invoke('audit', self.project, '--out', self.output), 0)
        self.assertEqual(self.read_report()['target']['inventoried_files'], 0)
        self.assertEqual(self.read_report()['target']['omissions']['links_or_special_files'], 2)
        self.assertEqual(cli.read_bounded(self.project, 'README.md'), '')

    def test_validate_and_render_reports(self):
        self.assertEqual(self.invoke('audit', self.project, '--out', self.output), 0)
        record = self.output / 'report.json'
        other = self.base / 'rendered'
        self.assertEqual(self.invoke('validate', record), 0)
        self.assertEqual(self.invoke('render', record, '--out', other), 0)
        for name in ('report.md', 'report.json'):
            self.assertEqual((self.output / name).read_bytes(), (other / name).read_bytes())

    def test_remote_uses_validated_sha_and_strict_url(self):
        for bad in ('https://example.com/a/b', 'https://github.com/a/b/blob/main/x',
                    'https://github.com/a/b?token=x', 'https://github.com/a/.'):
            self.assertEqual(self.invoke('audit', bad, '--out', self.output), 2)
        with patch.object(cli, 'clone_repo', return_value=(self.project, 'a'*40)) as clone:
            self.assertEqual(self.invoke('audit', 'https://github.com/name/repo', '--ref', 'not-a-sha', '--out', self.output), 2)
            clone.assert_not_called()
            self.assertEqual(self.invoke('audit', 'https://github.com/name/repo', '--ref', 'a'*40, '--out', self.output), 0)
            self.assertEqual(clone.call_args.args[:2], ('https://github.com/name/repo.git', 'a'*40))
        self.assertEqual(self.read_report()['target']['revision'], 'a'*40)

    def test_capped_scan_blocks_inventory_and_absence_claims(self):
        (self.project / 'package.json').write_text('{}')
        (self.project / 'sample.txt').touch()
        with patch.object(cli, 'MAX_FILES', 1):
            self.assertEqual(self.invoke('audit', self.project, '--out', self.output), 0)
        report = self.read_report()
        self.assertTrue(report['target']['inventory_capped'])
        self.assertEqual(report['checks'][0]['status'], 'blocked')
        self.assertEqual(report['findings'], [])


if __name__ == '__main__':
    unittest.main()
