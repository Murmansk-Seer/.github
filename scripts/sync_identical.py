"""Reconcile upstream ancestry only when the resulting fork tree is unchanged."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run(*args, cwd=None, check=True, input=None):
    return subprocess.run(args, cwd=cwd, input=input, text=True,
                          encoding="utf-8", capture_output=True, check=check)


def git(repo, *args, check=True, input=None):
    return run("git", *args, cwd=repo, check=check, input=input)


def candidate(repo, fork, upstream):
    """Return (status, tree). Compare complete Git trees including file modes."""
    if git(repo, "merge-base", "--is-ancestor", upstream, fork,
           check=False).returncode == 0:
        return "current", None
    if git(repo, "merge-base", fork, upstream, check=False).returncode != 0:
        return "unrelated", None
    result = git(repo, "merge-tree", "--write-tree", fork, upstream, check=False)
    if result.returncode:
        return "conflict", None
    tree = result.stdout.splitlines()[0].strip()
    original = git(repo, "rev-parse", fork + "^{tree}").stdout.strip()
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
    with tempfile.TemporaryDirectory(prefix="identical-fork-") as directory:
        repo = str(Path(directory) / "repo.git")
        run("git", "clone", "--bare", "--filter=blob:none", "--single-branch",
            "--branch", branch, "https://github.com/" + name + ".git", repo)
        git(repo, "config", "user.name", "github-actions[bot]")
        git(repo, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        git(repo, "fetch", "--filter=blob:none", "https://github.com/" + parent["full_name"] + ".git",
            f"refs/heads/{upstream_branch}:refs/audit/upstream")
        fork = git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip()
        upstream = git(repo, "rev-parse", "refs/audit/upstream").stdout.strip()
        status, tree = candidate(repo, fork, upstream)
        details = dict(repository=name, parent=parent["full_name"], fork=fork, upstream=upstream)
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
        commit = git(repo, "commit-tree", tree, "-p", fork, "-p", upstream,
                     input="Sync identical upstream content\n").stdout.strip()
        git(repo, "push", "origin", f"{commit}:refs/heads/{branch}")
        published = run("git", "ls-remote", "https://github.com/" + name + ".git",
                        f"refs/heads/{branch}").stdout.split()[0]
        report("merged", **details, commit=commit, observed_remote=published, tree=tree)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
