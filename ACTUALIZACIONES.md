# Actualizar directamente desde GitHub sin ZIP

El sistema usa Git de Windows ya instalado y el Python base privado existente. Funciona con la instalación movida a OneDrive Documentos aunque el Python de `.venv` siga pendiente de reparación. No instala Git, Python, dependencias ni modelos y no ejecuta automáticamente el reparador.

## Primera vez: un comando de PowerShell

Abre **Windows PowerShell de 64 bits, sin administrador**. Copia y ejecuta el siguiente comando completo. Deduce la carpeta `%USERPROFILE%\OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main`; no hay que editar tu nombre ni rutas.

<!-- BOOTSTRAP_COMMAND_BEGIN -->
```powershell
$ErrorActionPreference='Stop'; $r=Join-Path $env:USERPROFILE 'OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main'; $g=(Get-Command git.exe -CommandType Application -ErrorAction Stop|Select-Object -First 1).Source; $x=@(& $g -c http.sslVerify=true ls-remote https://github.com/MiguelMoreno-rgb/MusicStudioAILocal.git refs/heads/main); if($LASTEXITCODE -ne 0 -or $x.Count -ne 1){throw 'No se pudo verificar main'}; $c=($x[0] -split '\s+')[0]; if($c -notmatch '^[0-9a-f]{40}$'){throw 'Commit invalido'}; $d=Join-Path $env:TEMP ('MusicStudio-configurar-'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $d|Out-Null; $s=Join-Path $d 'ConfigurarActualizaciones.ps1'; [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/MiguelMoreno-rgb/MusicStudioAILocal/$c/instalacion/ConfigurarActualizaciones.ps1" -OutFile $s; if((Get-FileHash -LiteralPath $s -Algorithm SHA256).Hash -ine '416156252e436ff37ba864a8426fa32c011f63015b365f42a26af8152a4a0de7'){throw 'SHA256 incorrecto: no ejecutar'}; Start-Process notepad.exe -ArgumentList ('"'+$s+'"') -Wait; if((Read-Host 'Revisa el configurador. Escribe REVISADO para ejecutarlo') -ceq 'REVISADO'){& powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File $s -Root $r -Commit $c}
```
<!-- BOOTSTRAP_COMMAND_END -->

El comando también está en [CONFIGURAR_DESDE_POWERSHELL.txt](CONFIGURAR_DESDE_POWERSHELL.txt). Se ejecuta una sola vez para vincular la carpeta existente; **no descarga un ZIP ni mueve la carpeta**.

1. Consulta el commit exacto de `main` con Git/HTTPS y descarga únicamente el configurador de ese commit a una carpeta temporal nueva. Comprueba su SHA256 contra la huella incluida en el comando antes de ejecutarlo. Si cambió, se detiene: utiliza el comando actualizado de esta página, sin quitar la comprobación.
2. Abre el configurador en Bloc de notas para revisarlo. Cierra esa ventana y escribe **REVISADO** si deseas ejecutarlo. El comando no ejecuta el contenido descargado sin esa confirmación.
3. El configurador pide **PAUSADO**: antes marca el proyecto **Siempre mantener en este dispositivo**, espera a que esté disponible localmente, pausa OneDrive y cierra procesos de este proyecto.
4. Verifica el actualizador Python mediante otro SHA256 incorporado en el configurador, lo abre para revisión y pide **CONFIGURAR** antes de ejecutarlo. Usa el runtime base ya instalado con `-I -S -B`, sin cargar paquetes de `.venv` ni escribir bytecode.
5. Consulta el historial de código publicado, comprueba los archivos existentes y muestra el commit y la lista de incorporaciones, reemplazos o código retirado. Solo escribe tras recibir **ACTUALIZAR**. Cancelar puede dejar únicamente la caché Git de control, nunca reemplazos parciales de código.

Al terminar tendrás `CONFIGURAR_ACTUALIZACIONES.bat`, `ACTUALIZAR_MUSICSTUDIO.bat` y `RECUPERAR_ACTUALIZACION.bat` en la carpeta definitiva. El primero sirve para una carpeta aún no vinculada que ya tenga los archivos; después de vincular, usa el segundo.

