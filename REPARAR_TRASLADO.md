# Reparar el traslado a Documentos sin reinstalar los paquetes

La herramienta **08_REPARAR_TRASLADO.bat** está preparada exclusivamente para este traslado del mismo usuario:

- Original: `%USERPROFILE%\Downloads\MusicStudioAILocal-main`
- Definitiva: `%USERPROFILE%\OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main`

`%USERPROFILE%` es tu carpeta de usuario Windows. El BAT deduce ambas rutas; no hay que editar código. Debe ejecutarse desde la carpeta definitiva, junto a `.venv` y `.runtime`. No es una herramienta general para trasladar cualquier entorno ni autoriza otras carpetas de OneDrive.

## Qué causó el error

Un entorno virtual normal de Windows no se vuelve portable por moverlo. `pyvenv.cfg` conserva referencias al runtime original. Además, uv 0.9.6 puede crear lanzadores **UVPY** para Python y **UVSC** para scripts de paquetes con una ruta absoluta dentro del EXE. El mensaje `No Python at ...Downloads...` es compatible con esas referencias antiguas. Antes de decidir qué necesita tu copia, la herramienta analiza su configuración y lanzadores reales.

No basta con actualizar `owner.json`, y no se hará una sustitución indiscriminada de bytes en binarios. Se reconocen las plantillas oficiales de uv por SHA256 y su formato documentado: payload + ruta UTF-8 + longitud u32 + `UVPY`/`UVSC`. Solo se cambia esa ruta y su longitud. El PE y el script embebido quedan idénticos. Si no reconoce el formato, se detiene.

