#requires -Version 5.1
param([switch]$SinLM, [switch]$PorFases, [switch]$PruebaVoz)
. "$PSScriptRoot\Comun.ps1"
try {
    if ($PruebaVoz) { $SinLM = $true; $PorFases = $true }
    Assert-PrivatePython
    $hardware = Get-Hardware
    $hardware | Format-List | Out-Host
    if ($hardware.RAMDisponibleGiB -lt 4 -or $hardware.VRAMLibreMiB -lt 4608) { throw 'Poco margen de memoria. No iniciar; cerrar aplicaciones manualmente y volver a comprobar.' }
    $useLM = -not $SinLM
    $experimental = $false
    if ($PorFases) {
        $useLM = $false
        Write-Host 'CANDIDATA POR FASES: solo Turbo INT8/BF16, sin LM, 30 s, batch 1.'
        Write-Host 'Primero se ejecutan pruebas con tensores diminutos y codecs; no se cargan pesos musicales.'
        Write-Host 'Luego se pedira GENERAR antes de cargar los checkpoints y ejecutar una toma.'
        Set-PrivateEnvironment
        Invoke-Logged $PrivatePython @('-I','-B',$Runner,'phase-check') $null
    }
    if ($useLM -and ($hardware.RAMDisponibleGiB -lt 8 -or $hardware.VRAMLibreMiB -lt 6144)) {
        Write-Host 'Margen reducido: recomendamos SIN LM. Esto cambia la configuracion seleccionada.'
        Write-Host '1 = Sin LM (recomendado); 2 = LM 0.6B experimental con riesgo de falta de memoria.'
        $answer = Read-Host 'Selecciona 1 o 2; otra respuesta cancela'
        if ($answer -eq '1') { $useLM = $false }
        elseif ($answer -eq '2') { $experimental = $true }
        else { throw 'Cancelado.' }
    }
    if ($PruebaVoz) {
        . "$PSScriptRoot\PrepararPruebaVoz.ps1"
    } else {
    Write-Host 'Letra: 1 = ejemplo original de prueba; 2 = elegir tu archivo TXT (p. ej. tu fragmento de Ya No Me Toca).'
    Write-Host 'El ejemplo NO pertenece a Ya No Me Toca y no clona a ningun cantante.'
    $lyricsChoice = Read-Host 'Selecciona 1 o 2; otra respuesta cancela'
    if ($lyricsChoice -eq '1') { $lyricsPath = Join-Path $MusicRoot 'config\letra_ejemplo.txt' }
    elseif ($lyricsChoice -eq '2') {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Filter = 'Letra de cancion (*.txt)|*.txt'
        if ($dialog.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { throw 'Cancelado.' }
        $lyricsPath = $dialog.FileName
    } else { throw 'Cancelado.' }
    $lyrics = Get-Content -LiteralPath $lyricsPath -Raw -Encoding UTF8
    $prompt = Get-Content -LiteralPath (Join-Path $MusicRoot 'config\prompt.txt') -Raw -Encoding UTF8
    $custom = Read-Host 'Prompt opcional (Enter conserva corrido tumbado romantico con voz masculina)'
    if ($custom.Trim()) { $prompt = $custom }
    $seedText = Read-Host 'Semilla (Enter usa 40601)'
    $seed = 40601L
    if ($seedText.Trim()) {
        if (-not [long]::TryParse($seedText, [ref]$seed) -or $seed -lt 0 -or $seed -gt 4294967295) { throw 'Semilla invalida: usa un numero de 0 a 4294967295.' }
    }
    }
    if (-not $lyrics.Trim() -or $lyrics.Length -ge 4096 -or -not $prompt.Trim() -or $prompt.Length -ge 512) { throw 'Letra vacia/demasiado larga (menos de 4096 caracteres) o prompt invalido (menos de 512).' }
    Write-Host "Se generara UNA toma de 30 segundos, semilla $seed, LM=$useLM, batch=1, INT8 y offload. Sin descargas."
    $confirm = Read-Host 'Escribe GENERAR para iniciar; otra respuesta cancela'
    if ($confirm -cne 'GENERAR') { throw 'Cancelado.' }
    Set-PrivateEnvironment
    $request = Join-Path $MusicRoot ('.state\solicitud-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.json')
    $memoryProfile = if ($PorFases) { 'phased_v1' } else { 'streaming_aqt_v1' }
    @{ vocal_test = [bool]$PruebaVoz; memory_profile = $memoryProfile; use_lm = [bool]$useLM; experimental_lm_confirmed = [bool]$experimental; seed = $seed; prompt = $prompt.Trim(); lyrics = $lyrics.Trim(); duration = 30; requested_profile = 'Turbo 2B INT8 offload batch 1' } | ConvertTo-Json | Set-Content -LiteralPath $request -Encoding UTF8
    Invoke-Logged $PrivatePython @('-I','-B',$Runner,'generate',$request) $null
    exit 0
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
