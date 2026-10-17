@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0instalacion\Generar.ps1" -PruebaVoz
set "RESULTADO=%ERRORLEVEL%"
echo.
echo No repita la generacion automaticamente. Conserve los registros y escuche el WAV.
pause
exit /b %RESULTADO%
