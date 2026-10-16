#requires -Version 5.1
. "$PSScriptRoot\Comun.ps1"
try {
    $authorized = Join-Path $env:USERPROFILE 'OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'
    $old = Join-Path $env:USERPROFILE 'Downloads\MusicStudioAILocal-main'
    if ([IO.Path]::GetFullPath($MusicRoot).TrimEnd('\') -ine [IO.Path]::GetFullPath($authorized).TrimEnd('\')) { throw 'Ejecutar este BAT desde la ubicacion definitiva autorizada en OneDrive\Documentos.' }
    $hardware = Get-Hardware
    $hardware | Format-List | Out-Host
    if ($hardware.RAMDisponibleGiB -lt 4 -or $hardware.VRAMLibreMiB -lt 4608) { throw 'Cerrar aplicaciones manualmente: se requieren 4 GiB RAM y 4.5 GiB VRAM libres para comprobar CUDA/INT8 despues.' }
    if ($hardware.DiscoLibreGiB -lt 1) { throw 'Se requiere al menos 1 GiB libre para respaldos y validacion; no se descarga nada.' }
    $running = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath.StartsWith($MusicRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or $_.ExecutablePath.StartsWith($old + '\', [StringComparison]::OrdinalIgnoreCase)) })
    if ($running.Count) { throw 'Hay procesos ejecutandose desde la instalacion original o trasladada. Cerrarlos manualmente antes de reparar.' }
    $base = Join-Path $MusicRoot '.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe'
    if (-not (Test-Path -LiteralPath $base) -or -not (Test-Path -LiteralPath $PrivatePython)) { throw 'Falta alguno de los dos Python privados. No se instala ni reconstruye el entorno.' }
    # Check placeholders before executing the moved runtime; no hydration is requested.
    $runtime = Split-Path $base -Parent
    $cloudMask = 0x1000 -bor 0x40000 -bor 0x400000
    foreach ($file in @(Get-Item -LiteralPath $runtime) + @(Get-ChildItem -LiteralPath $runtime -Recurse -Force)) {
        if (([int]$file.Attributes -band $cloudMask) -ne 0) { throw 'El runtime tiene archivos solo en nube. Marcar Siempre mantener en este dispositivo y esperar antes de reparar.' }
    }
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $script:OutputEncoding = [Console]::OutputEncoding
    # The broken venv is never used for preflight. No Set-PrivateEnvironment here:
    # it creates folders; preflight must finish without writing any files.
    Invoke-Logged $base @('-I','-S','-B',(Join-Path $MusicRoot 'musicstudio\relocate.py')) $null
    exit 0
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
