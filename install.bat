@echo off
chcp 65001 >nul
setlocal
echo ============================================
echo   Konwertexpol installation
echo ============================================

py -3 --version >nul 2>nul
if errorlevel 1 (
  where winget >nul 2>nul
  if errorlevel 1 (
    echo Python and winget are missing. Install Python 3 from python.org ^(with the "py launcher" option^) and run this again.
    pause
    exit /b 1
  )
  echo Installing Python...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. Close this window and run install.bat again.
  pause
  exit /b 0
)

echo.
echo The tools ^(ffmpeg, ImageMagick, libjxl, Pandoc, Poppler, Ghostscript, qpdf, Tesseract OCR^) and the Python
echo modules ^(pikepdf, Pillow, pdf2docx, ocrmypdf^) go into the folder:
echo   %~dp0tools
echo Only what is MISSING is downloaded and installed. Tools that are already there ^(or already installed on this
echo computer, e.g. Ghostscript / Tesseract^) are left alone. To look for newer versions run update_tools.bat.
echo Ghostscript and Tesseract are looked for on this computer first ^(PATH, the Windows registry, Program Files on
echo every drive^). They are NEVER installed behind your back: if one is missing you are asked whether to install it,
echo or to point to your copy ^(it is remembered in tools\paths.txt^).
echo Once tools is complete, the program works without internet. You can move the folder to another computer
echo ^(it needs the same Python 3.x version there - otherwise the modules are downloaded again^).
echo.
py -3 "%~dp0tools_manager.py" --no-force
if errorlevel 1 (
  echo.
  echo WARNING: some tools could not be downloaded ^(no internet?^). The installation continues -
  echo the tools already in the tools folder will be used. Missing ones are downloaded when first needed.
)
echo.

py -3 "%~dp0register.py" install

echo.
echo Done! Right-click any file and choose Konwertexpol.
echo ^(Windows 11: choose "Show more options" or press Shift+F10^)
echo If you move this folder, run install.bat again.
pause
