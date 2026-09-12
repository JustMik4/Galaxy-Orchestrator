# Integration gates

1. Compare tracked/untracked files with contract; include both sides of rename and deletions.
2. Review tests and acceptance on current head and current integration base.
3. Read current grant revision and DAG merge evidence; unresolved blocker stops integration.
4. Reviewer uses fresh independent context in both modes. Same operator is allowed. Evidence includes
   implementer_session, reviewer_session, review_result=passed, reviewed_head and reviewed_base.
   External partner review is optional; never wait for a person merely to satisfy cross-review.
5. Required checks must succeed; unknown, skipped product tests or empty configuration stop this gate.
6. Resolve review conversations; check mergeability and configured branch rules with GitHub.
7. The serialized workflow integrates one PR at a time; recheck after base/head change. No bypass of required checks.

`gate` helper consumes evidence JSON described in `.multicontroller/examples/gate.json`. It does not fetch GitHub or
authenticate evidence; root must collect it fresh. Product workflow validates only configured commands.
Administrator must enable branch protection and require the workflow; a YAML file cannot protect a branch itself.
Policy/skills/CI changes require independent review because a PR author can otherwise weaken its own checks.
