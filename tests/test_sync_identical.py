import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock


class Cleanup(unittest.TestCase):
    def test_cleanup_failure_is_warning(self):
        with patch.object(sync.tempfile, "TemporaryDirectory") as factory, patch("builtins.print") as output:
            factory.return_value.name = "scratch"
            factory.return_value.cleanup.side_effect = OSError("Directory not empty")
            with sync.scratch_directory() as path:
                self.assertEqual(path, "scratch")
            self.assertIn("::warning::", output.call_args.args[0])

    def test_cleanup_preserves_original_error(self):
        with patch.object(sync.tempfile, "TemporaryDirectory") as factory, patch("builtins.print"):
            factory.return_value.cleanup.side_effect = OSError("Directory not empty")
            with self.assertRaisesRegex(ValueError, "original"):
                with sync.scratch_directory():
                    raise ValueError("original")

    def test_git_disables_background_maintenance(self):
        with patch.object(sync, "run") as run:
            sync.git(None, "clone", "example", "target")
            self.assertEqual(run.call_args.args[:5], ("git", "-c", "gc.auto=0", "-c", "maintenance.auto=false"))

spec = importlib.util.spec_from_file_location("sync", Path(__file__).parents[1] / "scripts/sync_identical.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class Trees(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = self.temp.name
        sync.git(self.repo, "init")
        sync.git(self.repo, "config", "user.name", "Test")
        sync.git(self.repo, "config", "user.email", "test@example.com")
        self.base = self.commit({"asset": "old"}, [])

    def commit(self, files, parents):
        entries = []
        for path, content in sorted(files.items()):
            blob = sync.git(self.repo, "hash-object", "-w", "--stdin", input=content).stdout.strip()
            entries.append(f"100644 blob {blob}\t{path}\n")
        tree = sync.git(self.repo, "mktree", input="".join(entries)).stdout.strip()
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(["-p", parent])
        return sync.git(self.repo, *args, input=str(files) + str(parents)).stdout.strip()

    def test_diverged_identical_and_fork_addition(self):
        upstream = self.commit({"asset": "new"}, [self.base])
        fork = self.commit({"asset": "new", "custom": "keep"}, [self.base])
        status, tree = sync.candidate(self.repo, fork, upstream)
        self.assertEqual(status, "identical")
        self.assertEqual(tree, sync.git(self.repo, "rev-parse", fork + "^{tree}").stdout.strip())

    def test_upstream_new_content_requires_review(self):
        upstream = self.commit({"asset": "new"}, [self.base])
        self.assertEqual(sync.candidate(self.repo, self.base, upstream)[0], "content_changed")

    def test_conflicting_content(self):
        upstream = self.commit({"asset": "upstream"}, [self.base])
        fork = self.commit({"asset": "fork"}, [self.base])
        self.assertEqual(sync.candidate(self.repo, fork, upstream)[0], "conflict")

    def test_delete_modify_conflict(self):
        upstream = self.commit({}, [self.base])
        fork = self.commit({"asset": "fork"}, [self.base])
        self.assertEqual(sync.candidate(self.repo, fork, upstream)[0], "conflict")

    def test_already_included(self):
        fork = self.commit({"asset": "fork"}, [self.base])
        self.assertEqual(sync.candidate(self.repo, fork, self.base)[0], "current")

    def test_unrelated(self):
        other = self.commit({"asset": "other"}, [])
        self.assertEqual(sync.candidate(self.repo, self.base, other)[0], "unrelated")


if __name__ == "__main__":
    unittest.main()
