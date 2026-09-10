@echo off
setlocal
chcp 65001 >nul
title Codex-Multicontroller - Projetos
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0lib\project_manager.py"
    goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0lib\project_manager.py"
    goto finished
)
echo Python 3.11 ou superior nao foi encontrado.
echo Instale o Python com acesso pelo PATH e abra este arquivo novamente.
:finished
echo.
pause