## Después de cada publicación de Codex

1. Pausa OneDrive y cierra instalaciones, reparaciones y generaciones del proyecto.
2. Haz doble clic en **ACTUALIZAR_MUSICSTUDIO.bat** y confirma **PAUSADO**.
3. Revisa el commit y los archivos propuestos; escribe **ACTUALIZAR** para aplicarlos con respaldo. Si no hay novedades, lo indica sin reescribir archivos.

El actualizador consulta exclusivamente `main` de `https://github.com/MiguelMoreno-rgb/MusicStudioAILocal.git`. No hace `pull`, checkout, reset, clean, stashes ni merges en tu carpeta. El repositorio Git es **bare y separado**, dentro de `.musicstudio-update\repo.git`; tu carpeta de ZIP no necesita `.git` ni se cambia por otro checkout. No modifica configuración Git global.

Git obtiene metadatos de commits/árboles y mantiene una caché local de objetos. Usa `--filter=blob:none`: solo solicita los contenidos de archivos que necesita incorporar y el manifiesto SHA256, reutilizando los ya descargados. En la vinculación inicial también consulta el historial de metadatos para reconocer versiones previas. No vuelve a descargar todo el ZIP ni los modelos/paquetes.

## Archivos antiguos, modificaciones locales y conflictos

Durante la primera vinculación, un archivo se considera código antiguo reconocido si su contenido exacto coincide con un blob publicado **de esa misma ruta** en el historial de `main`. Esto admite la mezcla conocida que quedó al copiar correcciones anteriores. Un archivo diferente de todas esas versiones provoca un bloqueo; no se decide automáticamente que es una versión vieja.

Tras vincular, se exige que todos los archivos de código gestionados coincidan con la versión registrada, incluso si GitHub no cambió alguno de ellos. Un archivo editado o borrado detiene la actualización. También se detiene si un archivo personal ocupa una nueva ruta que GitHub quiere añadir, o un directorio/enlace ocupa el destino. **No se sobrescriben silenciosamente cambios locales.**

Ante un conflicto:

- Conserva el mensaje y el archivo local. Compáralo con su versión publicada; comparte el nombre y explica si lo editaste.
- Si es una letra, prompt u otro contenido personal, guarda primero una copia fuera de las rutas de código gestionadas, por ejemplo en `config\entrada` o en tu carpeta personal. Los archivos versionados `config\prompt.txt` y `config\letra_ejemplo.txt` son plantillas: editarlos bloqueará el actualizador para proteger tus cambios.
- Restaura solo el archivo de código identificado después de revisar la diferencia, o solicita una integración específica. El actualizador no ofrece una opción de «ignorar y sobrescribir» ni usa `git reset --hard` o `git clean`.

Los archivos personales que no ocupan rutas publicadas se dejan intactos. No se recorren ni inventarían los 51.000 archivos del entorno para actualizar código. Una lista cerrada de rutas y tipos permitidos rechaza modelos, binarios, enlaces Git y rutas protegidas aunque aparecieran en un commit futuro.

## Respaldo y recuperación

Antes de escribir, vuelve a comprobar los archivos observados y los procesos. Verifica objetos Git y los SHA256 publicados, espacio para staging y respaldo, enlaces/reparse points y disponibilidad local. No se desactivan TLS ni las comprobaciones de Git. El manifiesto es una comprobación adicional de integridad; no equivale a una firma digital del autor. La procedencia se establece mediante el repositorio HTTPS fijo, el commit mostrado y, al arrancar, la huella copiada en el comando inicial.

Guarda originales, staging y diario en `.musicstudio-update\backups\<identificador>`. Escribe cada archivo atómicamente y comprueba el árbol de código completo al terminar; no promete atomicidad conjunta de todo el árbol frente a un apagón. Si falla, intenta restaurar únicamente los cambios de esa transacción. No borra respaldos automáticamente.

Si queda una transacción interrumpida, bloquea nuevas actualizaciones. Para recuperarla, ejecuta **RECUPERAR_ACTUALIZACION.bat**, revisa la lista y escribe **RECUPERAR**. También permite deshacer la actualización confirmada que corresponde a la versión actualmente vinculada; no elige una copia arbitraria por fecha. Verifica los hashes del respaldo utilizado y se niega a sobrescribir cambios personales posteriores.

