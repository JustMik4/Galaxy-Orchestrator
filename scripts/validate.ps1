#Requires -Version 7.4
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ProjectPath, [switch]$ProductGate, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$masterPath = Split-Path -Parent $PSScriptRoot
$validateArgs = @((Join-Path $masterPath 'lib/multicontroller.py'), 'validate', $ProjectPath)
if ($ProductGate) { $validateArgs += '--gate' }
& $Python @validateArgs
if ($LASTEXITCODE -ne 0) { throw "Validation failed (exit $LASTEXITCODE)." }
