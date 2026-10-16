# RAM: transferencia INT8 y diagnóstico por operación

**Actualización de telemetría:** si Windows rechaza publicar el marcador, consulta [CORRECCION_TELEMETRIA_WINDOWS.md](CORRECCION_TELEMETRIA_WINDOWS.md). La publicación actual usa diarios append-only; consulta [la corrección validable en Windows](CORRECCION_WINDOWS_APPEND.md). Un rechazo de telemetría no aborta por sí solo la toma.

## Lo que sabemos de la primera prueba

Windows/RTX 4060 Laptop 8 GB, RAM 16 GB, Python privado 3.12.12, PyTorch 2.10.0+cu128, TorchAO 0.16.0. Turbo 2B, sin LM, INT8, offload CPU/DiT, 30 segundos, 8 pasos y batch 1. El monitor informó 6.1 GiB disponibles inicialmente, mínimo 0.262 GiB y 33.46 segundos hasta detenerla por tres muestras bajo 1 GiB.

La causa inmediata documentada del término es **la protección del monitor ante presión de RAM física**. El código 1 comunicado por el supervisor no demuestra una excepción OOM de PyTorch ni agotamiento de VRAM. La caída global observada es de aproximadamente 5.84 GiB; no puede asignarse íntegramente al proceso ni a un peso INT8 sin sus series temporales. El último mensaje sitúa la actividad cerca de la carga del modelo a CUDA; no mide el consumo de cada operación.

No tenemos los registros originales completos ni mediciones nuevas de esa laptop. No se afirma que el controlador, OneDrive, el archivo de paginación, una fuga o una copia completa del modelo sean la causa. El perfil sin LM sigue cargando VAE y encoder de texto; offload conserva pesos en RAM cuando no están en GPU. Los archivos de modelos en disco, el dtype lógico BF16 de un wrapper AQT y sus bytes INT8 internos son medidas distintas.

## Investigación del código exacto

Se leyó el código local, verificado por hashes, de ACE-Step commit **ca1e85fe9430179831e6bc6be790c332190a3866**, y el de Torch 2.10.0/TorchAO 0.16.0 del entorno de pruebas existente. No se cambió el vendor ni se descargaron pesos.

