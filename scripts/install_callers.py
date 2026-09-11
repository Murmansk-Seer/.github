"""Install pinned hourly callers: uv run --with pyyaml scripts/install_callers.py."""
import argparse
import base64
import json
import re
import subprocess

import yaml


class WorkflowDumper(yaml.SafeDumper):
    pass


def represent_text(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value,
                                   style="|" if "\n" in value else None)


WorkflowDumper.add_representer(str, represent_text)


def gh(*args, payload=None, check=True):
    return subprocess.run(["gh", *args], input=None if payload is None else json.dumps(payload),
                          text=True, encoding="utf-8", capture_output=True, check=check)


def caller(name, ref, old, minute):
    invoke = {
        "uses": f"Murmansk-Seer/.github/.github/workflows/sync-identical-reusable.yml@{ref}",
        "with": {"implementation_ref": ref, "dry_run": "${{ inputs.dry_run == true }}"},
    }
    inputs = {"dry_run": {"description": "Compare without pushing", "type": "boolean", "default": False}}
    jobs = {"sync": invoke}
    if name == "config-sources":
        if not old:
            raise ValueError("Expected existing source-refresh workflow")
        previous = yaml.safe_load(old)
        refresh = previous["jobs"]["sync"]
        # Preserve source fetching, gems refresh and downstream dispatch verbatim.
        steps = refresh["steps"]
        removed = [step for step in steps if step.get("name") == "Merge upstream main"]
        if len(removed) != 1:
            raise ValueError("Unexpected config-sources workflow: inspect before installing")
        refresh["steps"] = [step for step in steps if step not in removed]
        for step in refresh["steps"]:
            if step.get("name") == "Commit and push changes":
                step["run"] = ('git config user.name "github-actions[bot]"\n'
                               'git config user.email "41898282+github-actions[bot]@users.noreply.github.com"\n'
                               + step["run"])
        refresh["needs"] = "sync"
        refresh["if"] = "${{ !inputs.dry_run && (needs.sync.outputs.status == 'current' || needs.sync.outputs.status == 'merged') }}"
        jobs["refresh"] = refresh
        inputs["force"] = {"description": "Force official gems refresh after safe sync", "type": "boolean", "default": False}
    workflow = {
        "name": "Sync Upstream",
        "on": {"schedule": [{"cron": f"{minute} * * * *"}], "workflow_dispatch": {"inputs": inputs}},
        "permissions": {"contents": "write"},
        "concurrency": {"group": "sync-upstream-${{ github.repository }}", "cancel-in-progress": False},
        "jobs": jobs,
    }
    return yaml.dump(workflow, Dumper=WorkflowDumper, sort_keys=False, allow_unicode=True, width=120)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True)
    parser.add_argument("--repo", action="append")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.ref):
        parser.error("--ref must be an immutable 40-character commit SHA")
    repos = json.loads(gh("repo", "list", "Murmansk-Seer", "--limit", "1000", "--json",
                         "name,isFork,isArchived").stdout)
    for index, repo in enumerate(sorted(repos, key=lambda row: row["name"])):
        name = repo["name"]
        if args.repo and name not in args.repo:
            continue
        if not repo["isFork"] or repo["isArchived"]:
            print(json.dumps({"repository": name, "status": "skipped"}), flush=True)
            continue
        endpoint = f"repos/Murmansk-Seer/{name}/contents/.github/workflows/sync-upstream.yml"
        result = gh("api", endpoint, check=False)
        if result.returncode and "404" not in result.stderr:
            raise RuntimeError(result.stderr)
        existing = json.loads(result.stdout) if not result.returncode else None
        old = base64.b64decode(existing["content"]).decode() if existing else None
        # Rerunning the installer with the same ref is a no-op, including config-sources.
        if old and f"@{args.ref}" in old and "implementation_ref:" in old:
            print(json.dumps({"repository": name, "status": "installed"}), flush=True)
            continue
        content = caller(name, args.ref, old, (7 + index * 3) % 60)
        parsed = yaml.safe_load(content)
        assert parsed["jobs"]["sync"]["with"]["implementation_ref"] == args.ref
        if not args.apply:
            print(json.dumps({"repository": name, "status": "planned", "workflow": content}), flush=True)
            continue
        payload = {"message": "ci: reconcile identical upstream trees hourly",
                   "content": base64.b64encode(content.encode()).decode()}
        if existing:
            payload["sha"] = existing["sha"]
        published = json.loads(gh("api", "--method", "PUT", endpoint, "--input", "-", payload=payload).stdout)
        print(json.dumps({"repository": name, "status": "installed", "commit": published["commit"]["sha"]}), flush=True)


if __name__ == "__main__":
    main()
