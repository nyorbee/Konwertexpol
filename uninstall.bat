@echo off
chcp 65001 >nul
rem The message is printed by register.py in the language chosen at installation (language.txt).
py -3 "%~dp0register.py" uninstall
pause
