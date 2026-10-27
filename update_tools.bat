@echo off
chcp 65001 >nul
echo Checking and updating the tools in the tools folder ...
py -3 "%~dp0tools_manager.py"
pause
