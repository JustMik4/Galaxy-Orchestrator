#Requires -Version 7.4
[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory)][string]$ProjectPath, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$masterPath = Split-Path -Parent $PSScriptRoot
$team = Get-Content -LiteralPath (Join-Path $ProjectPath '.galaxy/team.yml') -Raw | ConvertFrom-Json
$project = Get-Content -LiteralPath (Join-Path $ProjectPath '.galaxy/project.yml') -Raw | ConvertFrom-Json
$mode = [string]$team.mode
$preset = [string]$project.routing.profile
$contextEconomy = if ($null -eq $project.context_economy) { 'off' } else { [string]$project.context_economy.mode }
if ($mode -notin @('SOLO', 'CO-OP')) { throw 'Invalid canonical project mode.' }
if ($preset -notin @('balanced', 'critical')) { throw 'Invalid canonical routing profile.' }
if ($contextEconomy -notin @('off', 'balanced', 'aggressive')) { throw 'Invalid context economy mode.' }
$updateArgs = @(
    (Join-Path $masterPath 'galaxy.py'), 'install', $ProjectPath,
    '--mode', $mode, '--preset', $preset, '--context-economy', $contextEconomy
)
if (-not $PSCmdlet.ShouldProcess($ProjectPath, 'Update Galaxy Orchestrator V2 project')) {
    $updateArgs += '--check'
}
& $Python @updateArgs
if ($LASTEXITCODE -ne 0) { throw 'Update failed.' }
