@echo off
setlocal
cd /d "%~dp0"
echo Consulta HTTPS de metadatos oficiales. NO descarga pesos ni instala software.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Verificar-Modelos.ps1"
set "RESULTADO=%ERRORLEVEL%"
echo.
if not "%RESULTADO%"=="0" echo Verificacion incompleta. Conserva los resultados y errores.
pause
exit /b %RESULTADO%
