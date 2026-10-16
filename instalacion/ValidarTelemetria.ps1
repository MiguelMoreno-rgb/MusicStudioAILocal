#requires -Version 5.1
. "$PSScriptRoot\Comun.ps1"
try {
    $authorized = Join-Path $env:USERPROFILE 'OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'
    if ([IO.Path]::GetFullPath($MusicRoot).TrimEnd('\') -ine [IO.Path]::GetFullPath($authorized).TrimEnd('\')) { throw 'Ejecutar desde la carpeta definitiva autorizada.' }
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess) { throw 'Se requiere Windows x64 real.' }
    $base = Join-Path $MusicRoot '.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe'
    $info = Get-Item -LiteralPath $base
    if (([int]$info.Attributes -band (0x1000 -bor 0x40000 -bor 0x400000)) -ne 0) { throw 'El Python privado debe estar disponible localmente; no se descarga.' }
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    Write-Host 'Solo pruebas de E/S. No carga modelos, no genera musica, no cambia paquetes ni validacion CUDA.'
    Invoke-Logged $base @('-I','-S','-B',(Join-Path $PSScriptRoot 'comprobar_telemetria.py')) $null
    exit 0
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
