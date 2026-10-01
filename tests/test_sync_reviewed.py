from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import sync_identical as sync
import sync_reviewed as reviewed


class ReviewedTrees(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = self.temp.name
        sync.git(self.repo, "init", "--bare")
        sync.git(self.repo, "config", "user.name", "Test")
        sync.git(self.repo, "config", "user.email", "test@example.com")
        self.empty = sync.git(self.repo, "mktree", input="").stdout.strip()

    def commit(self, files, parents=()):
        updates = {}
        for path, value in files.items():
            oid = sync.git(self.repo, "hash-object", "-w", "--stdin", input=value).stdout.strip()
            updates[path] = ("100644", oid)
        tree = reviewed.edit_tree(self.repo, self.empty, updates)
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(["-p", parent])
        return sync.git(self.repo, *args, input="Test\n").stdout.strip()

    def config(self, local="2", remote="3", flash_local="base", flash_remote="new"):
        base = self.commit({"unity/.version": "1", "flash/.version": "base", "unity/sign.json": '{"text":"old"}'})
        fork = self.commit({"unity/.version": local, "flash/.version": flash_local,
                            "unity/sign.json": '{"text":"fork"}', "unity/partner_contracts.json": "{}",
                            "unity/gems.json": '{"fork":true}', "unity/custom.json": "{}"}, [base])
        upstream = self.commit({"unity/.version": remote, "flash/.version": flash_remote,
                                "unity/sign.json": '{"text":"upstream"}', "unity/gems.json": "{}"}, [base])
        return reviewed.reviewed_candidate(self.repo, fork, upstream, "Murmansk-Seer/config-sources")

    def test_newer_config_preserves_fork_files(self):
        status, tree, details = self.config()
        self.assertEqual(status, "config_snapshot")
        self.assertEqual(reviewed.content(self.repo, tree, "unity/sign.json"), '{"text":"upstream"}')
        self.assertEqual(reviewed.content(self.repo, tree, "unity/gems.json"), '{"fork":true}')
        self.assertIn("unity/custom.json", reviewed.entries(self.repo, tree))
        self.assertIn("unity/partner_contracts.json", reviewed.entries(self.repo, tree))
        self.assertTrue(details["versions"]["unity"]["apply_snapshot"])

    def test_older_unity_preserves_fork_snapshot(self):
        status, tree, details = self.config(local="4", remote="3")
        self.assertEqual(status, "config_snapshot")
        self.assertEqual(reviewed.content(self.repo, tree, "unity/sign.json"), '{"text":"fork"}')
        self.assertFalse(details["versions"]["unity"]["apply_snapshot"])

    def test_equal_unity_receives_corrections(self):
        status, tree, _ = self.config(local="3", remote="3")
        self.assertEqual(status, "config_snapshot")
        self.assertEqual(reviewed.content(self.repo, tree, "unity/sign.json"), '{"text":"upstream"}')

    def test_unknown_flash_version_blocks_candidate(self):
        self.assertEqual(self.config(flash_local="different")[0], "version_ambiguous")

    def test_api_generated_conflict_keeps_fork_tree(self):
        base = self.commit({"data/v1/data/skill/1/index.json": "{}"})
        fork = self.commit({"data/v1/data/skill/1/index.json": '{"fork":1}', "custom": "keep"}, [base])
        upstream = self.commit({"data/v1/data/skill/1/index.json": '{"upstream":1}'}, [base])
        status, tree = sync.candidate(self.repo, fork, upstream, fork_owned_patterns=sync.FORK_OWNED_GENERATED_POLICIES["Murmansk-Seer/api-data"])
        self.assertEqual(status, "fork_owned_generated")
        self.assertEqual(tree, sync.git(self.repo, "rev-parse", fork + "^{tree}").stdout.strip())

    def parser(self, upstream_code="new", fork_code="old", extra=None):
        files = {"main/index.ts": "import '../bytes2json/language'", "bytes2json/language.ts": "old", "json/language.json": "{}"}
        base = self.commit(files)
        fork = self.commit({**files, "bytes2json/language.ts": fork_code, "json/language.json": '{"fork":true}'}, [base])
        incoming = {**files, "bytes2json/language.ts": upstream_code, "json/language.json": '{"upstream":true}'}
        if extra:
            incoming.update(extra)
        upstream = self.commit(incoming, [base])
        return reviewed.reviewed_candidate(self.repo, fork, upstream, "Murmansk-Seer/seer-unity-config-parser")

    def test_parser_merges_code_and_keeps_generated_baseline(self):
        status, tree, _ = self.parser()
        self.assertEqual(status, "parser_candidate")
        self.assertEqual(reviewed.content(self.repo, tree, "bytes2json/language.ts"), "new")
        self.assertEqual(reviewed.content(self.repo, tree, "json/language.json"), '{"fork":true}')

    def test_parser_real_code_conflict_blocks(self):
        self.assertEqual(self.parser(fork_code="fork code")[0], "conflict")

    def test_parser_unreviewed_change_blocks(self):
        self.assertEqual(self.parser(extra={"utils/code.ts": "new"})[0], "content_changed")

    def test_parser_rename_into_generated_directory_blocks(self):
        files = {"main/index.ts": "import '../bytes2json/language'", "bytes2json/language.ts": "same"}
        base = self.commit(files)
        fork = self.commit(files, [base])
        upstream = self.commit({"main/index.ts": files["main/index.ts"], "json/renamed.json": "same"}, [base])
        self.assertEqual(reviewed.reviewed_candidate(self.repo, fork, upstream, "Murmansk-Seer/seer-unity-config-parser")[0], "content_changed")


class Validation(unittest.TestCase):
    def test_advanced_remote_discards_candidate_without_push(self):
        changed = subprocess.CompletedProcess([], 0, "new-head\trefs/heads/main\n", "")
        with patch.object(sync, "network", return_value=changed), patch.object(sync, "git") as command:
            self.assertIsNone(sync.publish_candidate("repo", "owner/repo", "main", "old-head", "candidate"))
            command.assert_not_called()

    def test_push_rechecks_concurrent_change_after_failure(self):
        before = subprocess.CompletedProcess([], 0, "old-head\trefs/heads/main\n", "")
        after = subprocess.CompletedProcess([], 0, "new-head\trefs/heads/main\n", "")
        failed = subprocess.CompletedProcess([], 1, "", "rejected")
        with patch.object(sync, "network", side_effect=[before, after]), patch.object(sync, "git", return_value=failed) as command, patch.object(sync.time, "sleep"), patch("builtins.print"):
            self.assertIsNone(sync.publish_candidate("repo", "owner/repo", "main", "old-head", "candidate"))
            self.assertEqual(command.call_count, 1)

    def test_caught_parser_error_is_failure_even_with_zero_exit(self):
        with self.assertRaisesRegex(RuntimeError, "Official config parsing failed"):
            reviewed.check_parse_result(subprocess.CompletedProcess([], 0, "", "✗ language 解析失败: truncated"))

    def test_nonzero_parser_exit_blocks(self):
        with self.assertRaises(RuntimeError):
            reviewed.check_parse_result(subprocess.CompletedProcess([], 1, "", "error"))

    def test_network_attempts_are_three_total(self):
        failed = subprocess.CompletedProcess([], 1, "", "timeout")
        with patch.object(sync, "run", return_value=failed) as command, patch.object(sync.time, "sleep"), patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                sync.network("git", "fetch")
            self.assertEqual(command.call_count, 3)


if __name__ == "__main__":
    unittest.main()
