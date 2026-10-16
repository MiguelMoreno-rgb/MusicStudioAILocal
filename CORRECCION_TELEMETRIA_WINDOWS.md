# WinError 5 al publicar el marcador de memoria

## Diagnóstico y alcance

La segunda prueba terminó en `memory_trace.py`, al reemplazar `memoria-ultima-etapa.json`, mientras registraba la transferencia de `decoder.layers.3.self_attn.k_proj.weight`. **Es una excepción de E/S de telemetría, no evidencia de un peso defectuoso, fallo del kernel INT8 ni una nueva falta de RAM.** No se atribuye a OneDrive: estaba pausado y la traza no identifica al proceso que denegó el acceso.

En la versión `b5b031220ab65d9566aa91d3f1c8889864c7246a` existía una carrera posible por diseño:

| Actor | Acceso anterior al marcador |
|---|---|
| Worker, `MemoryTrace.emit` | Único escritor explícito. Escribía el temporal fijo `memoria-ultima-etapa.json.tmp` y lo reemplazaba en cada evento. |
| Supervisor, hilo principal de `generate.py` | Leía el marcador con `Path.read_text` en cada muestra y al preparar el informe final. Podía coincidir con el reemplazo. |
| Hilo de GPU del supervisor | Consulta `nvidia-smi`; no lee ni escribe el marcador. |
| Hilo de consola del supervisor | Drena stdout del hijo hacia `consola.log`; no accede al marcador. |
| Callbacks del handler/transferencia | Llaman a la misma instancia `MemoryTrace` del worker. No creamos hilos escritores adicionales, pero antes no había exclusión si una biblioteca llamaba concurrentemente. |
| Otros procesos | No identificados mediante la traza recibida. Un lector externo, ACL o atributo de solo lectura también puede impedir el reemplazo. |

En Windows, un handle sin `FILE_SHARE_DELETE` puede bloquear renombrados/reemplazos aunque permita leer y escribir. La lectura anterior mediante CRT/Python no solicitaba explícitamente ese permiso. La colisión entre nuestro lector y escritor es una explicación compatible y se elimina con esta corrección; **no podemos probar retrospectivamente qué handle provocó ese WinError 5**. Un temporal fijo también suponía riesgo si dos llamadas a `emit` coincidían. No se presupone que eso ocurriera en esta ejecución.