Si el primer arranque se interrumpió antes de crear el BAT de recuperación, repite el comando inicial verificado. Tras revisar y confirmar el código, el actualizador detectará el diario pendiente y ofrecerá **RECUPERAR** antes de volver a vincular. Después repite la configuración. Si hay varios diarios pendientes o un respaldo necesario está incompleto, conserva `.musicstudio-update` y solicita revisión.

Restaurar revierte solo código y el marcador de vinculación del actualizador. Nunca restaura ni modifica dependencias, `.state`, modelos, cachés o música. Si necesitas revertir tras una reparación o generación posterior, esos datos permanecen en su estado actual; no se promete compatibilidad entre versiones arbitrarias de código y datos.

## Concurrencia y OneDrive

Los BAT que cargan `Comun.ps1` y los BAT de actualización comparten un mutex por ruta durante toda su operación: instalación, reparación, validación o generación bloquean el actualizador y viceversa. Además consulta procesos por ruta de ejecutable y línea de comandos, incluidas las rutas antigua y definitiva, antes de empezar y antes de cada escritura. Esto cubre procesos iniciados con los BAT anteriores a esta mejora. Si no puede consultar procesos, se detiene. No cierra otras aplicaciones.

Los programas lanzados directamente saltándose los BAT no toman el mutex; su detección por procesos reduce el riesgo, pero no elimina la carrera de un proceso externo que arranque después de una comprobación. Por eso hay que mantener cerradas las operaciones del proyecto y pausar sincronización. No ejecute dos copias desde diferentes sesiones de Windows o distintos equipos.

Solo se autoriza la ubicación definitiva existente de OneDrive Documentos de este usuario. Se admiten archivos de OneDrive hidratados; se rechazan archivos solo en nube, symlinks y junctions. Mantén disponible localmente tanto el proyecto como `.musicstudio-update`.

**`.gitignore` no impide que OneDrive sincronice.** Git de control y respaldos también pueden subir a tu cuenta, consumir cuota o sufrir conflictos. No sincronices durante operaciones ni uses el mismo entorno desde otro PC. El actualizador no cambia la configuración global de OneDrive.

## Reparación pendiente de `numba`

La primera actualización incorpora la corrección publicada de `musicstudio\relocate.py` e `instalacion\reparacion.lock.json`, además del nuevo flujo. Si ya son los actuales, los conserva. No toca `.venv\Scripts\numba` durante la actualización.

Una vez termine, ejecuta **08_REPARAR_TRASLADO.bat** como paso independiente. Ese reparador conserva sus comprobaciones y su respaldo transaccional, repara solo formatos auditados y después prueba Python/PyTorch/CUDA/INT8. No descargues modelos hasta revisar su resultado. [Detalle de la reparación](REPARAR_TRASLADO.md).

## Verificación en nube y límites

Resultado en nube: **55 pruebas ejecutadas, 54 aprobadas y 1 omitida por requerir CUDA**. Además se simuló la instalación real procedente del ZIP `10c1fa1`, con los dos archivos de la corrección `numba` ya copiados: adoptó la carpeta, actualizó 18 archivos, verificó todo el árbol publicado, preservó los datos privados, comprobó que una segunda actualización no cambiaba nada y recuperó todos los originales byte a byte.

Se prueban operaciones con Git real en remotos temporales: adopción de ZIP y versiones antiguas, descargas de blobs cambiados, modificaciones locales, colisiones con archivos personales, manifiestos alterados, rutas protegidas, cambio de origin/historial, fallos parciales, procesos bloqueantes, cambios concurrentes y recuperación. Se conserva la suite anterior de reparación/INT8. El parser PowerShell verifica sintaxis; no sustituye la prueba Windows real.

No se ha ejecutado el configurador en tu laptop ni se afirma que Git, la reparación o CUDA ya funcionen allí. No hay instalaciones, descargas musicales ni cambios en KickClipAI, ComfyUI, Ollama o dependencias globales.
