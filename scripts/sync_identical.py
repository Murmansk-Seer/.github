"""Reconcile upstream ancestry only when the resulting fork tree is unchanged."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from contextlib import contextmanager


# Generated asset repositories may independently publish the same weekly data,
# which makes their histories diverge even when the asset tree is compatible.
# For these explicitly reviewed repositories, upstream owns the generated tree
# while the fork keeps only the listed top-level administration paths.
UPSTREAM_OVERLAY_POLICIES = {
    "Murmansk-Seer/seer-unity-assets-pet_anim_part": (".github",),
}

# These forks publish their own generated snapshots. Their upstream's routine
# snapshot updates may differ byte-for-byte without changing the fork's data.
# Absorb ancestry only when *every* upstream change is in the reviewed list;
# code and unlisted data changes still require a manual merge.
FORK_OWNED_GENERATED_POLICIES = {
    "Murmansk-Seer/api-data": (
        "data/v1/data/metadata.json",
        "data/v1/sharded_data/metadata.json",
    ),
    "Murmansk-Seer/seer-unity-config-parser": (
        "cache/last-release-hashes.json",
        "json/",
    ),
    "Murmansk-Seer/seer-unity-preview-img-dumper": (
        "DefaultPackage/PackageManifest_DefaultPackage.json",
        "DefaultPackage/game_ui_activitylistpreview",
        "img/",
    ),
    "Murmansk-Seer/seer-unity-assets": (
        "newseer/",
        "package-manifests/",
    ),
}


@contextmanager
def scratch_directory():
    temporary = tempfile.TemporaryDirectory(prefix="identical-fork-")
    try:
        yield temporary.name
    finally:
        try:
            temporary.cleanup()
        except OSError as error:
            # The hosted runner discards this scratch directory after the job.
            # Cleanup must not replace a successful result or the real exception.
            print(f"::warning::Scratch cleanup failed: {error}")


def run(*args, cwd=None, check=True, input=None):
    return subprocess.run(args, cwd=cwd, input=input, text=True,
                          encoding="utf-8", capture_output=True, check=check)


def git(repo, *args, check=True, input=None):
    return run("git", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
               *args, cwd=repo, check=check, input=input)


def overlay_tree(repo, fork, upstream, preserved_paths):
    """Build the upstream root tree while retaining reviewed fork paths."""
    entries = {}
    for line in git(repo, "ls-tree", "-z", upstream + "^{tree}").stdout.split("\0"):
        if not line:
            continue
        metadata, path = line.split("\t", 1)
        entries[path] = (metadata, path)
    fork_entries = {}
    for line in git(repo, "ls-tree", "-z", fork + "^{tree}").stdout.split("\0"):
        if not line:
            continue
        metadata, path = line.split("\t", 1)
        fork_entries[path] = (metadata, path)
    for path in preserved_paths:
        if "/" in path or path not in fork_entries:
            raise ValueError(f"Invalid preserved top-level path: {path}")
        entries[path] = fork_entries[path]
    payload = "".join(
        f"{metadata}\t{path}\0"
        for metadata, path in (entries[path] for path in sorted(entries))
    )
    # The sync clone deliberately uses blob:none; all referenced object IDs are
    # trusted entries from the two fetched trees and need not be downloaded.
    return git(repo, "mktree", "--missing", "-z", input=payload).stdout.strip()


def _is_fork_owned_generated_path(path, patterns):
    return any(
        path.startswith(pattern) if pattern.endswith("/") else path == pattern
        for pattern in patterns
    )


def candidate(repo, fork, upstream, preserved_paths=(), fork_owned_patterns=()):
    """Return (status, tree). Compare complete Git trees including file modes."""
    if git(repo, "merge-base", "--is-ancestor", upstream, fork,
           check=False).returncode == 0:
        return "current", None
    if git(repo, "merge-base", fork, upstream, check=False).returncode != 0:
        return "unrelated", None
    if preserved_paths:
        return "upstream_overlay", overlay_tree(repo, fork, upstream, preserved_paths)
    original = git(repo, "rev-parse", fork + "^{tree}").stdout.strip()
    if fork_owned_patterns:
        base = git(repo, "merge-base", fork, upstream).stdout.strip()
        changed = git(repo, "diff", "--name-only", "-z", base, upstream).stdout
        upstream_paths = tuple(path for path in changed.split("\0") if path)
        if upstream_paths and all(
            _is_fork_owned_generated_path(path, fork_owned_patterns)
            for path in upstream_paths
        ):
            return "fork_owned_generated", original
    result = git(repo, "merge-tree", "--write-tree", fork, upstream, check=False)
    if result.returncode:
        return "conflict", None
    tree = result.stdout.splitlines()[0].strip()
    if tree != original:
        return "content_changed", None
    return "identical", tree


def report(status, **details):
    record = {"status": status, **details}
    print(json.dumps(record, ensure_ascii=False))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as out:
            out.write("```json\n" + json.dumps(record, indent=2) + "\n```\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"status={status}\n")


def main():
    name = os.environ["GITHUB_REPOSITORY"]
    info = json.loads(run("gh", "api", f"repos/{name}").stdout)
    if info.get("archived") or not info.get("fork") or not info.get("parent"):
        report("skipped", repository=name, reason="archived or no fork parent")
        return 0
    parent = info["parent"]
    branch = info["default_branch"]
    upstream_branch = parent["default_branch"]
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    with scratch_directory() as directory:
        repo = str(Path(directory) / "repo.git")
        git(None, "clone", "--bare", "--filter=blob:none", "--single-branch",
            "--branch", branch, "https://github.com/" + name + ".git", repo)
        git(repo, "config", "user.name", "github-actions[bot]")
        git(repo, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        git(repo, "fetch", "--filter=blob:none", "https://github.com/" + parent["full_name"] + ".git",
            f"refs/heads/{upstream_branch}:refs/audit/upstream")
        fork = git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip()
        upstream = git(repo, "rev-parse", "refs/audit/upstream").stdout.strip()
        preserved_paths = UPSTREAM_OVERLAY_POLICIES.get(name, ())
        fork_owned_patterns = FORK_OWNED_GENERATED_POLICIES.get(name, ())
        status, tree = candidate(
            repo, fork, upstream, preserved_paths, fork_owned_patterns
        )
        details = dict(repository=name, parent=parent["full_name"], fork=fork, upstream=upstream)
        if preserved_paths:
            details["strategy"] = "upstream_overlay"
            details["preserved_paths"] = list(preserved_paths)
        if fork_owned_patterns:
            details["fork_owned_generated_patterns"] = list(fork_owned_patterns)
        if status in {"conflict", "unrelated", "content_changed"}:
            report(status, **details, reason="Manual review required; no reference was updated.")
            return 1
        if status == "current" or dry_run:
            report(status if status == "current" else "would_merge", **details)
            return 0
        # Never force-push: a concurrent update makes the ordinary push fail.
        current = run("git", "ls-remote", "https://github.com/" + name + ".git",
                      f"refs/heads/{branch}").stdout.split()[0]
        if current != fork:
            report("deferred", **details, reason="Fork advanced; next run will compare again.")
            return 0
        message = (
            "Sync upstream generated content\n" if status == "upstream_overlay"
            else "Sync fork-owned generated ancestry\n" if status == "fork_owned_generated"
            else "Sync identical upstream content\n"
        )
        commit = git(repo, "commit-tree", tree, "-p", fork, "-p", upstream,
                     input=message).stdout.strip()
        git(repo, "push", "origin", f"{commit}:refs/heads/{branch}")
        published = run("git", "ls-remote", "https://github.com/" + name + ".git",
                        f"refs/heads/{branch}").stdout.split()[0]
        report("merged", **details, commit=commit, observed_remote=published, tree=tree)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
