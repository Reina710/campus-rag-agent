@echo off
chcp 65001 >nul
cd /d "%~dp0"
"C:\Users\11651\.workbuddy\binaries\python\envs\ca-test\Scripts\python.exe" web_demo.py
echo.
pause
