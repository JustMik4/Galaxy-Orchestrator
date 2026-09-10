#Requires -Version 7.4
[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory)][string]$ProjectPath, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $ProjectPath '.multicontroller/install-manifest.json'
$installed = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
& (Join-Path $PSScriptRoot 'install.ps1') -ProjectPath $ProjectPath -Mode $installed.mode -Preset $installed.preset -Python $Python -WhatIf:$WhatIfPreference
if ($LASTEXITCODE -ne 0) { throw 'Update failed.' }
