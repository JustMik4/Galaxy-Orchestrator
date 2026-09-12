---
name: core
description: Apply general Galaxy engineering discipline when no domain specialist qualifies.
galaxy:
  domains:
    - core
  task_classes:
    - exploration
    - implementation
    - reasoning
    - review
  roles:
    - worker
    - hard-worker
    - tester
    - reviewer
  triggers:
    - galaxy
  paths:
    - "*"
  pack: core
---
# Core Specialist

Keep the task bounded, preserve project invariants, and verify the result with proportionate evidence.
