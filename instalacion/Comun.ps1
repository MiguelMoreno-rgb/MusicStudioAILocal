#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$MusicRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $MusicRoot
# Keep this mutex alive for the lifetime of this PowerShell/BAT process.
# The updater uses the same name without loading project code.
$keyHash = [Security.Cryptography.SHA256]::Create()
try { $key = ([BitConverter]::ToString($keyHash.ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($MusicRoot).TrimEnd('\').ToLowerInvariant())))).Replace('-','') } finally { $keyHash.Dispose() }
$script:MusicOperationMutex = New-Object Threading.Mutex($false, ('Local\MusicStudioAILocal-' + $key))
$script:MusicOperationHeld = $false
try { $script:MusicOperationHeld = $script:MusicOperationMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $script:MusicOperationHeld = $true }
if (-not $script:MusicOperationHeld) { throw 'Otra operacion del proyecto esta activa (actualizacion, instalacion, reparacion o generacion). Esperar a que termine.' }
$Pins = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bootstrap.lock.json') -Raw | ConvertFrom-Json
$PrivatePython = Join-Path $MusicRoot '.venv\Scripts\python.exe'
$PrivateUv = Join-Path $MusicRoot '.runtime\uv\uv.exe'
$Runner = Join-Path $PSScriptRoot 'ejecutar.py'

function Set-PrivateEnvironment {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $script:OutputEncoding = [Console]::OutputEncoding
    foreach ($name in @('VIRTUAL_ENV','CONDA_PREFIX','PYTHONPATH','PYTHONHOME','UV_PYTHON','UV_SYSTEM_PYTHON','UV_TARGET','UV_PREFIX','UV_INDEX','UV_INDEX_URL','UV_EXTRA_INDEX_URL','UV_FIND_LINKS','UV_CONSTRAINT','UV_OVERRIDE','UV_INSECURE_HOST','UV_PYTHON_DOWNLOADS_JSON_URL','UV_PYTHON_INSTALL_MIRROR','UV_PYTHON_MIRROR','PIP_TARGET','PIP_PREFIX','PIP_USER','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','MAX_CUDA_VRAM','CUDA_VISIBLE_DEVICES')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONUTF8 = '1'
    $env:UV_CACHE_DIR = Join-Path $MusicRoot '.cache\uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $MusicRoot '.runtime\python'
    $env:UV_PYTHON_BIN_DIR = Join-Path $MusicRoot '.runtime\python-bin'
    $env:UV_PYTHON_DOWNLOADS = 'never'
    $env:UV_LINK_MODE = 'copy'
    $env:UV_NO_PROGRESS = '1'
    $env:HF_HOME = Join-Path $MusicRoot '.cache\huggingface'
    $env:TEMP = Join-Path $MusicRoot 'tmp'
    $env:TMP = $env:TEMP
    foreach ($path in @($env:UV_CACHE_DIR,$env:TEMP,(Join-Path $MusicRoot '.state'),(Join-Path $MusicRoot 'resultados'))) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
}

function Invoke-Logged {
    param([string]$Exe, [string[]]$ToolArgs, [string]$Log)
    $previous = $ErrorActionPreference
    $code = -1
    try {
        # Windows PowerShell treats native stderr as ErrorRecord; retain it in the log
        # without mistaking progress output for a failed command. Check native exit code.
        $ErrorActionPreference = 'Continue'
        if ($Log) { & $Exe @ToolArgs 2>&1 | Tee-Object -FilePath $Log -Append | ForEach-Object { Write-Host "$_" } }
        else { & $Exe @ToolArgs }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    if ($code -ne 0) { throw "La operacion fallo (codigo $code). No continuar; conservar el registro." }
}

function Get-Hardware {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Se requiere Windows x64 (AMD64).' }
    if ($MusicRoot -match '(?i)kickclip|comfyui|ollama' -or $MusicRoot.StartsWith('\\')) { throw 'Se requiere una carpeta local independiente, fuera de otras apps o rutas de red.' }
    if ($MusicRoot -match '(?i)\\OneDrive(?: - [^\\]+)?\\') {
        $authorized = Join-Path $env:USERPROFILE 'OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'
        if ([IO.Path]::GetFullPath($MusicRoot).TrimEnd('\') -ine [IO.Path]::GetFullPath($authorized).TrimEnd('\')) { throw 'Ubicacion OneDrive no autorizada. Solo se permite expresamente Documentos\MusicStudioAILocal\MusicStudioAILocal-main de este usuario.' }
        Write-Host 'Ubicacion OneDrive autorizada: mantener archivos siempre en este dispositivo y pausar sincronizacion mientras se repara/ejecuta.' -ForegroundColor Yellow
    }
    if ($MusicRoot.Length -gt 110) { throw 'Ruta demasiado larga. Usa una carpeta corta como C:\MusicStudioAILocal.' }
    $os = Get-CimInstance Win32_OperatingSystem
    $ram = [math]::Round(($os.FreePhysicalMemory * 1KB / 1GB), 2)
    $drive = [IO.Path]::GetPathRoot($MusicRoot).TrimEnd('\')
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drive'"
    if (-not $disk -or $disk.FileSystem -ne 'NTFS') { throw 'El proyecto necesita un disco local NTFS.' }
    $smi = Get-Command 'nvidia-smi.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $smiPath = if ($smi) { $smi.Source } else { Join-Path $env:WINDIR 'System32\nvidia-smi.exe' }
    if (-not (Test-Path -LiteralPath $smiPath)) { throw 'No se encuentra nvidia-smi. No se instala ni modifica el controlador.' }
    $lines = & $smiPath '--query-gpu=index,name,memory.total,memory.free,driver_version' '--format=csv,noheader,nounits'
    if ($LASTEXITCODE -ne 0) { throw 'nvidia-smi fallo.' }
    $rows = @($lines | ConvertFrom-Csv -Header Index,Name,Total,Free,Driver)
    $gpu = $rows | Where-Object { [int]$_.Index -eq 0 -and $_.Name -match '4060' } | Select-Object -First 1
    if (-not $gpu) { throw 'No se detecto RTX 4060 como GPU 0. No se cambia la seleccion de otros proyectos.' }
    return [pscustomobject]@{ RAMDisponibleGiB = $ram; DiscoLibreGiB = [math]::Round($disk.FreeSpace / 1GB, 2); Unidad = $drive; VRAMLibreMiB = [int]$gpu.Free; GPU = $gpu.Name.Trim(); Driver = $gpu.Driver.Trim() }
}

function Show-ExactPlan {
    Write-Host 'PLAN DE ETAPA 1 - No se ha descargado nada con esta pantalla.'
    Write-Host "Carpeta exclusiva: $MusicRoot"
    Write-Host '1. uv 0.9.6 privado; Python 3.12.12 privado, sin registro Windows ni PATH global.'
    Write-Host '2. .venv propio: torch/torchaudio 2.10.0+cu128, torchvision 0.25.0+cu128.'
    Write-Host '3. torchao 0.16.0, torchcodec 0.10.0; lock de dependencias con hashes.'
    Write-Host '4. Codigo ACE-Step en commit ca1e85fe9430179831e6bc6be790c332190a3866.'
    Write-Host '5. FFmpeg 8.0.1 compartido privado. Tu FFmpeg actual no se reemplaza.'
    Write-Host '6. Pruebas LOCALES CUDA, INT8/offload, codecs e imports ANTES de pesos.'
    Write-Host '7. Los modelos se descargan aparte con 04, por revisiones y hashes fijados.'
    Write-Host '   Con LM: aprox. 7.18 GiB. Sin LM: aprox. 5.90 GiB. No se descarga LM 1.7B.'
    Write-Host '8. Primera toma: 30 s, lote 1, Turbo 8 pasos, INT8, offload CPU/DiT, sin compile.'
    Write-Host 'Riesgos: 16 GiB RAM puede ser insuficiente incluso sin LM; offload puede ser lento.'
    Write-Host 'Las pruebas locales pueden fallar por DLL/driver/dependencias. No se arreglan globalmente.'
    Write-Host 'Reserva propuesta 60 GiB. No se cierran apps ni se modifica archivo de paginacion.'
    Write-Host 'No hay garantia de voz masculina, letra exacta o calidad hasta generar y escuchar.'
    Write-Host 'Plan completo: PLAN_ETAPA1.md (leer antes de confirmar cualquier descarga).'
}

function Confirm-Exact([string]$Word) {
    $answer = Read-Host "Escribe $Word para continuar; cualquier otra respuesta cancela"
    if ($answer -cne $Word) { throw 'Cancelado sin iniciar esta descarga.' }
}

function Get-VerifiedArchive($Spec, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Spec.sha256) { throw 'Archivo descargado existente con checksum incorrecto; no se usa ni sobrescribe.' }
        return
    }
    $partial = $Destination + '.part'
    Invoke-WebRequest -Uri $Spec.url -OutFile $partial -UseBasicParsing -TimeoutSec 600
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Spec.sha256) { throw 'Checksum incorrecto. No se extrae ni ejecuta el archivo.' }
    Move-Item -LiteralPath $partial -Destination $Destination
}

function Assert-PrivatePython {
    if (-not (Test-Path -LiteralPath $PrivatePython)) { throw 'Falta el Python exclusivo. Ejecutar primero 03_INSTALAR.bat.' }
    $ownerFile = Join-Path $MusicRoot '.state\owner.json'
    if (-not (Test-Path -LiteralPath $ownerFile)) { throw 'Entorno sin marcador de este instalador; no se modifica.' }
    $owner = Get-Content -LiteralPath $ownerFile -Raw | ConvertFrom-Json
    if ($owner.project -ne 'MusicStudioAILocal' -or $owner.root -ne $MusicRoot) { throw 'El entorno se movio o no pertenece a esta carpeta. No se reutiliza.' }
    & $PrivatePython '-I' '-S' '-B' '-c' "import sys,struct; sys.exit(0 if sys.version_info[:3]==(3,12,12) and struct.calcsize('P')==8 else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'El Python privado no es 3.12.12 x64; no se modifica este entorno.' }
}
