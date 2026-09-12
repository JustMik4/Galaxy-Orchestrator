---
name: python-debugging
description: Diagnose Python runtime errors, failing tests, concurrency defects, and incorrect behavior.
galaxy:
  domains:
    - python
  task_classes:
    - implementation
    - reasoning
  roles:
    - worker
    - hard-worker
    - reviewer
  triggers:
    - traceback
    - exception
    - asyncio
    - error
  paths:
    - "*.py"
  pack: python
---
# Python Debugging Specialist

Reproduce the failure before repair. Separate environment failures from product defects and prefer the smallest verified correction.
