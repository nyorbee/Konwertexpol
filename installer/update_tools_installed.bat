@echo off
chcp 65001 >nul
rem pure ASCII: the text comes from i18n.py in the language chosen at installation (language.txt)
"%~dp0python\python.exe" "%~dp0i18n.py" update_tools_installed
"%~dp0python\python.exe" "%~dp0tools_manager.py"
echo.
pause
