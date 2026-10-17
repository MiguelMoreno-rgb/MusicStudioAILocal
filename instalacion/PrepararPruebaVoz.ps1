#requires -Version 5.1
# Dot-sourced only after Comun.ps1 acquired the existing project mutex.
Write-Host 'PRUEBA VOCAL CONTROLADA: una toma, sin LM, mismo pipeline por fases.'
$vocalOutput = & $PrivatePython -I -B -c "import json,sys; sys.path.insert(0,sys.argv[1]); from musicstudio.vocal_conditioning import CONTROLLED_PROMPT,CONTROLLED_LYRICS; print(json.dumps([CONTROLLED_PROMPT,CONTROLLED_LYRICS]))" $MusicRoot
if ($LASTEXITCODE -ne 0) { throw 'No se pudo leer la configuracion vocal local.' }
$vocalInput = $vocalOutput | ConvertFrom-Json
$prompt = [string]$vocalInput[0]
$lyrics = [string]$vocalInput[1]
$seed = 40601L
Write-Host $prompt
Write-Host $lyrics
Write-Host '30 segundos, 8 pasos, shift 3, semilla 40601; comprobar canto mediante escucha.'
