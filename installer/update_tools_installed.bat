@echo off
chcp 65001 >nul
echo Checking for newer versions of the tools (ffmpeg, ImageMagick, Pandoc, Poppler, qpdf, ...) and updating them ...
"%~dp0python\python.exe" "%~dp0tools_manager.py"
echo.
pause
