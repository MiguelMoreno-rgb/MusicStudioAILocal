"""Maintainer-only: synchronize updater trust pins, bootstrap command, manifest."""
import hashlib
from pathlib import Path
import re
import subprocess
ROOT = Path(__file__).resolve().parents[1]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
ps = ROOT / 'instalacion/ConfigurarActualizaciones.ps1'
s = ps.read_text()
s, count = re.subn(r"\$sha = '(?:UPDATER_SHA_PLACEHOLDER|[0-9a-f]{64})'", "$sha = '" + sha(ROOT/'instalacion/actualizar.py') + "'", s)
assert count == 1
ps.write_bytes(s.replace('\r\n','\n').replace('\n','\r\n').encode('ascii'))
command = r'''$ErrorActionPreference='Stop'; $r=Join-Path $env:USERPROFILE 'OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'; $g=(Get-Command git.exe -CommandType Application -ErrorAction Stop|Select-Object -First 1).Source; $x=@(& $g -c http.sslVerify=true ls-remote https://github.com/MiguelMoreno-rgb/MusicStudioAILocal.git refs/heads/main); if($LASTEXITCODE -ne 0 -or $x.Count -ne 1){throw 'No se pudo verificar main'}; $c=($x[0] -split '\s+')[0]; if($c -notmatch '^[0-9a-f]{40}$'){throw 'Commit invalido'}; $d=Join-Path $env:TEMP ('MusicStudio-configurar-'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $d|Out-Null; $s=Join-Path $d 'ConfigurarActualizaciones.ps1'; [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/MiguelMoreno-rgb/MusicStudioAILocal/$c/instalacion/ConfigurarActualizaciones.ps1" -OutFile $s; if((Get-FileHash -LiteralPath $s -Algorithm SHA256).Hash -ine 'BOOTSTRAP_SHA'){throw 'SHA256 incorrecto: no ejecutar'}; Start-Process notepad.exe -ArgumentList ('"'+$s+'"') -Wait; if((Read-Host 'Revisa el configurador. Escribe REVISADO para ejecutarlo') -ceq 'REVISADO'){& powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File $s -Root $r -Commit $c}'''.replace('BOOTSTRAP_SHA',sha(ps))
(ROOT/'CONFIGURAR_DESDE_POWERSHELL.txt').write_text(command+'\n')
p=ROOT/'ACTUALIZACIONES.md';s=p.read_text();start='<!-- BOOTSTRAP_COMMAND_BEGIN -->';end='<!-- BOOTSTRAP_COMMAND_END -->';a,rest=s.split(start);_,b=rest.split(end);p.write_text(a+start+'\n```powershell\n'+command+'\n```\n'+end+b)
names=set(filter(None,subprocess.check_output(['git','-C',str(ROOT),'ls-files','-co','--exclude-standard','-z']).decode().split('\0')))
(ROOT/'SHA256SUMS.txt').write_text(''.join(f'{sha(ROOT/n)}  {n}\n' for n in sorted(names) if n!='SHA256SUMS.txt'))
print('Huellas sincronizadas. Bootstrap SHA256:',sha(ps))
