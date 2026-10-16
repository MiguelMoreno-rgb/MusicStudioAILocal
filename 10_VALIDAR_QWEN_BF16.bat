@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalacion\ValidarQwenBF16.ps1"
set "RESULTADO=%ERRORLEVEL%"
echo.
if not "%RESULTADO%"=="0" echo La validacion no se completo. Revise el mensaje; si se inicio, consulte el informe en resultados.
pause
exit /b %RESULTADO%