Fuente técnica del formato: [Launcher y with_python_path de uv 0.9.6](https://github.com/astral-sh/uv/blob/0.9.6/crates/uv-trampoline-builder/src/lib.rs). Las huellas de las plantillas oficiales están en `instalacion/reparacion.lock.json`; no se distribuyen ni descargan nuevos EXE para ejecutar la reparación.

## Aplicar los archivos, sin sobrescribir `.venv` ni `.runtime`

1. Cierra los BAT, terminales Python y generaciones de **este proyecto**. No necesitas cerrar ni modificar KickClipAI, ComfyUI u Ollama para reparar rutas; si consumen memoria, las guardas pueden pedirte liberar recursos manualmente.
2. Descarga el [ZIP actualizado de GitHub](https://github.com/MiguelMoreno-rgb/MusicStudioAILocal/archive/refs/heads/main.zip) y **extráelo en otra carpeta temporal**. No lo extraigas encima de tu instalación.
3. Copia únicamente estos archivos desde el ZIP a las mismas ubicaciones relativas de la instalación definitiva:

   | Archivo | Acción |
   |---|---|
   | `08_REPARAR_TRASLADO.bat` | Añadir en la raíz del proyecto |
   | `instalacion/Comun.ps1` | Reemplazar; contiene la excepción de ubicación específica |
   | `instalacion/RepararTraslado.ps1` | Añadir |
   | `instalacion/reparacion.lock.json` | Añadir |
   | `musicstudio/relocate.py` | Añadir |
   | `musicstudio/validate.py` | Copiar la versión corregida INT8 |
   | `musicstudio/int8_offload.py` | Copiar el helper de validación INT8 |

   No copies, borres ni recrees `.venv`, `.runtime`, `.cache`, `.state`, `checkpoints` o `resultados`. La herramienta actualizará internamente solo las referencias controladas que autorices. Los otros archivos del ZIP son documentación/pruebas; no necesitas copiarlos para ejecutar el arreglo. Si informa que `musicstudio/common.py` o `instalacion/ejecutar.py` difieren de la versión esperada, conserva el mensaje: son código propio del proyecto y pueden actualizarse desde el ZIP tras revisar tus cambios; no se sobrescriben automáticamente.

4. En el Explorador, marca la carpeta definitiva como **Siempre mantener en este dispositivo**. Espera a que OneDrive termine de recuperar todos sus archivos; después **pausa la sincronización** desde OneDrive. La herramienta no descarga paquetes ni fuerza hidratación de archivos de nube.
5. Haz doble clic en **08_REPARAR_TRASLADO.bat**, dentro de la carpeta definitiva. No necesita administrador ni Python global.
6. Confirma con **PAUSADO** que completaste el paso de OneDrive y cerraste procesos de este proyecto. Comienza una comprobación de solo lectura; puede tardar varios minutos porque calcula hashes de muchos archivos.
7. Solo si la comprobación termina, muestra la lista exacta de archivos que necesita modificar. Escribe **REPARAR** para aplicarla; cualquier otra respuesta cancela sin cambios ni copias de respaldo. No ejecutes `03_INSTALAR.bat` ni `04_DESCARGAR_MODELOS.bat` para reparar.
8. Al terminar, comparte el nuevo `validacion.json` de la carpeta más reciente de `resultados`. Si falla, conserva el texto completo y el respaldo; **no descargues modelos** hasta revisar el resultado.

## Antes de la primera escritura

Se utiliza directamente el Python standalone que ya existe en `.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe`, con `-I -S -B`. Esto evita depender del lanzador roto de `.venv`, evita site-packages/globales y no escribe bytecode.

El proceso de comprobación:

- Comprueba Windows x64, NTFS, la ubicación exacta, ambos ejecutables privados y Python 3.12.12 x64. Lee si la ruta original todavía existe; nunca escribe en ella. Si allí queda otra instalación, contrasta versiones y contenido de paquetes y se detiene ante diferencias, sin fusionarlas. Si solo queda una carpeta sin `.venv`, no la trata como una segunda instalación.
- Mantiene guardas: al menos 4 GiB de RAM libre y 4.5 GiB de VRAM libre para las pruebas posteriores y 1 GiB de disco para respaldo/registros. No instala un controlador ni libera memoria cerrando otras aplicaciones.
- Rechaza procesos activos desde estas dos instalaciones, enlaces/junctions en los directorios protegidos, archivos solo en nube, referencias ajenas, configuraciones desconocidas y objetivos de reparación sin permiso de escritura. No cambia permisos automáticamente.
- Verifica que existe la corrección INT8 y el código necesario esperado. El lock de las 57 dependencias, Python, PyTorch, TorchAO y TorchCodec no cambia.
- Lee las distribuciones instaladas, exige que estén presentes las 57 con sus versiones fijadas, comprueba los archivos declarados en sus `RECORD` y sus hashes cuando están declarados. No importa ni reinstala los paquetes en esa fase.
- Calcula un inventario SHA256 de los archivos de `.venv`, `.runtime`, `.cache`, `checkpoints` y `resultados` presentes. No mueve ni borra estos directorios. El cálculo usa lecturas secuenciales por bloques, sin cargar los paquetes completos en RAM.
- Construye una lista cerrada de cambios permitidos. No busca y sustituye rutas dentro de todos los archivos del proyecto ni altera resultados históricos con rutas antiguas.

Si falta un paquete, un archivo no coincide, hay una copia ambigua o no se puede comprobar el contenido, **se detiene antes de reparar** e indica el archivo o condición que necesita revisión. No propone resolverlo con una instalación completa ni una descarga automática. El éxito de esta comprobación acredita que puede aplicar ese plan conservando los archivos observados; no certifica la salud previa de toda la laptop ni elimina el riesgo de una interrupción externa posterior.

## Escrituras permitidas y conservación

Después de aprobar el plan:

1. Repite el inventario para detectar cambios desde la vista previa. Si cambió, cancela antes de escribir.
2. Crea `.state\repair-backups\<fecha-hora>`, guarda los originales de cada objetivo, inventario y diario de la transacción.
3. Actualiza únicamente los campos de ruta reconocidos de `.venv\pyvenv.cfg`, rutas de activación reconocidas, trailers de launchers uv reconocidos y `.state\owner.json`. Si cambia un launcher de paquete, ajusta **solo su hash y tamaño en el `RECORD` correspondiente** para mantener consistente la metadata instalada. Código de los paquetes, DLL, pesos, versiones y payloads ejecutables no se reemplazan.
4. Invalida el marcador de validación de la ubicación anterior, que debe volver a generarse. No borra informes anteriores.
5. Comprueba por inventario que no se modificó ningún otro archivo protegido. Prueba el Python de `.venv`, su nuevo `sys.prefix`, PyTorch 2.10.0+cu128, disponibilidad CUDA 12.8 y una operación BF16 pequeña en la GPU. Después vuelve a comprobar conservación de los archivos y versiones.

Si esa prueba falla o se detectan modificaciones inesperadas, intenta restaurar **solo sus cambios** desde el respaldo. No sobrescribe un cambio externo detectado al restaurar: lo registra y exige revisión. Si una caída abrupta impide completar la reversión, el diario queda pendiente y una ejecución posterior se detiene; conserva el respaldo y solicita ayuda, no lo borres ni reinstales a ciegas.

Cuando esa fase termina correctamente, el traslado queda confirmado y ejecuta la **validación completa actual**, incluida INT8 y los codecs, usando el Python reparado. Si aparece entonces un error independiente de INT8/FFmpeg/imports, conserva las referencias reparadas y los paquetes: informa «traslado reparado, validación completa pendiente». El marcador de éxito completo solo se crea si todas esas comprobaciones pasan. Reparar rutas no demuestra por sí solo que la generación musical funcione.

## OneDrive: excepción y condiciones

La restricción general no desaparece. `Comun.ps1` permite expresamente y solo `%USERPROFILE%\OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main`. Conserva el rechazo a otras ubicaciones OneDrive, rutas UNC, carpetas de otras aplicaciones, rutas demasiado largas y discos no NTFS. El marcador sigue teniendo que coincidir con la raíz real.

Mantén el proyecto disponible localmente. Pausa sincronización durante reparación, instalación y generación para reducir conflictos, bloqueos de DLL y archivos cambiados a mitad de una comprobación. No ejecutes la misma copia sincronizada simultáneamente desde otro PC ni permitas que Storage Sense retire archivos necesarios mientras se usa.

**`.gitignore` no excluye archivos de OneDrive.** Al reanudarlo, `.venv`, runtime, cachés, modelos, respaldos, letras y resultados pueden subir a tu cuenta y consumir cuota. «Siempre mantener» conserva la copia local; no impide la sincronización. La herramienta no cambia configuración, cuenta, cuotas ni políticas globales de OneDrive y no puede observar con fiabilidad su estado de pausa: depende de tu confirmación y de las comprobaciones locales. Los nombres/rutas de los respaldos e informes pueden contener datos personales; compártelos por chat cuando hagan falta, no los publiques en GitHub.

## Verificación y límites

En nube se prueban parsing de launchers/cfg, conservación del payload, metadata `RECORD`, rechazo de referencias ajenas, cambios concurrentes y rollback mediante archivos temporales. El parser PowerShell comprueba sintaxis; no ejecuta CIM ni un launcher Windows. También se mantiene la regresión real INT8 CPU de la corrección anterior.

La herramienta **todavía necesita ejecutarse en tu Windows**. No se ha accedido ni reparado la laptop desde la nube; no se afirma que CUDA o la instalación funcionen allí antes de recibir sus nuevos resultados. No hay descargas de modelos musicales ni cambios de KickClipAI, ComfyUI, Ollama o Python global.