- [`init_service_offload_context.py`](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_offload_context.py): mueve cada componente al entrar, lo devuelve a CPU al salir y libera cachés/GC después. La liberación previa al traslado no estaba incluida.
- [`init_service_memory_transfer.py`](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_memory_transfer.py): primero intenta `model.to()`. Si aparece `NotImplementedError`, recorre parámetros y llama a `_move_quantized_param`, que usa `_apply_fn_to_data`.
- [`Module._apply` de PyTorch 2.10](https://github.com/pytorch/pytorch/blob/v2.10.0/torch/nn/modules/module.py): ejecuta la conversión del parámetro **antes** de comprobar `_has_compatible_shallow_copy_type`. Por ello, cuando falla esa comprobación con AQT v1, ya se ha solicitado la transferencia del primer peso cuantizado.
- [`AffineQuantizedTensor`](https://github.com/pytorch/ao/blob/v0.16.0/torchao/dtypes/affine_quantized_tensor.py) y [`PlainAQTTensorImpl`](https://github.com/pytorch/ao/blob/v0.16.0/torchao/dtypes/uintx/plain_layout.py): transfieren datos enteros, escalas y zero-point; en esta ruta no descomprimen todo el modelo a BF16.

La regresión real sobre un Linear INT8 pequeño, dentro de `inference_mode`, contabiliza **dos solicitudes** a `PlainAQTTensorImpl.to` en la ruta anterior y **una** en la nueva. Se usa `meta` para aislar el dispatch: **no acredita bytes reales ahorrados en RAM o VRAM**, ni cuánto tiempo conviven asignaciones reales en Windows. Confirma un intento redundante y una forma de evitarlo. La exploración no demuestra una duplicación completa del modelo en RAM en ese punto. El código de respaldo de decoder para LoRA no pertenece al perfil sin LoRA que ejecutamos.

## Corrección conservadora

`musicstudio/streaming_offload.py` se incorpora solo a nuestro handler, sin editar `.runtime` ni archivos upstream:

1. Si el componente contiene AQT, va directamente a transferencia por parámetros; omite el `Module.to` que sabemos que falla. Mantiene el mecanismo soportado `_apply_fn_to_data`, INT8 v1 y PlainLayout.
2. Transfiere de manera bloqueante y secuencial. No crea un `state_dict`, clon CPU completo, pesos descomprimidos, caché CPU fijada ni colas asíncronas de copias. Reutiliza parámetros ya en el destino y preserva aliases de un mismo parámetro compartido: no guarda referencias a los wrappers CPU sustituidos.
3. Los parámetros densos del componente cuantizado combinan dispositivo y dtype en una sola llamada, evitando el intermedio de `.to(device).to(dtype)` cuando ambos difieren. Buffers conservan su dtype, como upstream. VAE y encoder densos mantienen la transferencia upstream.
4. Añade `gc.collect()` **antes** del traslado; conserva la liberación posterior del contexto upstream. Esto puede liberar objetos inalcanzables, pero no obliga al allocator Windows a devolver RAM. No usa `EmptyWorkingSet`, trims del working set ni cambios del archivo de paginación para maquillar métricas.
5. Comprueba dispositivos internos INT8 y buffers; no silencia errores de transferencia ni cambia a BF16. Conserva el recorrido de módulos no registrados usado por upstream. No admite entrenamiento con gradientes ni otro layout cuantizado de forma implícita.

No cambian los pesos, cuantización, seed solicitada, letra, caption, scheduler, duración, pasos, batch o flags de offload. La salida del forward INT8 en CPU antes/después es idéntica en la regresión. No se ha evaluado calidad musical ni reproducibilidad de una canción CUDA.

**Alcance del ahorro:** se elimina una asignación/transferencia especulativa y se evita duplicar parámetros compartidos cuando existen. No se promete recuperar 5.84 GiB ni que 6.1 GiB iniciales basten para el pipeline completo. La reducción real del pico global y el margen de esta laptop requieren medición en Windows. La instrumentación tiene coste de tiempo/IO, sin cambiar los parámetros musicales.

## Qué se registrará en una futura prueba autorizada

| Medida | Significado y límite |
|---|---|
| `system_available_bytes` | RAM física que Windows considera disponible. Se mantiene como señal de seguridad. |
| `process_rss_bytes` | Working set residente del hijo; puede incluir páginas compartidas. No equivale a memoria comprometida. |
| `process_private_bytes` | Private bytes del proceso en Windows: memoria privada comprometida, residente o paginada. No mide RAM física ocupada exclusivamente. |
| `process_vms_bytes` | Contador VMS reportado por psutil; no se interpreta como RAM física ni se suma a RSS/private. |
| `system_commit_bytes`, `system_commit_limit_bytes`, `system_commit_peak_bytes` | Commit global, límite y pico global desde `GetPerformanceInfo` multiplicando páginas por `PageSize`. El pico de Windows puede ser anterior a la prueba. |
| `system_swap_used_bytes` | Contador heredado de psutil; no sustituye commit y su semántica es dependiente del SO. |
| VRAM `nvidia-smi` | Uso/libre global de GPU, incluidos otros procesos y driver; se registra antigüedad de la muestra. |
| CUDA allocated/reserved y sus máximos | Contadores del allocator PyTorch del worker. No incluyen toda la memoria del driver, otros procesos ni toda la memoria CUDA externa. |
| `payload_bytes` por tensor | Tamaño de los datos internos para la transferencia, no RSS ni un censo de RAM física; vistas de almacenamiento pueden solaparse. |

Los contadores ausentes se guardan como `null` con error explícito, nunca como cero inventado. El worker no carga CUDA por consultar métricas antes de inicializarla.

- **`memoria-eventos.jsonl`**: inicio/fin/error con reloj monotónico, secuencia, PID, métricas, pila de operaciones y nombre del parámetro/buffer. Incluye preparación, verificación de checkpoints, inicialización, contextos de VAE/encoder/modelo, transferencia tensor a tensor, `prepare_condition`, `generate_audio`, inferencia y exportación WAV. No añade letra ni prompt a esos eventos.
- **`memoria-ultima-etapa.json`**: marcador atómico del último evento, actualizado y volcado antes de cada copia para sobrevivir a `kill`. Un inicio sin fin señala la operación en curso; no demuestra por sí solo causalidad.
- **`memoria.csv`**: muestra del padre con RSS/private/commit/RAM/VRAM y secuencia/operación del worker para correlacionar relojes. Los picos entre muestras pueden perderse; las métricas de fin tampoco capturan necesariamente el pico dentro de un kernel.
- **`memoria-interrupcion.json`** y **`monitor.json`**: muestran la muestra que provocó el corte y el último evento observado, incluso si el worker no alcanzó a escribir `resultado.json`.

`nvidia-smi` ahora se consulta en un hilo separado: una respuesta lenta no detiene el bucle principal de RAM. **Se mantiene exactamente el corte bajo 1 GiB durante tres muestras consecutivas**, con pausa nominal de un segundo entre muestras; la duración exacta incluye el coste del muestreo/IO. El corte no depende de que pueda escribir el informe. Ante un fallo inesperado del monitor se detiene su propio hijo por seguridad. No se cierran aplicaciones ajenas.

## Aplicación y revisión, sin generar todavía

1. Ejecuta **ACTUALIZAR_MUSICSTUDIO.bat** como actualización de código normal, con OneDrive pausado y procesos del proyecto cerrados. Revisa la lista y confirma; conserva conflictos locales y respaldos según [ACTUALIZACIONES.md](ACTUALIZACIONES.md). No hace falta descargar ZIP ni copiar archivos.
2. Lee este documento y revisemos la solución. **No ejecutes todavía una generación.** La actualización no lanza instalación, validación o música automáticamente.
3. Cuando autoricemos las pruebas locales, el primer paso previsto será **07_VALIDAR_ENTORNO.bat**: conserva la regresión upstream y añade dos rondas CPU/CUDA de la nueva transferencia con un Linear 64×64 sintético. No usa modelos musicales ni evalúa el pipeline completo.
4. Un marcador de validación antiguo ya no habilita la generación: debe contener el resultado de `move_streaming`. Esto evita saltarse la comprobación local de la nueva ruta. Después de revisar esa validación decidiremos si autorizar una toma sin LM con la misma configuración, recogiendo los nuevos registros.

No se reinstalan Python/CUDA/Torch/TorchAO ni los 57 paquetes; no se descargan modelos, cambian variables globales/FFmpeg/paginación, mueven carpetas ni se modifica KickClipAI/Ollama/ComfyUI. Las huellas del reparador se actualizan para exigir el código corregido: no se eliminan controles. El actualizador conserva su mecanismo y huellas de arranque.

## Verificación y límites

**Resultado en nube: 71 pruebas ejecutadas, 69 aprobadas y 2 omitidas por requerir CUDA real.** Se prueban con las versiones reales Torch 2.10.0 CPU/TorchAO 0.16.0 ya disponibles: dispatch original, número de solicitudes de copia, bytes INT8/escalas, igualdad de forward CPU, aliases compartidos, rechazo de dtype incompatible y propagación de errores. No se usan checkpoints musicales. Las pruebas de telemetría cubren significados distintos de RSS/private/commit/VMS, persistencia de eventos, reinicio del contador de RAM, corte de tres muestras con GPU lenta y bloqueo de marcadores antiguos.

CPU y `meta` no reproducen CUDA, WDDM, presión real de RAM, transferencias PCIe o el allocator de Windows. Las pruebas CUDA quedan omitidas sin GPU. El ahorro de RAM real, funcionamiento del API Windows en la laptop, tiempo y resultado musical siguen pendientes; no se ejecutó ninguna generación nueva.
