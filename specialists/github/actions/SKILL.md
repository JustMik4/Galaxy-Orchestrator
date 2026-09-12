---
name: github-actions
description: Implement and diagnose deterministic GitHub Actions workflows and checks.
galaxy:
  domains:
    - github-actions
    - ci
  task_classes:
    - implementation
    - reasoning
    - testing
  roles:
    - worker
    - tester
    - reviewer
  triggers:
    - workflow
    - github
    - actions
    - ci
  paths:
    - ".github/workflows/*.yml"
    - ".github/workflows/*.yaml"
  pack: devops
---
# GitHub Actions Specialist

Keep permissions minimal, pin behavior where practical, and make required checks stable and reproducible.
