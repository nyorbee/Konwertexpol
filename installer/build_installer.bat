@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem ---------------------------------------------------------------------------------------------
rem  Builds output\Konwertexpol_Setup.exe  (run this on a Windows PC with internet; takes a while)
rem  Needs: Inno Setup 6 (https://jrsoftware.org/isdl.php) and a normal 64-bit Python 3 from python.org
rem  ("py" launcher, with tcl/tk - the default). That Python is copied (cleaned) into the package, so the
rem  installed program has its own Python and the target PC needs none.
rem ---------------------------------------------------------------------------------------------
set "PAYLOAD=%~dp0payload"
set "PYDIR=%PAYLOAD%\python"

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC goto no_inno

echo [1/6] Cleaning ...
if exist "%PAYLOAD%" rmdir /s /q "%PAYLOAD%"
mkdir "%PAYLOAD%"

echo [2/6] Copying the program ...
for %%F in (converter.py launch.py register.py tools_manager.py README.txt LICENSE THIRD_PARTY_NOTICES.md) do copy /y "%~dp0..\%%F" "%PAYLOAD%\" >nul
xcopy /e /i /y "%~dp0..\icons" "%PAYLOAD%\icons" >nul
copy /y "%~dp0update_tools_installed.bat" "%PAYLOAD%\update_tools.bat" >nul
copy /y "%~dp0INSTALLER_NOTICE.txt" "%PAYLOAD%\" >nul
mkdir "%PAYLOAD%\tools"

echo [3/6] Private Python - copying the Python installed on this PC (without extras) ...
set "PYSRC="
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.base_prefix)" 2^>nul') do set "PYSRC=%%P"
if not defined PYSRC (
  echo ERROR: Python 3 was not found ^(the "py" launcher^). Install Python 3.12 from https://www.python.org/downloads/
  goto fail
)
echo       source: %PYSRC%
echo %PYSRC% | find /i "WindowsApps" >nul
if not errorlevel 1 (
  echo ERROR: this is the Microsoft Store Python, which cannot be copied. Install Python from python.org instead.
  goto fail
)
if not exist "%PYSRC%\python.exe" goto badpy
if not exist "%PYSRC%\tcl" goto badpy
robocopy "%PYSRC%" "%PYDIR%" /E /XD Doc include libs Scripts Tools test tests __pycache__ /XF *.pdb /NFL /NDL /NJH /NJS /NP
if errorlevel 8 (
  echo ERROR: copying Python failed.
  goto fail
)
rem start from a clean library folder: the packages of THIS PC must not end up in the installer
if exist "%PYDIR%\Lib\site-packages" rmdir /s /q "%PYDIR%\Lib\site-packages"
mkdir "%PYDIR%\Lib\site-packages"
"%PYDIR%\python.exe" -m ensurepip --upgrade
"%PYDIR%\python.exe" -c "import sys, tkinter, ssl, ctypes, sqlite3; print('Python', sys.version.split()[0], 'OK, Tk', tkinter.TkVersion)"
if errorlevel 1 (
  echo ERROR: the copied Python does not start or has no Tk. Send the messages above.
  goto fail
)

echo [4/6] Downloading the tools into the package (ffmpeg, ImageMagick, Pandoc, Poppler, qpdf, Python modules, OCR data ...)
echo       Ghostscript and Tesseract: if this PC has them they are COPIED into the package, otherwise their installers are run (UAC).
"%PYDIR%\python.exe" "%PAYLOAD%\tools_manager.py" --no-force --bundle
if errorlevel 1 echo WARNING: some tools could not be downloaded - check the messages above.

echo [5/6] Precompiling ...
"%PYDIR%\python.exe" -m compileall -q "%PAYLOAD%\python" "%PAYLOAD%\converter.py" "%PAYLOAD%\register.py" "%PAYLOAD%\launch.py" "%PAYLOAD%\tools_manager.py" >nul 2>nul

echo [6/6] Building the installer ...
"%ISCC%" "%~dp0Konwertexpol.iss"
if errorlevel 1 goto fail
echo.
echo Done: %~dp0output\Konwertexpol_Setup.exe
pause
exit /b 0

:badpy
echo ERROR: %PYSRC% is not a complete Python with Tk ^(python.exe or the tcl folder is missing^).
echo Install the normal Python from https://www.python.org/downloads/ ^(keep "tcl/tk and IDLE" ticked^).
goto fail

:no_inno
echo Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php and run this again.
pause
exit /b 1

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
