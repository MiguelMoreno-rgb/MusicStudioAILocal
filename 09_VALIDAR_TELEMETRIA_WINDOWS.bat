@echo off
setlocal
cd /d "%~dp0"
echo Validacion nativa Windows de telemetria. Sin modelos ni generacion musical.
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0instalacion\ValidarTelemetria.ps1"
set "MUSIC_IO_RESULT=%ERRORLEVEL%"
echo.
if not "%MUSIC_IO_RESULT%"=="0" echo Validacion incompleta o fallida. Conserva el informe y no repitas la generacion.
pause
exit /b %MUSIC_IO_RESULT%
