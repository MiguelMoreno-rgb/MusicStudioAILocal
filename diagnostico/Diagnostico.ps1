#requires -Version 5.1
# Solo inventario. Sin red, instalaciones, importacion de torch ni cambios globales.
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Este diagnostico requiere Windows.' }
$out = Join-Path $PSScriptRoot ('resultados\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $out -Force | Out-Null
$issues = New-Object 'System.Collections.Generic.List[string]'

function Invoke-ReadOnlyTool {
    param([string]$Exe, [string]$Arguments, [int]$TimeoutSeconds = 20)
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo.FileName = $Exe
    $p.StartInfo.Arguments = $Arguments
    $p.StartInfo.UseShellExecute = $false
    $p.StartInfo.CreateNoWindow = $true
    $p.StartInfo.RedirectStandardOutput = $true
    $p.StartInfo.RedirectStandardError = $true
    try {
        [void]$p.Start()
        $stdout = $p.StandardOutput.ReadToEndAsync()
        $stderr = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
            $p.Kill()
            $p.WaitForExit()
            return [pscustomobject]@{ ExitCode = -1; Output = ''; Error = 'Tiempo de espera agotado; se cerro solo la consulta iniciada.' }
        }
        return [pscustomobject]@{ ExitCode = $p.ExitCode; Output = $stdout.Result.Trim(); Error = $stderr.Result.Trim() }
    } catch {
        return [pscustomobject]@{ ExitCode = -1; Output = ''; Error = $_.Exception.Message }
    } finally { $p.Dispose() }
}
function Find-Tool([string]$Name) {
    $c = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    return $null
}
function To-GiB($Bytes) { return [math]::Round(([double]$Bytes / 1GB), 2) }

$report = [ordered]@{
    Fecha = (Get-Date).ToString('o')
    Etapa = '0 - diagnostico; NO autoriza instalacion'
    Alcance = 'Lectura de inventario; escritura exclusiva en esta carpeta de resultados. No se importan dependencias de otros proyectos.'
    RAM = $null; Windows = $null; Discos = @(); GPU = @(); Nvidia = $null
    Python = @(); PythonLauncher = $null; FFmpeg = $null; ProcesosRelacionados = @()
    VariablesPresentes = @(); Advertencias = @()
}
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $cs = Get-CimInstance Win32_ComputerSystem
    $installed = ($cs.TotalPhysicalMemory)
    try { $installed = (Get-CimInstance Win32_PhysicalMemory | Measure-Object Capacity -Sum).Sum } catch { }
    $report.Windows = [ordered]@{ Version = $os.Caption; Build = $os.BuildNumber; Arquitectura = $os.OSArchitecture }
    $report.RAM = [ordered]@{
        InstaladaGiB = To-GiB $installed
        VisibleGiB = To-GiB ($os.TotalVisibleMemorySize * 1KB)
        DisponibleGiB = To-GiB ($os.FreePhysicalMemory * 1KB)
    }
    if ($report.RAM.InstaladaGiB -lt 31) { $issues.Add('Menos de 32 GiB de RAM: offload puede causar paginacion. 32 GiB es recomendacion de planificacion, no minimo oficial.') }
    if ($report.RAM.DisponibleGiB -lt 12) { $issues.Add('Menos de 12 GiB de RAM disponible: revisar aplicaciones abiertas antes de una futura prueba.') }
} catch { $issues.Add('No se pudo medir RAM/Windows: ' + $_.Exception.Message) }
try {
    $report.Discos = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
        [pscustomobject]@{ Unidad = $_.DeviceID; Sistema = $_.FileSystem; TotalGiB = To-GiB $_.Size; LibreGiB = To-GiB $_.FreeSpace }
    })
    $drive = [IO.Path]::GetPathRoot($PSScriptRoot).TrimEnd('\')
    $target = $report.Discos | Where-Object Unidad -eq $drive | Select-Object -First 1
    $report['UnidadDeEstaCarpeta'] = $drive
    if (-not $target) { $issues.Add('No se pudo identificar el disco local de esta carpeta. Elegir disco de instalacion antes de continuar.') }
    elseif ($target.LibreGiB -lt 60) { $issues.Add('El disco de esta carpeta tiene menos de 60 GiB libres: no cumple la reserva prudente propuesta, pendiente de tamanos exactos.') }
    $report['PresupuestoEstimadoGiB'] = [ordered]@{ PersistenteMin = 22.1; PersistenteMax = 33.3; PicoMin = 30.1; PicoMax = 50.3; ReservaPropuesta = 60; Estado = 'Estimacion, no suma verificada de artefactos' }
} catch { $issues.Add('No se pudieron medir discos: ' + $_.Exception.Message) }
try {
    $report.GPU = @(Get-CimInstance Win32_VideoController | ForEach-Object {
        # AdapterRAM es un entero de 32 bits y NO se utiliza para medir VRAM.
        [pscustomobject]@{ Nombre = $_.Name; ControladorWindows = $_.DriverVersion; Estado = $_.Status }
    })
} catch { $issues.Add('No se pudo leer el inventario de GPU: ' + $_.Exception.Message) }

