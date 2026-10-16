@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0instalacion\Generar.ps1" -SinLM
set "MUSIC_RESULT=%ERRORLEVEL%"
echo.
if not "%MUSIC_RESULT%"=="0" echo Operacion incompleta. Conserva el error; no continuar con el paso siguiente.
pause
exit /b %MUSIC_RESULT%
