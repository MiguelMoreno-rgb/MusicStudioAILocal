#requires -Version 5.1
. "$PSScriptRoot\Comun.ps1"
try {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess) { throw 'Se requiere Windows x64 real.' }
    if ($MusicRoot -match '(?i)kickclip|comfyui|ollama' -or $MusicRoot.StartsWith('\\')) { throw 'La ruta del proyecto no es una carpeta local independiente.' }
    if ($MusicRoot.IndexOf('\OneDrive\', [StringComparison]::OrdinalIgnoreCase) -ge 0 -or $MusicRoot.IndexOf('\OneDrive - ', [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $authorized = Join-Path $env:USERPROFILE 'OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'
        if ([IO.Path]::GetFullPath($MusicRoot).TrimEnd('\') -ine [IO.Path]::GetFullPath($authorized).TrimEnd('\')) { throw 'Ubicacion OneDrive no autorizada. Solo se permite expresamente Documentos\MusicStudioAILocal\MusicStudioAILocal-main.' }
        Write-Host 'OneDrive Documentos autorizado: mantenga el proyecto disponible localmente y pause la sincronizacion durante la prueba.' -ForegroundColor Yellow
    }
    $basePython = Join-Path $MusicRoot '.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe'
    if (-not (Test-Path -LiteralPath $basePython -PathType Leaf) -or -not (Test-Path -LiteralPath $PrivatePython -PathType Leaf)) { throw 'Falta el Python privado o el entorno .venv; no se instala ni repara nada.' }
    foreach ($path in @($basePython,$PrivatePython)) {
        $info = Get-Item -LiteralPath $path
        if (([int]$info.Attributes -band (0x1000 -bor 0x40000 -bor 0x400000)) -ne 0) { throw 'Python privado no esta disponible localmente; no se intentara hidratar ni descargar.' }
    }
    Assert-PrivatePython
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Write-Host 'Se cargara solo Qwen3-Embedding-0.6B en CPU/BF16. No se cargan DiT/VAE ni se genera musica.'
    Invoke-Logged $privatePython @('-I','-B','-X','utf8',(Join-Path $PSScriptRoot 'validar_qwen_bf16.py')) $null
    exit 0
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
