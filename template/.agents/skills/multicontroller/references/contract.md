# Task contract

Store task state outside tracked product files (Issue for CO-OP; explicit local scratch directory for SOLO).
Use JSON fields: schema_version=1, task, task_class, risk, objective, operator, machine,
role, model, effort, base_sha, worktree, scope (literal paths; directory ends /), forbidden_changes,
depends_on, acceptance, test_commands (argument arrays), preset, max_attempts, max_minutes,
token_limit (null if unavailable), grant_revision, stop_conditions.

Example: AUTH-03; backend-concurrency; worker; gpt-5.6-luna/medium; scope src/auth/token.py
and tests/auth/test_token.py; forbid schema/API/dependency changes; acceptance old refresh token
is invalid before new token becomes usable, reuse rejected, existing auth tests pass.
If atomicity requires schema change: stop and return to root, do not expand scope.

Execution summary fields: task, attempt_id, agent_id, effective model/effort/role, head_sha,
changed_paths, tests [{command,exit_code}], result, failure_class, signature, action, risks, next_owner.
An attempt ID is unique within its task. Complete history follows handoffs without resetting counters.
