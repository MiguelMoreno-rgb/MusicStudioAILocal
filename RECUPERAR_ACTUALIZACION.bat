@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0instalacion\ConfigurarActualizaciones.ps1" -Mode Recover
set "MUSIC_UPDATE_RESULT=%ERRORLEVEL%"
echo.
if not "%MUSIC_UPDATE_RESULT%"=="0" echo Operacion detenida. Conserva el mensaje. No reinstales paquetes.
pause
exit /b %MUSIC_UPDATE_RESULT%
