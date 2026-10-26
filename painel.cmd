@echo off
setlocal EnableExtensions DisableDelayedExpansion
set PYTHONUTF8=1
set "PYTHON=python"
py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3.14"
cd /d "%~dp0"
echo Overview-Painel no ar
echo Pagina: file:///%~dp0publico/index.html
echo Não abra duas cópias; a tarefa de logon cuida da instância única.
echo Ctrl-C para parar.
:coletar
%PYTHON% coletor %*
echo O coletor saiu com codigo %errorlevel%. Reiniciando em 3 s.
timeout /t 3 /nobreak >nul
goto coletar
