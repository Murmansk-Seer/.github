"""Real Git staging regressions; external parser/network commands are fixtures."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import sync_identical as sync
import sync_reviewed as reviewed


class ParserStateValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        sync.git(str(self.repo), "init")
        sync.git(str(self.repo), "config", "user.name", "Test")
        sync.git(str(self.repo), "config", "user.email", "test@example.invalid")
        (self.repo / "json").mkdir()
        (self.repo / "json/table.json").write_text('[{"id":1}]')
        (self.repo / ".gitignore").write_text("/.build-state/\n/ConfigPackage/\n/cache/\n")
        (self.repo / "source.ts").write_text("preserved")

    def validate(self, legacy=False, changed=True, invalid=False, bad_hash=False):
        if legacy:
            (self.repo / "cache").mkdir()
            (self.repo / "cache/last-release-hashes.json").write_text("[]")
            sync.git(str(self.repo), "add", "-f", "cache/last-release-hashes.json")
        sync.git(str(self.repo), "add", "--", "json", ".gitignore", "source.ts")
        sync.git(str(self.repo), "commit", "-m", "fixture")
        head = sync.git(str(self.repo), "rev-parse", "HEAD").stdout.strip()
        tree = sync.git(str(self.repo), "rev-parse", "HEAD^{tree}").stdout.strip()
        commands = []

        def command(*args, cwd=None, **kwargs):
            commands.append(args)
            root = Path(cwd)
            if args[-1] == "update":
                package = root / "ConfigPackage"
                package.mkdir()
                payload = b"frozen transport fixture"
                (package / "pgame_configs_bytes").write_bytes(payload)
                digest = "0" * 32 if bad_hash else hashlib.md5(payload).hexdigest()
                manifest = {"version": "20261106181039", "items": {
                    "pgame_configs_bytes": {"fileHash": digest}}}
                (package / "PackageManifest_ConfigPackage.json").write_text(json.dumps(manifest))
            if args[-1] == "detect-changes":
                if changed:
                    (root / "json/table.json").write_text("{" if invalid else '[{"id":2}]')
                state = root / ("cache" if legacy else ".build-state/parser")
                state.mkdir(parents=True, exist_ok=True)
                (state / "last-release-hashes.json").write_text('[{"runtime":"changed"}]')
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(reviewed.shutil, "which", return_value="fixture-pnpm"), \
                patch.object(reviewed, "network", side_effect=command), \
                patch.object(reviewed, "run", side_effect=command):
            final, details = reviewed.validate_parser(str(self.repo), tree, head, head, self.root)
        changes = reviewed.changed_paths(str(self.repo), tree, final)
        self.assertEqual(changes, ("json/table.json",) if changed else ())
        self.assertEqual(details["json_count"], 1)
        self.assertEqual(reviewed.content(str(self.repo), final, "source.ts"), "preserved")
        entries = reviewed.entries(str(self.repo), final)
        self.assertFalse(any(path.startswith(".build-state/") for path in entries))
        if legacy:
            self.assertEqual(reviewed.content(str(self.repo), final, "cache/last-release-hashes.json"), "[]")
        else:
            self.assertNotIn("cache/last-release-hashes.json", entries)
        self.assertEqual(sync.git(str(self.repo), "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual([args[-1] for args in commands][-4:],
                         ["main", "mergeItemsOptimizeCatItems", "validate", "detect-changes"])

    def test_state_branch_layout_stages_only_validated_json(self):
        self.validate()

    def test_legacy_layout_preserves_original_runtime_blob(self):
        self.validate(legacy=True)

    def test_state_only_change_produces_identical_tree(self):
        self.validate(changed=False)

    def test_invalid_json_stops_before_staging(self):
        with self.assertRaises(json.JSONDecodeError):
            self.validate(invalid=True)
        candidate = self.root / "parser-candidate"
        self.assertEqual(sync.git(str(candidate), "diff", "--cached", "--name-only").stdout, "")

    def test_bundle_hash_mismatch_stops_before_parse(self):
        with self.assertRaisesRegex(ValueError, "bundle hash mismatch"):
            self.validate(bad_hash=True)
        candidate = self.root / "parser-candidate"
        self.assertEqual(sync.git(str(candidate), "diff", "--cached", "--name-only").stdout, "")


if __name__ == "__main__":
    unittest.main()
