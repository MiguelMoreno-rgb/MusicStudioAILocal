@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0instalacion\Generar.ps1" -SinLM -PorFases
set "RESULTADO=%ERRORLEVEL%"
echo.
if not "%RESULTADO%"=="0" echo La candidata no se completo. Conserve los registros en resultados y no repita automaticamente.
pause
exit /b %RESULTADO%
