# 0021: GitHub pull-request and workflow-status resources

Status: Accepted.

Extend the existing selected-repository adapter with `github:pulls:read` and `github:workflows:read`, rather than adding executable plugin installation or unrelated account providers without a concrete use case. GitHub Pull requests read and Actions read permissions correspond to these resources; neither implies issue writes or source/workflow modification.

Normalize bounded metadata from fixed `/pulls` and `/actions/runs` endpoints, reuse the 60-second cache, require exact connection and Crew grants, and mark snapshots untrusted. Do not collect PR bodies/diffs, job logs or artifact downloads. Existing connections retain their original capabilities and new-resource checkboxes start unchecked. Reviews remain interval-driven and bounded by the model budget; there are no claimed GitHub webhooks.

Document public-user token setup and limitations in `docs/github-setup.md`, and future provider candidates in `docs/plugins/README.md`. A registered adapter or catalog entry alone is not a successful connection. Contract tests do not prove real GitHub consent or permissions.
