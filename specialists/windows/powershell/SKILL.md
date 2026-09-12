---
name: windows-powershell
description: Build and diagnose Windows and PowerShell scripts with portable, safe behavior.
galaxy:
  domains:
    - windows
    - powershell
  task_classes:
    - implementation
    - reasoning
    - testing
  roles:
    - worker
    - tester
    - reviewer
  triggers:
    - powershell
    - pwsh
    - windows
    - executionpolicy
  paths:
    - "*.ps1"
    - "*.psm1"
    - "*.bat"
  pack: windows
---
# Windows PowerShell Specialist

Use literal paths for filesystem operations, quote arguments carefully, and test under the declared PowerShell baseline.
