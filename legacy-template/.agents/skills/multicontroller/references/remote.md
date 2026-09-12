# Serialized remote cooperation (schema 3)

Every configured integration_operator has equal authority. Native Codex runs local agents.
GitHub Actions multicontroller-control is the only state writer and merger; its active job is the
temporary lead. One repository-wide concurrency group, cancel-in-progress=false, queue=max.
The Issue body holds version, active grants, per-task revision tombstones, recent receipts and pending merge.
Do not edit it manually during operation. Bootstrap/repair are explicit administrative migrations.

Before an operation read canonical state, reconcile task/dependencies and dispatch JSON with unique id,
expected_version and task. Requests are claim, reclaim, release, merge, recover. Workflow authenticates
GitHub actor against team IDs. A committed claim receipt authorizes its owner/machine/nonce to write.
Assignments/comments alone do not. Queued is not claimed. Repeated same ID+payload+actor returns receipt;
changed payload or stale version fails. Read fresh state before preparing a new request. Receipts retain
50 recent operations; stale version blocks older replay. No continuous polling or automatic retry loop.

Claims reserve literal scope. Reclaim from absent/withdrawn peer needs no ACK, but preserves all contract
fields/dependencies, increments task revision and uses new nonce. Complete attempt history/budget follows
the task. Old deliveries fail current-grant checks. Release requires current owner/revision/nonce/machine.
Only one job can change these fields. Two PCs may implement independent scopes concurrently.

Merge must go through this same workflow. It checks current grant, actual PR head/base/changed paths,
dependencies present in base and review evidence; GitHub branch protection enforces remote checks.
Root supplies honest fresh test/review evidence: it is not authenticated model attestation. Pending intent
is saved before API merge. After a response loss, all state changes stop until an authorized peer submits
recover for pending_id. Already merged => reconcile receipt. Still open unchanged => retry original merge.
Changed head/base or unusable evidence => close unmerged PR, then recover records abort without deleting
the grant. Prepare a new PR/request/evidence afterward. Never wipe pending to reuse a scope prematurely.

Disable manual/external merge and automatic queues outside this workflow. If platform rules cannot enforce
that restriction, participants must honor it; this V1 protocol assumes trusted maintainers. Concurrent manual
state edits or admin bypass invalidate guarantees. No credential is packaged: workflow uses ephemeral token.
Run only from default branch and protect its workflow/tools/team policy. Required human approval remains
optional; Reviewer must use a separate context. See docs/COOP-BOOTSTRAP.md in the master for activation.
