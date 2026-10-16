#requires -Version 5.1
. "$PSScriptRoot\Comun.ps1"
Show-ExactPlan
try { Get-Hardware | Format-List | Out-Host } catch { Write-Host $_.Exception.Message -ForegroundColor Yellow }
Write-Host 'Solo se mostro el plan y se leyo inventario. No se descargo ni instalo nada.'
