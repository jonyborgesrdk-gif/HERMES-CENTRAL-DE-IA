@echo off
setlocal EnableExtensions DisableDelayedExpansion
set PYTHONUTF8=1
set "PYTHON=python"
py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3.14"
cd /d "%~dp0"
echo Overview-Painel no ar
echo Pagina: http://127.0.0.1:7421/
echo Ctrl-C para parar.
%PYTHON% servidor.py --com-coletor %*
