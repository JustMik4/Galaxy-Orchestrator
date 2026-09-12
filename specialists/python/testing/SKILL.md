---
name: python-testing
description: Design focused deterministic tests for Python behavior and regressions.
galaxy:
  domains:
    - python
  task_classes:
    - implementation
    - review
    - testing
  roles:
    - worker
    - tester
    - reviewer
  triggers:
    - pytest
    - unittest
    - test
    - coverage
  paths:
    - "test_*.py"
    - "tests/*.py"
  pack: python
---
# Python Testing Specialist

Test public behavior and failure boundaries. Keep fixtures small and assertions deterministic.
