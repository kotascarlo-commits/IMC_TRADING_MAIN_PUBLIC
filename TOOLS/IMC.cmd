@echo off
if "%~1"=="t" goto terminal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\Start-IMC-Work.ps1" %*
exit /b %errorlevel%
:terminal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\IMC_Terminal.ps1" %*
exit /b %errorlevel%
