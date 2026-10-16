@echo off
setlocal
cd /d "%~dp0"
echo Music Studio AI Local - Etapa 0. Sin instalaciones.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Diagnostico.ps1"
set "RESULTADO=%ERRORLEVEL%"
echo.
if not "%RESULTADO%"=="0" echo No se completo el diagnostico. Conserva el mensaje mostrado.
pause
exit /b %RESULTADO%
