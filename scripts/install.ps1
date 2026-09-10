#Requires -Version 7.4
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$ProjectPath,
    [ValidateSet('SOLO','CO-OP')][string]$Mode = 'SOLO',
    [ValidateSet('balanced','critical')][string]$Preset = 'balanced',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$masterPath = Split-Path -Parent $PSScriptRoot
$installArgs = @((Join-Path $masterPath 'lib/installer.py'), $ProjectPath, '--master', $masterPath, '--mode', $Mode, '--preset', $Preset)
if (-not $PSCmdlet.ShouldProcess($ProjectPath, "Install Multicontroller $Mode/$Preset snapshot")) {
    $installArgs += '--preview'
}
& $Python @installArgs
if ($LASTEXITCODE -ne 0) { throw "Multicontroller installer failed (exit $LASTEXITCODE)." }
