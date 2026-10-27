@echo off
chcp 65001 >nul
setlocal
rem Usage: install.bat [en|pl]   (without a parameter you are asked for the language first)
rem This file is pure ASCII on purpose: the texts are printed by i18n.py in the chosen language.
set "LNG="
if /i "%~1"=="pl" set "LNG=pl"
if /i "%~1"=="en" set "LNG=en"
if defined LNG goto lang_ok
echo.
echo   Language / Jezyk
echo.
echo     [E]  English
echo     [P]  Polski
echo.
choice /c EP /n /m "  > "
if errorlevel 2 (set "LNG=pl") else (set "LNG=en")
:lang_ok
rem the chosen language is remembered for the program, its right-click menu and the tools installer
>"%~dp0language.txt" echo %LNG%

echo ============================================
if "%LNG%"=="pl" (echo   Instalacja Konwertexpol) else (echo   Konwertexpol installation)
echo ============================================

py -3 --version >nul 2>nul
if not errorlevel 1 goto have_python
where winget >nul 2>nul
if errorlevel 1 goto no_winget
if "%LNG%"=="pl" (echo Instalowanie Pythona...) else (echo Installing Python...)
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
if "%LNG%"=="pl" (echo Python zostal zainstalowany. Zamknij to okno i uruchom install.bat jeszcze raz.) else (echo Python installed. Close this window and run install.bat again.)
pause
exit /b 0

:no_winget
if "%LNG%"=="pl" goto no_winget_pl
echo Python and winget are missing. Install Python 3 from python.org (with the 'py launcher' option) and run this again.
pause
exit /b 1
:no_winget_pl
echo Brak Pythona i narzedzia winget. Zainstaluj Python 3 ze strony python.org (z opcja 'py launcher') i uruchom ten plik ponownie.
pause
exit /b 1

:have_python
echo.
py -3 "%~dp0i18n.py" install_intro "%~dp0tools"
echo.
py -3 "%~dp0tools_manager.py" --no-force
if errorlevel 1 goto warn_tools
:tools_done
echo.

py -3 "%~dp0register.py" install

echo.
py -3 "%~dp0i18n.py" install_done
pause
exit /b 0

:warn_tools
echo.
py -3 "%~dp0i18n.py" install_warn
goto tools_done
