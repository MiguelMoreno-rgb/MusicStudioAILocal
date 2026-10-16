#requires -Version 5.1
param(
    [ValidateSet('Setup','Update','Recover')][string]$Mode = 'Setup',
    [string]$Root = (Split-Path $PSScriptRoot -Parent),
    [ValidatePattern('^[0-9a-f]{40}$')][string]$Commit
)
$ErrorActionPreference = 'Stop'
$mutex = $null
$held = $false
try {
    $allowed = Join-Path $env:USERPROFILE 'OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'
    $Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if ($Root -ine [IO.Path]::GetFullPath($allowed).TrimEnd('\')) { throw 'Solo se autoriza la carpeta definitiva de OneDrive\Documentos.' }
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess) { throw 'Ejecutar PowerShell de Windows x64.' }
    $volume = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Root))
    if ($volume.DriveFormat -ine 'NTFS') { throw 'Se requiere NTFS para conservar las comprobaciones de este proyecto.' }
    $keyBytes = [Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant())
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $key = ([BitConverter]::ToString($hasher.ComputeHash($keyBytes))).Replace('-','') } finally { $hasher.Dispose() }
    $mutex = New-Object Threading.Mutex($false, ('Local\MusicStudioAILocal-' + $key))
    try { $held = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { throw 'Otra operacion del proyecto esta activa. Cierra su BAT y espera a que termine.' }
    Write-Host 'Mantener esta carpeta siempre en el dispositivo. Pausar OneDrive y cerrar procesos del proyecto.' -ForegroundColor Yellow
    if ((Read-Host 'Escribe PAUSADO para confirmar estas condiciones') -cne 'PAUSADO') { throw 'Cancelado sin actualizar codigo.' }
    $gitCommand = Get-Command git.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
    if ($gitCommand.Source.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Se requiere Git de Windows ya instalado fuera del proyecto.' }
    & $gitCommand.Source --version
    if ($LASTEXITCODE -ne 0) { throw 'Git no funciona. No se instala automaticamente.' }
    $python = Join-Path $Root '.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe'
    foreach ($item in @($Root, (Split-Path $python -Parent), $python)) {
        $info = Get-Item -LiteralPath $item -Force
        if (([int]$info.Attributes -band (0x1000 -bor 0x40000 -bor 0x400000)) -ne 0 -or $info.LinkType -in @('SymbolicLink','Junction')) { throw 'Ruta no local o enlace. Esperar disponibilidad local; no se descarga Python.' }
    }
    $sha = '440afb4ee7b761c501d4777ca511ea265b6f7487c732d5d67bff161337520b4c'
    if ($Commit) {
        # Initial bootstrap only: exact immutable commit + embedded SHA256.
        $source = "https://raw.githubusercontent.com/MiguelMoreno-rgb/MusicStudioAILocal/$Commit/instalacion/actualizar.py"
        $scriptFile = Join-Path $PSScriptRoot 'actualizar-verificado.py'
        if (Test-Path -LiteralPath $scriptFile) { throw 'El staging inicial ya existe. Usa una carpeta temporal nueva.' }
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $source -OutFile $scriptFile
    } else {
        $scriptFile = Join-Path $PSScriptRoot 'actualizar.py'
    }
    if ((Get-FileHash -LiteralPath $scriptFile -Algorithm SHA256).Hash -ine $sha) { throw 'SHA256 del actualizador no coincide. No se ejecuta; conservar el mensaje.' }
    Write-Host ('Codigo verificado: ' + $scriptFile)
    Write-Host ('SHA256: ' + $sha)
    if ($Commit) {
        Start-Process notepad.exe -ArgumentList ('"'+$scriptFile+'"') -Wait
        if ((Read-Host 'Revisa el codigo. Escribe CONFIGURAR para ejecutarlo') -cne 'CONFIGURAR') { throw 'Cancelado sin ejecutar el actualizador descargado.' }
    }
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    $env:PYTHONIOENCODING = 'utf-8'
    & $python -I -S -B $scriptFile --root $Root --mode $Mode --git $gitCommand.Source
    $result = $LASTEXITCODE
    if ($result -ne 0) { throw ('Actualizacion/recuperacion detenida. Codigo: '+$result+'. No reinstalar ni borrar archivos.') }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($held -and $mutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
