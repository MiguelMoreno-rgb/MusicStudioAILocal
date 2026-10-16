#requires -Version 5.1
. "$PSScriptRoot\Comun.ps1"
try {
    Show-ExactPlan
    Assert-PrivatePython
    $hardware = Get-Hardware
    if ($hardware.DiscoLibreGiB -lt 12) { throw 'Menos de 12 GiB libres para descargar pesos y mantener margen.' }
    if (-not (Test-Path (Join-Path $MusicRoot '.state\validation.json'))) { throw 'No hay validacion local aprobada. Ejecutar 07_VALIDAR_ENTORNO.bat.' }
    Write-Host '1 = Turbo + VAE + encoder + LM 0.6B (7.18 GiB aprox.)'
    Write-Host '2 = Solo Turbo + VAE + encoder, sin LM (5.90 GiB aprox.; menor consumo)'
    $choice = Read-Host 'Selecciona 1 o 2; otra respuesta cancela'
    if ($choice -notin @('1','2')) { throw 'Cancelado.' }
    Confirm-Exact 'DESCARGAR'
    Set-PrivateEnvironment
    $log = Join-Path $MusicRoot ('resultados\descarga-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.log')
    $argsForPython = @('-I','-B',$Runner,'models')
    if ($choice -eq '2') { $argsForPython += '--sin-lm' }
    Invoke-Logged $PrivatePython $argsForPython $log
    exit 0
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
