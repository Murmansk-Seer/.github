import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / '.github/actions/pipeline-state/pipeline_state.py'
spec = importlib.util.spec_from_file_location('pipeline_state', SCRIPT)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.DEVNULL, text=True).strip()


class PipelineStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        git(self.root, 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.work = self.root / 'work'
        git(self.root, 'clone', str(self.remote), str(self.work))
        git(self.work, 'config', 'user.name', 'Test')
        git(self.work, 'config', 'user.email', 'test@example.invalid')
        (self.work / 'README').write_text('code')
        git(self.work, 'add', 'README')
        git(self.work, 'commit', '-m', 'code')
        git(self.work, 'push', 'origin', 'main')
        self.main = git(self.work, 'rev-parse', 'HEAD')
        self.previous = Path.cwd()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def checkpoint(self, value):
        Path('.build-state').mkdir(exist_ok=True)
        Path('.build-state/checksum').write_text(value)
        return state.publish()

    def test_state_round_trip_leaves_main_untouched_and_skips_identical_tree(self):
        state.restore()
        first = self.checkpoint('verified')
        body = git(self.remote, 'show', '-s', '--format=%B', first)
        self.assertIn('EN reason/scope:', body)
        self.assertIn('中文原因／范围', body)
        self.assertEqual(first, self.checkpoint('verified'))
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), self.main)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), self.main)
        Path('.build-state/checksum').write_text('local')
        state.restore()
        self.assertEqual(Path('.build-state/checksum').read_text(), 'verified')
        self.assertEqual(git(self.remote, 'ls-tree', '--name-only', 'pipeline-state'), '.build-state')

    def test_concurrent_state_is_not_overwritten(self):
        state.restore()
        first = self.checkpoint('one')
        state.marker().write_text('')
        Path('.build-state/checksum').write_text('stale')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            state.publish()
        self.assertEqual(git(self.remote, 'rev-parse', 'pipeline-state'), first)

    def test_missing_restore_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Restore'):
            self.checkpoint('unverified')

    def test_code_on_state_branch_is_rejected(self):
        git(self.work, 'push', 'origin', 'HEAD:refs/heads/pipeline-state')
        with self.assertRaisesRegex(ValueError, 'non-state'):
            state.restore()

    def test_first_restore_preserves_exact_tracked_previous_publication(self):
        Path('.build-state').mkdir()
        Path('.build-state/publication.json').write_text('{"sha256":"previous-verified"}')
        git(self.work, 'add', '.build-state/publication.json')
        git(self.work, 'commit', '-m', 'existing verified state')
        before = git(self.work, 'rev-parse', 'HEAD')
        state.restore()
        self.assertEqual(Path('.build-state/publication.json').read_text(), '{"sha256":"previous-verified"}')
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), before)
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), self.main)

    def test_first_restore_removes_untracked_unverified_state(self):
        Path('.build-state').mkdir()
        Path('.build-state/unverified').write_text('not a published checkpoint')
        state.restore()
        self.assertFalse(Path('.build-state/unverified').exists())
