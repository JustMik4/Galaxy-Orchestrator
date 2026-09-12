---
name: git
description: Diagnose Git state and perform safe, reviewable source-control operations.
galaxy:
  domains:
    - git
  task_classes:
    - implementation
    - integration
    - review
  roles:
    - worker
    - reviewer
  triggers:
    - git
    - merge
    - rebase
    - branch
    - conflict
  paths:
    - ".gitignore"
    - ".gitattributes"
  pack: git
---
# Git Specialist

Inspect repository state before changes. Preserve user work and favor non-destructive, reviewable operations.
