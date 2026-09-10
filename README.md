# Galaxy-Multicontroller

A cooperative multicontroller environment for Codex. This project orchestrates Astra, Sol, and Luna agents using strict deterministic contracts, DAG-based integration, and adaptive routing.

## Overview
Galaxy-Multicontroller is designed for reliable and predictable AI agent orchestration. It uses native Codex capabilities to plan, delegate, interrupt, and review tasks without relying on background daemons or undocumented services. This is a cooperative protocol for trusted operators, focusing on strict contracts rather than open-ended agent loops.

## Key Features
* **Flexible Operation Modes:** Supports both SOLO (single local root) and CO-OP (multiple operators with distinct logins and equivalent authority).
* **Strict Architecture Isolation:** The master installation package remains completely isolated from project directories. Each project receives a versioned snapshot of configurations to ensure reproducibility.
* **Intelligent Adaptive Controller:** A deterministic engine that makes routing decisions based on failure signatures and attempt history. It dynamically scales model complexity, attempts same-agent repairs, or triggers immediate circuit breakers for scope regressions.
* **Queue-Based Synchronization:** Live state coordination is managed via a dedicated GitHub Issue using a single serial `workflow_dispatch` (`multicontroller-control`).
* **Deterministic Budgets & Retries:** Implements strict limits (e.g., 4 total attempts for balanced operations, 5 for critical ones) to prevent infinite loops and control API costs.

## The Stellar Hierarchy (Roles)
Traffic, budget, and authority are routed based on specific model capabilities and risk profiles:

* **Root (Astra High / Sol High):** Highest authority. Handles contracts, budgets, decisions, and architecture planning. Dispatches execution via explicit handoffs.
* **Reviewer (Sol High / Astra High):** Focused on independent reading and code review. Does not implement features directly.
* **Worker & Hard Worker (Luna Medium / Luna High):** Handles bounded code implementation and difficult local-scope defects. Cannot alter implicit architecture.
* **Explorer & Researcher (Luna Low / Luna Medium):** Maps file structures, gathers sources, and reads evidence without permissions to edit the final product.
* **Tester (Luna Medium):** Isolates test executions in a separate workspace. Cannot silently repair the product.

## Getting Started
*Note: The master package must be kept outside of standard project directories. Projects should never be placed inside `.codex` or the master package.*

1. Run the `Iniciar.bat` script to open the Python 3.11+ staging menu.
2. Select your target project directory (e.g., `C:\AI\Projetos\<name>`).
3. Validate the installation preflight checks.
4. The system will deploy the versioned `.codex`, `AGENT_TEAM.yml`, and `.multicontroller` snapshots without overriding your global settings.

## Installation and validation

See [installation guide](docs/INSTALLATION.md), [CO-OP setup](docs/COOP-BOOTSTRAP.md), [SPEC](docs/SPEC-V1.md), and [validation evidence](docs/VALIDATION.md). Version 1.3.0 passes 52 local tests; live GitHub coordination requires activation and validation in each project repository.
