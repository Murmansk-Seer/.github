# Fork synchronization

Every active Murmansk-Seer fork runs the shared workflow hourly. Callers pin both
the workflow and its Python implementation to the same reviewed commit.
Archived repositories stay archived. New forks need the caller workflow installed.

The job merges ancestry only when Git's ordinary three-way merge succeeds and its
complete resulting tree equals the fork tree (paths, blob IDs and file modes).
Fork-only custom files survive. Actual content changes, conflicts and unrelated
histories fail with an Actions summary for manual review. JSON semantic equality,
newer timestamps or a superset of manifest entries are not automatic overrides.

Pushes use only the caller's GITHUB_TOKEN and never force-update a branch. No
cross-repository secret is required. Permission/protection failures are reported
as failed jobs. Concurrent source writers are preserved; the next hourly run
rechecks. These history-only pushes do not require downstream data rebuilds.

Manual workflow_dispatch supports dry_run. The config-sources workflow retains
its separate official gems refresh and downstream dispatch stages, gated after a
successful safe sync. No upstream merge remains in the source-refresh stage.

Run tests: `python -m unittest discover -s tests`.
