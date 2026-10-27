@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem ---------------------------------------------------------------------------------------------
rem  Builds output\Konwertexpol_Setup.exe  (run this on a Windows PC with internet; takes a while)
rem  Needs: Inno Setup 6 (https://jrsoftware.org/isdl.php) and a normal 64-bit Python 3 from python.org
rem  ("py" launcher, with tcl/tk - the default). That Python is copied (cleaned) into the package, so the
rem  installed program has its own Python and the target PC needs none.
rem  The language question asked here is only for THIS script's messages. The built Setup.exe asks the
rem  user for the language (English / Polski) when it starts and installs the program in that language.
rem  Usage: build_installer.bat [en|pl]
rem ---------------------------------------------------------------------------------------------
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

set "PAYLOAD=%~dp0payload"
set "PYDIR=%PAYLOAD%\python"
set "SAY=py -3 "%~dp0..\i18n.py" --lang %LNG%"

py -3 --version >nul 2>nul
if errorlevel 1 goto nopy

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC goto no_inno

%SAY% S1
if exist "%PAYLOAD%" rmdir /s /q "%PAYLOAD%"
mkdir "%PAYLOAD%"

%SAY% S2
for %%F in (converter.py launch.py register.py tools_manager.py i18n.py lang_pl.py README.txt README_PL.txt LICENSE THIRD_PARTY_NOTICES.md) do copy /y "%~dp0..\%%F" "%PAYLOAD%\" >nul
xcopy /e /i /y "%~dp0..\icons" "%PAYLOAD%\icons" >nul
copy /y "%~dp0update_tools_installed.bat" "%PAYLOAD%\update_tools.bat" >nul
copy /y "%~dp0INSTALLER_NOTICE.txt" "%PAYLOAD%\" >nul
copy /y "%~dp0INSTALLER_NOTICE_PL.txt" "%PAYLOAD%\" >nul
mkdir "%PAYLOAD%\tools"

%SAY% S3
set "PYSRC="
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.base_prefix)" 2^>nul') do set "PYSRC=%%P"
if not defined PYSRC goto nopy
%SAY% SRC "%PYSRC%"
echo %PYSRC% | find /i "WindowsApps" >nul
if not errorlevel 1 goto storepy
if not exist "%PYSRC%\python.exe" goto badpy
if not exist "%PYSRC%\tcl" goto badpy
robocopy "%PYSRC%" "%PYDIR%" /E /XD Doc include libs Scripts Tools test tests __pycache__ /XF *.pdb /NFL /NDL /NJH /NJS /NP
if errorlevel 8 goto copyfail
rem start from a clean library folder: the packages of THIS PC must not end up in the installer
if exist "%PYDIR%\Lib\site-packages" rmdir /s /q "%PYDIR%\Lib\site-packages"
mkdir "%PYDIR%\Lib\site-packages"
"%PYDIR%\python.exe" -m ensurepip --upgrade
"%PYDIR%\python.exe" -c "import sys, tkinter, ssl, ctypes, sqlite3; print('Python', sys.version.split()[0], 'OK, Tk', tkinter.TkVersion)"
if errorlevel 1 goto pynotk

%SAY% S4
"%PYDIR%\python.exe" "%PAYLOAD%\tools_manager.py" --no-force --bundle
if errorlevel 1 %SAY% WARN

%SAY% S5
"%PYDIR%\python.exe" -m compileall -q "%PAYLOAD%\python" "%PAYLOAD%\converter.py" "%PAYLOAD%\register.py" "%PAYLOAD%\launch.py" "%PAYLOAD%\tools_manager.py" "%PAYLOAD%\i18n.py" "%PAYLOAD%\lang_pl.py" >nul 2>nul

%SAY% S6
"%ISCC%" "%~dp0Konwertexpol.iss"
if errorlevel 1 goto fail
echo.
%SAY% DONE "%~dp0output\Konwertexpol_Setup.exe"
pause
exit /b 0

:nopy
if "%LNG%"=="pl" goto nopy_pl
echo ERROR: Python 3 was not found (the 'py' launcher). Install Python 3.12 from https://www.python.org/downloads/
goto fail_plain
:nopy_pl
echo BLAD: nie znaleziono Pythona 3 (launcher 'py'). Zainstaluj Python 3.12 ze strony https://www.python.org/downloads/
goto fail_plain

:storepy
%SAY% STORE
goto fail

:copyfail
%SAY% COPYFAIL
goto fail

:pynotk
%SAY% PYNOTK
goto fail

:badpy
%SAY% BADPY "%PYSRC%"
goto fail

:no_inno
%SAY% NOINNO
pause
exit /b 1

:fail
echo.
%SAY% FAIL
:fail_plain
pause
exit /b 1
