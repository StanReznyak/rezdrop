@echo off
cd /d "%~dp0"
if exist ".venv" rmdir /s /q ".venv"
call "%~dp0start_windows.bat"
