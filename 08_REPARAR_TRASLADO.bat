@echo off
setlocal
cd /d "%~dp0"
echo Reparacion de traslado. Sin instalaciones ni descargas de paquetes/modelos.
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0instalacion\RepararTraslado.ps1"
set "MUSIC_RESULT=%ERRORLEVEL%"
echo.
if not "%MUSIC_RESULT%"=="0" echo Reparacion o validacion incompleta. Conserva el mensaje y no descargues modelos.
pause
exit /b %MUSIC_RESULT%
