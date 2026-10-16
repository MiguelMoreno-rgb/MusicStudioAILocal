#requires -Version 5.1
. "$PSScriptRoot\Comun.ps1"
try {
    Show-ExactPlan
    $hardware = Get-Hardware
    $hardware | Format-List | Out-Host
    if ($hardware.DiscoLibreGiB -lt 60) { throw 'Se requieren 60 GiB libres antes de esta instalacion/refresco.' }
    if ($hardware.RAMDisponibleGiB -lt 4) { throw 'Menos de 4 GiB de RAM libre; guardar trabajo y cerrar apps manualmente.' }
    if ($hardware.VRAMLibreMiB -lt 4608) { throw 'Menos de 4.5 GiB de VRAM libre; no continuar ahora.' }
    Confirm-Exact 'INSTALAR'
    Set-PrivateEnvironment
    $ownerFile = Join-Path $MusicRoot '.state\owner.json'
    if ((Test-Path (Join-Path $MusicRoot '.venv')) -and -not (Test-Path $ownerFile)) { throw 'Ya existe .venv sin marcador de este instalador: no se modifica.' }
    if (Test-Path $ownerFile) {
        $owner = Get-Content -LiteralPath $ownerFile -Raw | ConvertFrom-Json
        if ($owner.project -ne 'MusicStudioAILocal' -or $owner.root -ne $MusicRoot) { throw 'Carpeta movida o marcador ajeno. No se modifica el entorno.' }
    } else { @{ project = 'MusicStudioAILocal'; root = $MusicRoot } | ConvertTo-Json | Set-Content -LiteralPath $ownerFile -Encoding UTF8 }
    $log = Join-Path $MusicRoot ('resultados\instalacion-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.log')
    $downloads = Join-Path $MusicRoot '.cache\downloads'
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    $uvZip = Join-Path $downloads 'uv-0.9.6-windows.zip'
    Get-VerifiedArchive $Pins.uv $uvZip
    $uvDir = Split-Path $PrivateUv -Parent
    if (-not (Test-Path $PrivateUv)) {
        Expand-Archive -LiteralPath $uvZip -DestinationPath $uvDir
    } else {
        # Compare the existing binary with its verified release archive before executing it.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($uvZip)
        try {
            $entry = $archive.Entries | Where-Object FullName -eq 'uv.exe' | Select-Object -First 1
            if (-not $entry) { throw 'ZIP uv con estructura inesperada.' }
            $stream = $entry.Open()
            try { $algo = [Security.Cryptography.SHA256]::Create(); $expected = [BitConverter]::ToString($algo.ComputeHash($stream)).Replace('-','') } finally { $stream.Dispose(); $algo.Dispose() }
            if ((Get-FileHash $PrivateUv -Algorithm SHA256).Hash -ne $expected) { throw 'uv privado fue modificado; no se ejecuta.' }
        } finally { $archive.Dispose() }
    }
    # --no-bin and --no-registry are present in the pinned uv release.
    $env:UV_PYTHON_DOWNLOADS = 'automatic'
    Invoke-Logged $PrivateUv @('python','install',$Pins.python,'--no-bin','--no-registry','--no-config') $log
    $env:UV_PYTHON_DOWNLOADS = 'never'
    $pythonDir = Join-Path $env:UV_PYTHON_INSTALL_DIR 'cpython-3.12.12-windows-x86_64-none'
    $managed = Join-Path $pythonDir 'python.exe'
    if (-not (Test-Path $managed)) { throw 'No se encontro el runtime privado esperado de Python 3.12.12.' }
    if (-not (Test-Path $PrivatePython)) {
        Invoke-Logged $PrivateUv @('venv',(Join-Path $MusicRoot '.venv'),'--python',$managed,'--no-python-downloads','--no-config') $log
    }
    Assert-PrivatePython
    $oldValidation = Join-Path $MusicRoot '.state\validation.json'
    if (Test-Path -LiteralPath $oldValidation) { Remove-Item -LiteralPath $oldValidation }
    Invoke-Logged $PrivateUv @('pip','sync','--python',$PrivatePython,'--require-hashes','--only-binary',':all:','--no-config','--default-index','https://pypi.org/simple',(Join-Path $PSScriptRoot 'requirements-windows.lock.txt')) $log
    Invoke-Logged $PrivateUv @('pip','check','--python',$PrivatePython,'--no-config') $log
    foreach ($kind in @('source','ffmpeg')) {
        $spec = $Pins.$kind
        $zip = Join-Path $downloads ($kind + '.zip')
        Get-VerifiedArchive $spec $zip
        Invoke-Logged $PrivatePython @('-I','-B',$Runner,'bootstrap',$kind,$zip) $log
    }
    Invoke-Logged $PrivatePython @('-I','-B',$Runner,'validate') $log
    Write-Host 'Instalacion y comprobaciones del entorno terminadas EN ESTE EQUIPO. No se descargaron pesos.'
    Write-Host 'Siguiente: 04_DESCARGAR_MODELOS.bat. Si hubo errores, no continuar.'
    exit 0
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
