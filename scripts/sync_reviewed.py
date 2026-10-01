"""Reviewed repository-specific candidates and pre-publication validation."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

from sync_identical import git, network, run

GENERATED = ("json/", "cache/last-release-hashes.json")
PROTECTED_CONFIG = {"unity/gems.json", "unity/partner_contracts.json", "unity/.parse-status.json"}


def entries(repo, revision):
    result = {}
    for record in git(repo, "ls-tree", "-r", "-z", revision).stdout.split("\0"):
        if record:
            metadata, path = record.split("\t", 1)
            mode, _, oid = metadata.split()
            result[path] = (mode, oid)
    return result


def edit_tree(repo, original, updates):
    with tempfile.TemporaryDirectory(prefix="sync-index-") as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
        git(repo, "read-tree", original, env=env)
        payload = "".join(
            f"{value[0]} {value[1]}\t{path}\0" if value else f"0 {'0' * 40}\t{path}\0"
            for path, value in sorted(updates.items())
        )
        if payload:
            git(repo, "update-index", "-z", "--index-info", input=payload, env=env)
        return git(repo, "write-tree", env=env).stdout.strip()


def changed_paths(repo, base, upstream):
    return tuple(filter(None, git(repo, "diff", "--no-renames", "--name-only", "-z", base, upstream).stdout.split("\0")))


def content(repo, revision, path):
    return git(repo, "show", f"{revision}:{path}").stdout


def is_generated(path):
    return path.startswith("json/") or path == "cache/last-release-hashes.json"


def reviewed_candidate(repo, fork, upstream, name):
    if name not in {"Murmansk-Seer/config-sources", "Murmansk-Seer/seer-unity-config-parser"}:
        return None
    if git(repo, "merge-base", "--is-ancestor", upstream, fork, check=False).returncode == 0:
        return None
    base_result = git(repo, "merge-base", fork, upstream, check=False)
    if base_result.returncode:
        return None
    base = base_result.stdout.strip()
    paths = changed_paths(repo, base, upstream)
    original = entries(repo, fork)
    incoming = entries(repo, upstream)
    ancestral = entries(repo, base)
    details = {"changed_paths": list(paths)}
    if name.endswith("config-sources"):
        details["strategy"] = "versioned_config_snapshot"
        if not all(path.startswith(("unity/", "flash/")) for path in paths):
            return "content_changed", None, details
        updates = {}
        versions = {}
        for package in ("unity", "flash"):
            selected = [path for path in paths if path.startswith(package + "/")]
            if not selected:
                continue
            version_path = package + "/.version"
            local = content(repo, fork, version_path).strip()
            remote = content(repo, upstream, version_path).strip()
            old = content(repo, base, version_path).strip()
            versions[package] = {"fork": local, "upstream": remote, "base": old}
            if package == "unity":
                if not local.isdecimal() or not remote.isdecimal():
                    return "version_ambiguous", None, {**details, "versions": versions}
                apply = int(remote) >= int(local)
            else:
                if local not in {old, remote}:
                    return "version_ambiguous", None, {**details, "versions": versions}
                apply = True
            versions[package]["apply_snapshot"] = apply
            if apply:
                for path in selected:
                    if path in PROTECTED_CONFIG:
                        continue
                    value = incoming.get(path)
                    if value:
                        if value[0] != "100644":
                            raise ValueError(f"Unexpected config file mode: {path}")
                        raw = content(repo, upstream, path)
                        if path.endswith(".json"):
                            json.loads(raw)
                        elif path.endswith(".xml"):
                            ET.fromstring(raw)
                    updates[path] = value
        tree = edit_tree(repo, fork, updates)
        return "config_snapshot", tree, {**details, "versions": versions, "validation": "JSON/XML parsed"}

    details["strategy"] = "validated_parser_merge"
    source_paths = [path for path in paths if not is_generated(path)]
    if not source_paths:
        return None
    registered = content(repo, fork, "main/index.ts")
    for path in source_paths:
        if (not re.fullmatch(r"bytes2json/[^/]+\.ts", path)
                or path not in original or path not in ancestral or path not in incoming
                or original[path][0] != incoming[path][0] or incoming[path][0] != "100644"
                or f"../bytes2json/{Path(path).stem}" not in registered):
            return "content_changed", None, details
    # Replace upstream generated files with their common-ancestor entries before
    # three-way merging code. The final commit still has both real parents.
    updates = {path: ancestral.get(path) for path in set(ancestral) | set(incoming) if is_generated(path)}
    sanitized = edit_tree(repo, upstream, updates)
    synthetic = git(repo, "commit-tree", sanitized, "-p", base, input="Parser code candidate\n").stdout.strip()
    result = git(repo, "merge-tree", "--write-tree", fork, synthetic, check=False)
    if result.returncode:
        return "conflict", None, details
    return "parser_candidate", result.stdout.splitlines()[0], details


def check_parse_result(result):
    if result.returncode or re.search(r"✗ .*解析失败", result.stdout + result.stderr):
        raise RuntimeError("Official config parsing failed:\n" + (result.stdout + result.stderr)[-12000:])


def validate_parser(repo, tree, fork, upstream, directory):
    commit = git(repo, "commit-tree", tree, "-p", fork, "-p", upstream, input="Validate parser candidate\n").stdout.strip()
    workspace = str(Path(directory) / "parser-candidate")
    git(repo, "worktree", "add", "--detach", workspace, commit)
    pnpm = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    if not pnpm:
        raise RuntimeError("pnpm is required to validate the parser candidate")
    network(pnpm, "install", "--frozen-lockfile", cwd=workspace)
    network(sys.executable, "-m", "pip", "install", "-r", "requirements.txt", cwd=workspace)
    network(pnpm, "run", "update", cwd=workspace)
    root = Path(workspace)
    manifest = json.loads((root / "ConfigPackage/PackageManifest_ConfigPackage.json").read_text(encoding="utf-8"))
    bundle_key = next(key for key in manifest["items"] if Path(key).name == "pgame_configs_bytes")
    expected = manifest["items"][bundle_key]["fileHash"].lower()
    actual = hashlib.md5((root / "ConfigPackage/pgame_configs_bytes").read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("Official ConfigPackage bundle hash mismatch")
    run(pnpm, "run", "export", cwd=workspace)
    env = dict(os.environ, PARSER_STRICT="1", FEISHU_WEBHOOK_URL="", FEISHU_SECRET="")
    parsed = run(pnpm, "run", "main", cwd=workspace, check=False, env=env)
    check_parse_result(parsed)
    run(pnpm, "run", "mergeItemsOptimizeCatItems", cwd=workspace, env=env)
    run(pnpm, "run", "validate", cwd=workspace, env=env)
    run(pnpm, "run", "detect-changes", cwd=workspace, env=env)
    files = list((root / "json").glob("*.json"))
    if not files:
        raise ValueError("Parser produced no JSON files")
    for path in files:
        json.loads(path.read_text(encoding="utf-8"))
    git(workspace, "add", "--", "json", "cache/last-release-hashes.json")
    final_tree = git(workspace, "write-tree").stdout.strip()
    changes = changed_paths(repo, tree, final_tree)
    if any(not is_generated(path) for path in changes):
        raise ValueError("Validation modified an unapproved tracked path")
    return final_tree, {"result": "passed", "package_version": manifest["version"], "bundle_md5": actual, "json_count": len(files)}