$smi = Find-Tool 'nvidia-smi.exe'
if (-not $smi) {
    foreach ($candidate in @((Join-Path $env:WINDIR 'System32\nvidia-smi.exe'), (Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe'))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $smi = $candidate; break }
    }
}
if ($smi) {
    $samples = @()
    for ($i = 0; $i -lt 5; $i++) {
        $r = Invoke-ReadOnlyTool $smi '--query-gpu=index,name,memory.total,memory.free,memory.used,driver_version --format=csv,noheader,nounits'
        if ($r.ExitCode -ne 0) { $issues.Add('nvidia-smi no pudo medir VRAM: ' + $r.Error); break }
        foreach ($row in @($r.Output | ConvertFrom-Csv -Header Indice,Nombre,TotalMiB,LibreMiB,UsadaMiB,Controlador)) {
            try {
                $samples += [pscustomobject]@{
                    Fecha = (Get-Date).ToString('o'); Indice = [int]$row.Indice
                    Nombre = $row.Nombre.Trim(); TotalMiB = [int]$row.TotalMiB
                    LibreMiB = [int]$row.LibreMiB; UsadaMiB = [int]$row.UsadaMiB
                    ControladorNVIDIA = $row.Controlador.Trim()
                }
            } catch { $issues.Add('VRAM no disponible o no numerica; no se sustituye por RAM compartida.') }
        }
        if ($i -lt 4) { Start-Sleep -Seconds 1 }
    }
    $summary = Invoke-ReadOnlyTool $smi ''
    $report.Nvidia = [ordered]@{ Muestras = $samples; Resumen = $summary; Nota = 'MiB de memoria dedicada. CUDA Version en el resumen indica capacidad del controlador, no toolkit instalado.' }
    if ($samples.Count -eq 0) { $issues.Add('No se obtuvo ninguna muestra valida de VRAM.') }
    foreach ($gpu in @($samples | Group-Object Indice)) {
        $free = ($gpu.Group | Measure-Object LibreMiB -Minimum).Minimum
        if ($free -lt 6144) { $issues.Add('GPU ' + $gpu.Name + ': menos de 6 GiB libres en alguna muestra. Revisar consumo antes de instalar/probar; no es un umbral oficial de exito.') }
    }
} else { $issues.Add('No se encontro nvidia-smi. La GPU/controlador y VRAM libre necesitan comprobacion; no se instala ningun controlador.') }

$pythonPaths = @()
foreach ($name in @('python.exe','python3.exe')) {
    $pythonPaths += @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object Source)
}
$py = Find-Tool 'py.exe'
if ($py) {
    $launch = Invoke-ReadOnlyTool $py '-0p'
    $report.PythonLauncher = $launch
    foreach ($line in (($launch.Output + "`n" + $launch.Error) -split "`r?`n")) {
        if ($line -match '([A-Za-z]:\\.+?python(?:[0-9.]*)?\.exe)\s*$') { $pythonPaths += $Matches[1] }
    }
}
foreach ($root in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore','HKLM:\Software\WOW6432Node\Python\PythonCore')) {
    if (Test-Path $root) {
        foreach ($version in @(Get-ChildItem $root -ErrorAction SilentlyContinue)) {
            $key = Get-Item ($version.PSPath + '\InstallPath') -ErrorAction SilentlyContinue
            if ($key) {
                $exe = $key.GetValue('ExecutablePath')
                if (-not $exe -and $key.GetValue('')) { $exe = Join-Path ($key.GetValue('')) 'python.exe' }
                if ($exe) { $pythonPaths += $exe }
            }
        }
    }
}
foreach ($exe in @($pythonPaths | Where-Object { $_ } | Sort-Object -Unique)) {
    if ($exe -match '\\Microsoft\\WindowsApps\\' -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { continue }
    # -I -S evita site-packages, PYTHONPATH y scripts de inicio; -B evita escribir pyc.
    $r = Invoke-ReadOnlyTool $exe '-I -S -B -c "import sys,json,struct; print(json.dumps(dict(version=sys.version.split()[0],bits=struct.calcsize(''P'')*8,executable=sys.executable)))"'
    $parsed = $null
    try { if ($r.ExitCode -eq 0) { $parsed = $r.Output | ConvertFrom-Json } } catch { }
    $report.Python += [pscustomobject]@{ Ruta = $exe; Datos = $parsed; Consulta = $r }
}
$compatible = @($report.Python | Where-Object { $_.Datos -and $_.Datos.bits -eq 64 -and $_.Datos.version -match '^3\.(11|12)\.\d+$' })
if ($compatible.Count -eq 0) { $issues.Add('No se detecto Python estable 3.11/3.12 de 64 bits. Python 3.10.9 no cumple ACE-Step actual. No se modifica ni instala Python.') }

$ff = Find-Tool 'ffmpeg.exe'
if ($ff) {
    $version = Invoke-ReadOnlyTool $ff '-version'
    $encoders = Invoke-ReadOnlyTool $ff '-hide_banner -encoders'
    $dlls = @(Get-ChildItem -LiteralPath (Split-Path $ff) -Filter 'avcodec-*.dll' -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
    $report.FFmpeg = [ordered]@{ Ruta = $ff; Version = $version; ConsultaEncoders = $encoders.ExitCode; Libmp3lame = ($encoders.ExitCode -eq 0 -and $encoders.Output -match '\blibmp3lame\b'); DLLAvcodecJuntoAlExe = $dlls; Nota = 'Encontrar ffmpeg.exe no demuestra compatibilidad de sus DLL con TorchCodec.' }
    if ($version.ExitCode -ne 0) { $issues.Add('FFmpeg se encontro pero no pudo ejecutarse.') }
    if (-not $report.FFmpeg.Libmp3lame) { $issues.Add('No se verifico libmp3lame: la exportacion MP3 queda pendiente; WAV sigue siendo el objetivo primario.') }
    if ($dlls.Count -eq 0) { $issues.Add('No se encontraron DLL avcodec junto a FFmpeg. Puede ser build estatica; TorchCodec necesita bibliotecas compartidas. No es prueba concluyente de ausencia en otras rutas.') }
} else { $issues.Add('FFmpeg no esta en PATH; podria existir en otra carpeta. No se cambia PATH ni se instala FFmpeg.') }

$report.ProcesosRelacionados = @(Get-Process -ErrorAction SilentlyContinue | Where-Object ProcessName -match 'ollama|comfy|kickclip' | Select-Object -ExpandProperty ProcessName -Unique)
$report.VariablesPresentes = @('VIRTUAL_ENV','CONDA_PREFIX','PYTHONPATH','PYTHONHOME','HF_HOME','TORCH_HOME','CUDA_PATH' | Where-Object { [Environment]::GetEnvironmentVariable($_, 'Process') })
$report['Limites'] = @('No demuestra ejecucion CUDA/PyTorch ni compatibilidad de INT8.', 'No inspecciona dependencias ni carpetas de KickClipAI, Ollama o ComfyUI; no las modifica.', 'Las rutas de herramientas pueden contener tu nombre de usuario; revisalas antes de compartir el informe.')
$report.Advertencias = @($issues.ToArray())
$json = Join-Path $out 'diagnostico.json'
$txt = Join-Path $out 'RESUMEN.txt'
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $json -Encoding UTF8
$lines = @('MUSIC STUDIO AI LOCAL - DIAGNOSTICO ETAPA 0', 'No se ha instalado ni descargado software/modelos.', '', 'RAM:', ($report.RAM | Format-List | Out-String), 'Discos (GiB):', ($report.Discos | Format-Table -AutoSize | Out-String), 'GPU / VRAM (MiB):', ($report.Nvidia.Muestras | Format-Table -AutoSize | Out-String), 'Python detectado:', ($report.Python.Datos | Format-Table -AutoSize | Out-String), 'FFmpeg:', ($report.FFmpeg | Format-List | Out-String), 'ADVERTENCIAS / PENDIENTES:', ($issues -join [Environment]::NewLine), '', 'Estado: inventario recogido; instalacion NO autorizada. Revisar JSON para detalles y errores.')
$lines | Set-Content -LiteralPath $txt -Encoding UTF8
Write-Host ($lines -join [Environment]::NewLine)
Write-Host "`nResultados: $out"
exit 0
