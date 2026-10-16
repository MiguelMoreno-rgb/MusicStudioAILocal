#requires -Version 5.1
# GET de JSON de metadatos, nunca de pesos ni de codigo ejecutable remoto.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$out = Join-Path $PSScriptRoot ('resultados\modelos-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $out -Force | Out-Null
$manifest = [ordered]@{
    Fecha = (Get-Date).ToString('o')
    CommitCodigoCandidato = 'ca1e85fe9430179831e6bc6be790c332190a3866'
    Estado = 'Metadatos; NO prueba de carga ni integridad de pesos descargados'
    Modelos = @(); Errores = @(); BytesSeleccionados = 0
}
$specs = @(
    @{ Id = 'ACE-Step/Ace-Step1.5'; Carpetas = @('acestep-v15-turbo/','vae/','Qwen3-Embedding-0.6B/') },
    @{ Id = 'ACE-Step/acestep-5Hz-lm-0.6B'; Carpetas = @() }
)
foreach ($spec in $specs) {
    try {
        $base = 'https://huggingface.co/api/models/' + $spec.Id
        $head = Invoke-RestMethod -Uri ($base + '?blobs=true') -Method Get -TimeoutSec 40
        $sha = [string]$head.sha
        if ($sha -notmatch '^[0-9a-f]{40}$') { throw 'La API no devolvio una revision Git SHA de 40 caracteres.' }
        # Releer por revision inmutable: la lista y el SHA pertenecen al mismo snapshot.
        $snapshot = Invoke-RestMethod -Uri ($base + '/revision/' + $sha + '?blobs=true') -Method Get -TimeoutSec 40
        if ($snapshot.sha -ne $sha -or -not $snapshot.siblings) { throw 'Metadatos incompletos o revision inconsistente.' }
        $tag = $spec.Id.Split('/')[-1]
        $snapshot | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $out ($tag + '-metadata.json')) -Encoding UTF8
        $files = @()
        foreach ($file in $snapshot.siblings) {
            $path = [string]$file.rfilename
            $include = ($spec.Carpetas.Count -eq 0)
            foreach ($prefix in $spec.Carpetas) { if ($path.StartsWith($prefix, [StringComparison]::Ordinal)) { $include = $true } }
            if (-not $include) { continue }
            $size = $file.size
            if ($null -eq $size -and $file.lfs) { $size = $file.lfs.size }
            if ($null -eq $size) { throw ('Tamano ausente para ' + $path + '; no se calcula un total falso.') }
            $hash = $null
            if ($file.lfs) { $hash = $file.lfs.sha256 }
            $files += [pscustomobject]@{ Archivo = $path; Bytes = [long]$size; SHA256LFS = $hash; GitBlobId = $file.blobId }
        }
        if ($files.Count -eq 0) { throw 'No hay archivos seleccionados; revisar nombres oficiales.' }
        foreach ($prefix in $spec.Carpetas) {
            $weights = @($files | Where-Object { $_.Archivo.StartsWith($prefix) -and $_.Archivo -match '\.(safetensors|bin)$' })
            if ($weights.Count -eq 0) { throw ('Sin pesos identificables en ' + $prefix) }
        }
        if (@($files | Where-Object Archivo -match '\.(safetensors|bin)$').Count -eq 0) { throw 'No se identificaron pesos del LM.' }
        $sum = [long](($files | Measure-Object Bytes -Sum).Sum)
        $entry = [ordered]@{ Repositorio = $spec.Id; Revision = $sha; LicenciaDeclarada = $snapshot.cardData.license; Gated = $snapshot.gated; Bytes = $sum; GiB = [math]::Round($sum / 1GB, 3); Archivos = $files }
        $manifest.Modelos += $entry
        $manifest.BytesSeleccionados += $sum
        Write-Host ($spec.Id + ' @ ' + $sha + ': ' + $entry.GiB + ' GiB seleccionados')
    } catch {
        $msg = $spec.Id + ': ' + $_.Exception.Message
        $manifest.Errores += $msg
        Write-Host $msg -ForegroundColor Yellow
    }
}
$manifest['Completo'] = ($manifest.Errores.Count -eq 0 -and $manifest.Modelos.Count -eq 2)
if ($manifest.Completo) {
    $manifest['GiBSeleccionados'] = [math]::Round($manifest.BytesSeleccionados / 1GB, 3)
    Write-Host ('Total de archivos seleccionados: ' + $manifest.GiBSeleccionados + ' GiB. No incluye entorno/caches.')
} else { Write-Host 'Verificacion incompleta. No aprobar pesos ni total de espacio con este resultado.' }
$manifest | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $out 'manifest-modelos.json') -Encoding UTF8
Write-Host "Resultados: $out"
Write-Host 'No se descargaron pesos. Los hashes declarados todavia no se han contrastado con archivos locales.'
if (-not $manifest.Completo) { exit 1 }
exit 0
