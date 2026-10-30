@echo off
call "%~dp0scripts\start_windows.bat" --convert-opaque-to-jpeg %*
exit /b %errorlevel%
