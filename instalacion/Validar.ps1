#requires -Version 5.1
. "$PSScriptRoot\Comun.ps1"
try {
    Assert-PrivatePython
    Set-PrivateEnvironment
    $log = Join-Path $MusicRoot ('resultados\validacion-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.log')
    Invoke-Logged $PrivatePython @('-I','-B',$Runner,'validate') $log
    exit 0
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