Fuentes de la regla Windows: [CreateFileW y FILE_SHARE_DELETE](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) y [compartición de acceso a archivos](https://learn.microsoft.com/en-us/windows/win32/fileio/creating-and-opening-files).

## Corrección

### Publicación del marcador

- Cada intento usa un temporal exclusivo generado por `mkstemp`, en la misma carpeta. El handle del escritor se cierra **antes** de reemplazar. No se comparte ni reutiliza el nombre `.json.tmp` anterior.
- La publicación intenta como máximo tres veces, con esperas de 10 y 20 ms, ante `PermissionError` o errores Windows 5/32/33. No cambia ACL, atributos, antivirus, OneDrive ni permisos del usuario.
- Si sigue rechazado, se registra el fallo y se aplaza otro intento de marcador un segundo; los eventos siguen registrándose. Son reintentos de E/S, **nunca reintentos de generación o de transferencia de pesos**.
- Se elimina únicamente el temporal creado por esa llamada. Si su limpieza falla, se informa y puede quedar ese temporal; no se borran por patrón otros archivos ni registros previos.

### Lectores y concurrencia

`last_event` abre tanto el diario como el marcador en Windows mediante `CreateFileW` con `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`. Transfiere correctamente la propiedad del handle al descriptor Python y lo cierra al terminar, también ante errores. No mantiene el archivo abierto entre muestras.

Lee como máximo 256 KiB de cada fuente y elige el evento válido con mayor secuencia. Del JSONL solo admite líneas terminadas; ignora una cola parcial y fragmentos inválidos sin truncar el historial. Puede usar el diario aunque el marcador esté bloqueado/atrasado, o el marcador si falla el diario. Los errores de lectura inesperados quedan en `worker_telemetry_read_error` de `memoria.csv` y en la última muestra de `monitor.json`; ausencia de archivos antes del primer evento es normal.

Un `RLock` serializa secuencias, publicación y append de una instancia de `MemoryTrace`. La pila de operaciones es local a cada hilo para que sus contextos no se mezclen. El diseño sigue teniendo **un worker escritor por carpeta de ejecución**, como el supervisor actual; no pretende mezclar varios procesos escritores independientes en una misma carpeta.

### Durabilidad y fallos recuperables

`memoria-eventos.jsonl` sigue siendo el historial principal. Cada evento se escribe y hace `flush` antes de intentar publicar el marcador. Se solicita `fsync` al menos cuando un evento llega tras un segundo desde el último sync, y en errores/final del worker. No hay hilo de fsync en reposo. `flush` permite recuperar eventos tras matar el proceso; no garantiza persistencia ante un apagón. `fsync` depende de las garantías y respuesta del almacenamiento.

Si falla el append, el worker conserva una cola acotada a 64 eventos y un presupuesto aproximado de 1 MiB serializado. La próxima oportunidad de reintento es tras 250 ms, o al emitir error/final. No hay reintentos infinitos bloqueantes ni crecimiento ilimitado de RAM por registros. Si la indisponibilidad persiste y la cola se llena, se descartan eventos antiguos **con contador explícito**, conservando los más recientes. Si un fallo sucede después de escribir parcialmente, se separa ese fragmento con una nueva línea y se reintenta el registro sin truncar datos previos. Puede quedar una secuencia duplicada si el SO rechazó flush/close después de aceptar los bytes: la entrega es al menos una vez cuando logra recuperarse, no exactamente una vez.

Los rechazos se conservan en:

- **`memoria-telemetria-errores.jsonl`**: diario independiente de errores, con etapa, secuencia, WinError y contadores; se hace flush/fsync cuando es posible.
- **`consola.log`/stderr**: aviso `TELEMETRIA DEGRADADA`, limitado a un aviso por etapa y segundo, con contadores acumulados. Si también falla el diario de errores, se incluye ese segundo fallo en el aviso y el estado en memoria.
- **`telemetry`** de los eventos y del marcador: errores acumulados, último error, eventos pendientes y descartados. Es una instantánea; los contadores de un evento del historial pueden preceder al fallo de publicación de su marcador. El diario de errores registra ese fallo inmediatamente.

Si todos los destinos son inaccesibles permanentemente o el proceso termina antes de recuperar la cola, **no se promete conservar todos los eventos**. Se intenta reportarlo sin abortar por telemetría. El fallo real de escritura de una canción, una excepción del modelo o una falta de RAM no se convierte en éxito ni se silencia. Los archivos históricos de la prueba fallida no se modifican.

## Protección de memoria y parámetros musicales

La corrección no toca pesos, INT8, offload, seed, pasos, duración ni lote. No cambia paquetes ni configuraciones globales. La toma no se repite automáticamente.

El monitor conserva su muestreo independiente y el corte bajo **1 GiB de RAM física disponible durante tres muestras consecutivas**. No depende del marcador para decidirlo. Se mantienen RSS/private bytes, commit global, VRAM, trazas por tensor, `memoria-interrupcion.json` y `monitor.json`. El fallo de un informe tampoco impide matar al hijo por seguridad.

## Archivos modificados y aplicación

- `musicstudio/memory_trace.py`: lectores compartidos, temporales únicos, exclusión, reintentos acotados, diario/buffer y avisos de E/S.
- `musicstudio/generate.py`: columna de errores de lectura de telemetría; política de RAM intacta.
- `pruebas/test_memory_io.py`: regresiones de E/S y concurrencia, sin música ni modelos.
- Este documento, enlaces de README/CORRECCION_RAM y `SHA256SUMS.txt`.

Ejecuta **ACTUALIZAR_MUSICSTUDIO.bat** para obtener la corrección de código. No necesitas reinstalar, descargar modelos, mover carpetas, copiar ZIP o cambiar paginación. La actualización no ejecuta pruebas locales ni una generación. El marcador de validación INT8 vigente no se invalida por esta corrección exclusiva de telemetría.

## Pruebas y límites

**Resultado en nube: 85 pruebas ejecutadas, 81 aprobadas y 4 omitidas: dos necesitan handles Windows nativos y dos CUDA real.**

Se simulan WinError 5 temporal/persistente, lector vivo que bloquea borrado, append parcial/denegado, fsync rechazado, limpieza denegada, recuperación de cola y pérdida explícita al llenar el buffer. Se ejecutan lectores y escritores concurrentes con archivos reales en nube, verificando secuencias, temporales únicos y aislamiento de pilas. Se comprueban los flags `FILE_SHARE_DELETE` y la transferencia/cierre de handles mediante dobles de Win32. Se mantienen las pruebas del corte por RAM, incluso ante fallo de informes o GPU lenta.

Dos pruebas adicionales usan handles **Windows reales**: un lector compartido debe permitir el reemplazo mientras está abierto, y un lector CRT sin esa compartición no debe abortar al worker. Se omiten en Linux; no requieren GPU. Las pruebas CUDA existentes también se omiten sin GPU. Los dobles y las pruebas POSIX no prueban ACL, bloqueos de terceros o semántica NTFS de la laptop. No se ha ejecutado una generación nueva ni se afirma haber validado esta toma en RTX 4060.

Para verificar solo E/S en Windows cuando decidas hacerlo, desde la carpeta del proyecto en PowerShell:

```powershell
& '.\.runtime\python\cpython-3.12.12-windows-x86_64-none\python.exe' -B -m unittest pruebas.test_memory_io -v
```

Este comando usa biblioteca estándar y temporales; no carga Torch, modelos ni genera audio. Las pruebas reales de bloqueo se habilitan por estar en Windows. No se ejecuta automáticamente al actualizar.
