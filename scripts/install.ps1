#Requires -Version 7.4
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$ProjectPath,
    [ValidateSet('SOLO','CO-OP')][string]$Mode = 'SOLO',
    [ValidateSet('balanced','critical')][string]$Preset = 'balanced',
    [ValidateSet('off','balanced','aggressive')][string]$ContextEconomy = 'off',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$masterPath = Split-Path -Parent $PSScriptRoot
$installArgs = @(
    (Join-Path $masterPath 'galaxy.py'), 'install', $ProjectPath,
    '--mode', $Mode, '--preset', $Preset, '--context-economy', $ContextEconomy
)
if (-not $PSCmdlet.ShouldProcess($ProjectPath, 'Install Galaxy Orchestrator V2 project')) {
    $installArgs += '--check'
}
& $Python @installArgs
if ($LASTEXITCODE -ne 0) { throw "Galaxy Orchestrator installer failed (exit $LASTEXITCODE)." }
