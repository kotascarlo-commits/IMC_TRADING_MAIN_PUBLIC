@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\Desktop\IMC_TRADING_MAIN\TOOLS\Invoke-IMC-AdvisorBroker.ps1" %*
exit /b %ERRORLEVEL%
