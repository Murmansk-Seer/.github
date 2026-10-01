# Fork synchronization

Every active Murmansk-Seer fork runs the shared workflow hourly. Callers pin both
the workflow and its Python implementation to the same reviewed commit.
Archived repositories stay archived. New forks need the caller workflow installed.

The job merges ancestry only when Git's ordinary three-way merge succeeds and its
complete resulting tree equals the fork tree (paths, blob IDs and file modes).
Fork-only custom files survive. Actual content changes, conflicts and unrelated
histories fail with an Actions summary for manual review. JSON semantic equality,
newer timestamps or a superset of manifest entries are not automatic overrides.

Four reviewed data-generating forks have narrow, fork-owned generated-path
policies. When every upstream change since the merge base is within that
repository's allowlist, the job merges upstream ancestry while retaining the
fork's entire current tree. It never copies upstream generated files over the
fork's release. Code, workflow, and other non-allowlisted data changes still
fail for review. The policies are in `scripts/sync_identical.py`; additions
require an explicit review and tests. This keeps routine independent snapshots
from causing hourly conflict notifications without enabling a blanket merge.

Pushes use only the caller's GITHUB_TOKEN and never force-update a branch. No
cross-repository secret is required. Permission/protection failures are reported
as failed jobs. Concurrent source writers are preserved; the next hourly run
rechecks. These history-only pushes do not require downstream data rebuilds.

Manual workflow_dispatch supports dry_run. The config-sources workflow retains
its separate official gems refresh and downstream dispatch stages, gated after a
successful safe sync. No upstream merge remains in the source-refresh stage.

Run tests: `python -m unittest discover -s tests`.

Reviewed extensions for config-sources, api-data, and seer-unity-config-parser:

- config-sources accepts only upstream changes in flash/ and unity/. Unity
  numeric versions must not roll back. Flash updates require the fork hash to
  match the merge base or upstream hash. The fork's gems, partner contracts,
  parse status, administration files, and unique files survive.
- api-data owns data/v1/data/ and data/v1/sharded_data/. Routine upstream
  snapshots absorb ancestry without overwriting fork-generated output. Other
  paths still require review; fork builds regenerate output from fork sources.
- Existing registered bytes2json/*.ts parser updates can merge only without
  code conflicts. Before pushing, a temporary checkout installs locked
  dependencies, downloads one official ConfigPackage snapshot, checks its
  bundle hash, exports, parses, and validates JSON. Caught parse errors fail the
  candidate even if the original parser returned zero. Only json/ and the
  release-hash cache can enter the resulting generated tree. Workflow, utility,
  dependency, deletion, and unregistered parser changes require review.

Dry runs validate candidates but do not push. Remote branch advancement discards
the candidate; a later run must prepare and validate again. Network operations
and pushes each have at most three total attempts, without force pushes.
