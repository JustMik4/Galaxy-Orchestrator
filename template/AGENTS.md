# Galaxy Orchestrator project instructions

Read `.galaxy/project.yml`, `.galaxy/team.yml`, `.galaxy/checks.json`, and
`galaxy.lock` before changing project policy. Treat these files and this
instruction file as project-owned declarations.

Run `galaxy bootstrap . --check` to verify the local Codex projection and
`galaxy validate . --gate` for the `galaxy / validate` integration check.
Generated `.codex` files and `.galaxy/local`, `.galaxy/runtime`,
`.galaxy/cache`, and `.galaxy/install` are local artifacts and must remain
untracked. Do not ignore all of `.agents`; project-owned agent instructions
may be tracked there.

The generated root agent uses Sol at medium reasoning effort. Specialists are
loaded only from the explicit hot set. Browser fallback requires explicit
operator approval, and external LLM providers are not part of this project.

The optional Obsidian vault declaration is disabled by default. Vault sync is
always explicit and must not export prompts, responses, telemetry, or secrets.
