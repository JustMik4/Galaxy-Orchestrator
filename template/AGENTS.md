# Galaxy Orchestrator project instructions

Read `.galaxy/project.yml`, `.galaxy/team.yml`, `.galaxy/checks.json`, and
`galaxy.lock` before changing project policy. Treat these files and this
instruction file as project-owned declarations.

Run `galaxy bootstrap . --check` to verify the local Codex projection and
`galaxy validate . --gate` for the `galaxy / validate` integration check.
Generated `.codex` files and `.galaxy/local`, `.galaxy/runtime`,
`.galaxy/cache`, `.galaxy/install`, and `.galaxy/evidence` are local artifacts and must remain
untracked. Do not ignore all of `.agents`; project-owned agent instructions
may be tracked there.

The generated root agent uses Sol at medium reasoning effort. Specialists are
loaded only from the explicit hot set. Browser fallback requires explicit
operator approval, and external LLM providers are not part of this project.

Before every subagent dispatch, retry, or escalation, run
`galaxy dispatch authorize PROJECT --request FILE --capabilities FILE --quota FILE`
and spawn only the exact model/effort pair it authorizes. Immediately after
spawn, run `galaxy dispatch verify` with the dispatch ID and effective host
telemetry; an unverified or mismatched route is a `host-routing` blocker, not a
model-quality failure. A task may select a valid route profile in its request;
emergency requests must include a stable `task_id`, evidence, and reason, and
the local persisted per-task limit cannot be supplied by the caller. Before an expensive review, run
`galaxy dispatch review-check`; reuse evidence only on an exact fingerprint
match, and persist new completed evidence with `galaxy dispatch review-record`.

The optional Obsidian vault declaration is disabled by default. Vault sync is
always explicit and must not export prompts, responses, telemetry, or secrets.

Context economy is an explicit project setting and defaults to `off`. In
`balanced` or `aggressive` mode, keep handoffs structured, persist sanitized
overflow under `.galaxy/evidence/<task>/`, refer to stable evidence instead of
retransmitting it, and expand references before treating missing context as a
model failure. Never compact failure identity, location, message, blockers,
risks, decisions, or validation evidence. Context metrics and account quota are
separate signals.

Do not ask for confirmation for read-only work, normal implementation steps,
or local reversible changes already requested by the user, including a planned
project rename. For an external publication, treat the user's explicit publish
request as authorization for the complete declared batch. Ask once only when a
new irreversible/public scope, credentials, permissions, or browser fallback is
actually required; one approval covers all named sub-actions in that scope.
