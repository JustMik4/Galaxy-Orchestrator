# Learning and telemetry

Write one sanitized JSON event per attempt in explicit local storage outside tracked project code.
Use UUID filenames to avoid concurrent appends. Keep only operational outcome; never secrets or reasoning transcripts.
Use `usage` with a JSON array of delta events; cumulative counters must be normalized externally.
Input includes cached tokens; do not add cache again. Unavailable usage is null. Root usage must be recorded too.

`reputation` accepts a JSON array with task, timestamp with UTC offset, project, task_class, model, effort,
policy_version, result and failure_class. It groups latest task outcome per bucket over 30 days.
Only implementation/reasoning/complexity failures enter model reputation. Review possible confounders,
task risk and sample sizes. Output is a proposal, not a causal conclusion or config mutation.

Task adapts within budget. Project policy changes require PR/review. Environment proposal requires at least
two projects, sanitized evidence, master PR/review and a new release. Install explicitly; revert to previous
reviewed snapshot if performance worsens. Suggested retention: 30 days; operator deletes chosen local files manually.
