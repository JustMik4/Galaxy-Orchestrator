---
name: multicontroller
description: Use when Codex coordinates bounded agents, selects Luna/Sol/Astra roles, retries failures, or works in SOLO or remote CO-OP projects with file ownership and PR review.
---

# Multicontroller V1

Root owns the contract, budget and integration decision. Native Codex owns agent execution.
This skill is a cooperative protocol, not a filesystem sandbox or remote locking service.

## Start

1. Read `AGENT_TEAM.yml`, `.multicontroller/policy.json`, project instructions and relevant contract.
2. Identify local operator and machine from the master's `local/operator.toml` using its explicit path.
   Missing identity in CO-OP blocks claiming; never infer it from account, Git author or folder name.
3. Confirm the host exposes the required model/effort and role files. If not, record the missing capability;
   use an explicitly agreed substitute or stop the affected dispatch. Never claim a model ran when it did not.
4. Map dependencies and exclusive literal paths. Only merged prerequisites present in the chosen base unblock work.
5. Fill the contract from `references/contract.md`; read only the references needed for this task.

## Dispatch recipe

Balanced: Sol High root; Explorer/Researcher Luna Low; Worker/Tester Luna Medium;
Hard Worker Luna High; Reviewer Sol High. Critical: Astra High architecture/final review,
explicit handoff to Sol High for execution. No duplicate active roots for one task.
Mechanical low-risk work may use Luna Low; nuanced research may use Luna Medium.
Give the worker objective, base SHA, assigned worktree, permitted files, acceptance tests,
budget and stop conditions. Pass compact evidence rather than the full conversation.
Maximum subagents across the entire local tree: balanced 3, critical 2, excluding the root.
If the host cap includes root, subtract that occupied slot first; use the lower resulting capacity.
Workers do not delegate. End finished agents before filling their slots.
Use a separate worktree for each writer. If the host cannot bind worktrees, run writers serially.
Shared files are read-only until exclusively granted. Check tracked and untracked diff, renames and deletions.

## Failure recipe

Record each completed attempt as operational JSON. Run `python .multicontroller/tools/multicontroller.py decide <history.json> --preset balanced`
(select the installed preset). The helper recommends; root performs interruption/re-dispatch and records it.
Check elapsed time and any token budget before dispatch too; the JSON command does not measure runtime.
Second identical signature on the same agent stops that agent. One same-agent repair and one fresh-agent
retry at the same level are allowed per task. Escalation consumes the same total budget: 4 balanced / 5 critical.
Fresh context is distinct from higher effort. Implementation: bounded repair → fresh same level → escalate.
Reasoning: Luna Low → Medium → High → Sol High → Astra High. Complexity: escalate model.
Architecture/ambiguous requirements: return contract to root. Infra/flaky/Git: block and repair the environment;
do not penalize the model. Scope violation or severe regression: interrupt, quarantine diff, no merge.
Budget exhausted: breaker; preserve evidence, return to root. A new task ID is not a budget reset;
reopening needs a revised reviewed contract, link to the exhausted task and explicit justification.

## CO-OP sync points

Read references/remote.md before first claim. Authorized integration_operators have equal authority;
one person can work alone indefinitely. Native Codex runs workers; the serialized GitHub Actions
workflow multicontroller-control runs claim/reclaim/release/merge/recover, one job at a time.
Its active job is the temporary Integration Lead. No desktop owns permanent leadership.
Before planning, writing, push and merge, read current Issue state and receipts. Dispatch JSON with
unique id and expected_version. Start editing only after committed claim receipt and matching nonce.
Do not edit state, assign yourself authority via comments, or merge via gh/UI outside the coordinator.
An absent partner needs no ACK: any authorized peer can reclaim the task using its next revision/new
nonce while preserving scope, dependencies and attempt budget. Old delivery then fails current-grant checks.
No TTL election. All integrations must use the SAME concurrency group; disable other auto-merge/queues.
Pending uncertain merge fences new mutations; any peer can recover it. Changed head/base: close unmerged
PR and recover to abort, then prepare new current evidence. Never clear pending state manually to hurry.
Partner review is optional; independent local agent review remains mandatory. GitHub outages are
infrastructure blockers, not reasons to invent ownership or bypass gates.

## Verify and deliver

Tester reports commands, exit codes and head SHA. Independent Reviewer compares contract and current diff.
Record implementer_session, reviewer_session (different fresh context), review_result=passed and reviewed
head/base. Same operator is allowed; same agent context is not. Partner absence is never a review blocker.
Do not ignore substantive findings just because their author is unavailable.
Use `references/gates.md`; current head/base, current grant, merged DAG, no blockers, successful required CI,
resolved conversations and independent review are required. Do not reuse review of an old head for a minor fix.
Root publishes structured summaries only when the operator has authorized messages; use templates otherwise.
Permanent routing/skill changes are proposed by PR, reviewed and versioned before adoption.
Task memory adapts immediately. Project reputation: 30-day window, at least 3 distinct comparable failures
for a promotion proposal; 5 cheap-level successes for a low-risk probe proposal, never automatic in critical.
Environment learning requires evidence from at least two projects and review in the master repository.
Unknown tokens remain null. No quota estimates from token totals. No raw prompts/chain-of-thought in telemetry.

## Common mistakes

| Temptation | Required action |
|---|---|
| “The fix is tiny; a fifth attempt is certain.” | Stop at the aggregate cap and return the evidence. |
| “The partner has been offline indefinitely.” | Authorized peer reclaims through the serialized workflow; no partner ACK required. |
| “Tests pass despite an extra schema change.” | Quarantine, review contract and migration implications. |
| “Three timeouts prove Luna is weak.” | Classify infrastructure; leave reputation unchanged. |
| “The old PR approval should count.” | Obtain evidence for the current head. |

STOP signs: overlapping grants, duplicate lead, stale revision, unavailable model, scope expansion,
unknown gate state, budget reset disguised as a new agent. Explain the concrete blocker once; avoid loops.

Installation: preview first. Existing different config or AGENTS.md is a conflict, not permission to overwrite.
Identical reinstall is idempotent; upgrade needs original managed hashes or manual reviewed reconciliation.
Never alter global Codex configuration. Preserve local operator identity outside release/project.
