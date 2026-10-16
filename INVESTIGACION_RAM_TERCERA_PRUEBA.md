# Investigación de RAM: tercera prueba real

Esta rama contiene una optimización acotada para revisión. **No se ejecutó ACE-Step, no se descargaron pesos y no se validó el cambio en la laptop Windows.** El monitor de RAM y la telemetría de Windows no cambiaron.

## Hallazgo

El registro confirma que el proceso llegó a `compute.text_encoder` antes de que el supervisor detuviera el worker por RAM física disponible bajo 1 GiB durante tres muestras. En la muestra comunicada:

| Contador | Valor convertido | Qué representa |
|---|---:|---|
| RAM disponible inicial | 7.29 GiB | RAM que Windows consideraba disponible al inicio |
| RAM disponible mínima | 0.317 GiB | RAM física disponible; activa el protector |
| RSS del worker | 7.71 GiB | Working set residente, no commit total |
| Private bytes del worker | 14.08 GiB | Memoria privada comprometida; puede incluir páginas no residentes |
| CUDA allocated del worker | 1.12 GiB | Asignaciones contabilizadas por el allocator PyTorch |
| VRAM global libre | 5.61 GiB | Instantánea global de GPU, incluidos otros procesos/driver |

La bajada de RAM disponible fue de 6.973 GiB. No se puede atribuir esa caída completa al worker. El desfase entre RSS y private bytes confirma que no se deben tratar esos contadores como equivalentes. La muestra no demuestra OOM de CUDA: quedaban 5.61 GiB globales libres en ese instante. Tampoco identifica por sí sola qué transferencia causó el pico.

## Recorrido del código fijado

Se revisó la integración y el código ACE-Step del commit `ca1e85fe9430179831e6bc6be790c332190a3866`.

1. `generate_worker.perform` importa las librerías, verifica metadatos/hashes de los checkpoints de forma secuencial y comprueba recursos. Esa verificación no instancia los tensores de los modelos.
2. El loader de DiT pide `dtype=self.dtype` (BF16 en la RTX 4060 del equipo), carga Turbo 2B en CPU y aplica INT8 weight-only solo a las capas lineales del decoder. TorchAO 0.16 transforma esas capas una por una y reemplaza cada parámetro por un AQT con datos INT8, escala y zero-point: la capa fuente BF16 y la nueva AQT coexisten durante su transformación, pero el código no crea un clon BF16 completo del DiT. La memoria pico de esa transformación en Windows no está medida. El loader también conserva `silence_latent` en CPU.
3. El loader de VAE crea el módulo y, con offload, lo conserva en CPU como FP32: `_get_vae_dtype("cpu")` devuelve `torch.float32`.
4. El loader de texto carga tokenizer y Qwen mediante `AutoModel.from_pretrained(path)` **sin `dtype`** y luego conserva el encoder en CPU como FP32. Transformers 4.57.6 fija FP32 como valor predeterminado cuando se omite `dtype`; el dtype guardado en el checkpoint no se detecta por defecto.
5. En esta configuración CUDA, `_initialize_mlx_backends` no carga otro DiT/VAE MLX: el DiT MLX está desactivado y ACE-Step desactiva su VAE MLX para CUDA. El handler mantiene referencias simultáneas a `self.model`, `self.vae`, `self.text_encoder`, el tokenizer y tensores de estado. El offload mueve módulos entre CPU y GPU; no los descarga ni borra esas referencias.
6. Durante la generación, el VAE se mueve a CUDA para la etapa de acondicionamiento y regresa a CPU al salir del contexto; `_release_system_memory` ejecuta GC y vacía caché, pero el objeto VAE sigue vivo en el handler. ACE-Step llama a `malloc_trim` solo en Linux; en Windows esa parte no corre, por lo que la memoria liberada por el allocator no necesariamente vuelve de inmediato al SO.
7. Al usar `text_encoder`, `_load_model_context` mueve el módulo al dispositivo antes de entrar al bloque `compute.text_encoder`. La transferencia densa usa el `model.to(device=..., dtype=...)` del helper upstream, en una operación combinada; el objeto CPU se sustituye parámetro por parámetro. El evento `compute.text_encoder.begin` es posterior a esa transferencia y a la rutina de liberación del contexto. Por tanto, ese evento no significa que la transferencia aún no haya empezado.

Fuentes fijadas: [orquestador](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_orchestrator.py), [carga de pesos](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_loader.py), [componentes](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_loader_components.py), [contexto de offload](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_offload_context.py), [dtype de CPU](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/memory_utils.py) y [configuración MLX](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_setup.py). TorchAO se cotejó con la [implementación 0.16.0 de `Int8WeightOnlyConfig`](https://github.com/pytorch/ao/blob/v0.16.0/torchao/quantization/quant_api.py). El comportamiento de `dtype` se cotejó con la fuente publicada de [Transformers 4.57.6](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/modeling_utils.py).

## Causa y límite de la evidencia

**Causa confirmada del desperdicio evitable:** el encoder de 0.6B se materializa y conserva en FP32 en CPU aunque su único uso de inferencia en este perfil es CUDA BF16. La copia BF16 equivale teóricamente a unos 1.12 GiB y la FP32 a unos 2.24 GiB para 600 millones de parámetros. La diferencia esperada es cerca de **1.12 GiB**. El archivo de pesos fijado mide 1,191,586,416 bytes (1.11 GiB), tamaño compatible con pesos de 16 bits; no se leyó el encabezado ni se descargó el archivo de pesos. Si el resto de la carga y las demás aplicaciones consumieran lo mismo, el mínimo de RAM disponible podría mejorar teóricamente desde 0.317 hasta aproximadamente 1.43 GiB; no es una predicción garantizada.

**No confirmado:** que ese encoder sea responsable de toda la caída de 6.973 GiB, que ocurra una copia completa adicional del encoder durante la transferencia, o que la laptop vaya a pasar el protector tras recuperar 1.12 GiB. La ruta densa de transferencia hace una conversión combinada de dispositivo y dtype, y no hay evidencia de una copia CPU completa duplicada allí. El pico de inicialización puede incluir la carga de parámetros del DiT, la coexistencia transitoria de cada peso fuente con su representación INT8, VAE, bibliotecas y otros procesos; faltan eventos por parámetro durante esa inicialización y el muestreo Windows correspondiente para cuantificarlo.

## Alternativas revisadas

- Cargar Qwen de forma perezosa solo movería su carga desde inicialización hasta el acondicionamiento. Como se necesita para prompt y letra, el encoder y sus activaciones seguirían ocupando memoria al generar; por sí sola no reduce la memoria de trabajo que observamos.
- Descargar el VAE del handler después del acondicionamiento ahorraría su copia CPU hasta la decodificación, pero obliga a volver a leer e inicializar pesos y buffers durante la misma toma. No se implementa sin medir el pico y revisar el flujo de errores.
- Cambiar la transferencia del encoder no está justificado: ACE-Step pasa dispositivo y dtype juntos en `.to(...)`; al completar, cada parámetro CPU es sustituido. No se observó un clon completo. El cambio propuesto elimina la representación FP32 persistente antes de llegar a esa transferencia.
- `gc.collect()` no elimina módulos todavía referenciados por el handler. Se conserva GC/offload existentes; no se introduce `del`, `empty_cache` adicional, cambio de paginación ni trimming de working set.

## Cambio acotado de esta rama

`TextEncoderCPUOffloadMixin` y la validación local usan la misma función `load_qwen_encoder(path, dtype)`. Esta ejecuta `AutoModel.from_pretrained(..., dtype=dtype, local_files_only=True)`; el tokenizer de producción también se carga con `local_files_only=True`. El modelo queda en CPU, sus pesos flotantes se comprueban contra el dtype de inferencia y se detiene con error claro si Transformers devuelve otro dispositivo o dtype. El perfil CUDA existente luego mueve ese mismo encoder BF16 a CUDA; no se cambia la cuantización del DiT ni se convierte el encoder a INT8. Las demás combinaciones siguen usando el loader ACE-Step original.

Se retiró `low_cpu_mem_usage=True` de la prueba y del cargador compartido porque Transformers 4.57.6 lo descarta como argumento heredado en `from_pretrained`. En esta versión el modelo se inicializa en `meta` mediante `init_empty_weights` independientemente de ese flag. El checkpoint fijado usa `model.safetensors`; el cargador consulta sus metadatos en `meta` y procesa parámetros desde el archivo por nombre. Por código, la llamada con y sin `low_cpu_mem_usage=True` sigue el mismo camino y no se espera ahorro adicional al pasar el flag. Es una revisión estática de la etiqueta 4.57.6, no una medición de memoria de la laptop; el pico real y el posible overhead siguen pendientes de la carga local.

Al cargar directamente en el dtype final, los valores BF16 del checkpoint, si se confirma ese dtype al leer sus metadatos localmente, se conservan en BF16 en vez de expandirse a FP32 y después volver a BF16. La prueba sintética verifica que esa ida y vuelta de valores BF16 es exacta. El ahorro teórico persistente es ~1.12 GiB, sin incluir allocator, buffers o overhead. El test del loader usa módulos falsos; no mide RSS.

## Riesgo VRAM durante el DiT

El nuevo loader **no aumenta la VRAM final esperada**: el flujo actual ya convierte el encoder a BF16 al usarlo en CUDA; el cambio solo conserva BF16 en su copia CPU. El informe mostró 5,749 MiB de VRAM global libre en la última muestra. El archivo BF16 del DiT mide 4.46 GiB antes de cuantización; ACE-Step cuantiza las capas lineales del decoder, no todos los tensores, y añade escalas/activaciones durante la inferencia. No se puede derivar el pico real de VRAM del tamaño del archivo ni de una sola muestra global. Por ello sigue existiendo riesgo de `CUDA out of memory` cuando el DiT de 30 s se cargue y ejecute. El límite actual del allocator y el registro de GPU siguen activos, pero no garantizan ausencia de OOM.

## Pruebas y siguiente validación

Las pruebas sintéticas del loader usan módulos falsos y verifican que producción y validador compartan la llamada offline y el dtype solicitado; un tensor BF16 pequeño confirma el roundtrip. No se usan checkpoints ACE-Step ni se genera audio. Estas pruebas no miden RSS, memoria comprometida o VRAM de la RTX 4060.

Después de revisar el cambio en esta rama, la validación debe hacerse en el Windows real antes de autorizar otra canción. Ejecutar las pruebas nuevas con el Python privado y revisar la carga del encoder y su dtype. La primera generación posterior debe seguir autorizada por separado; el protector de RAM continúa deteniendo el worker al detectar menos de 1 GiB disponible durante tres muestras consecutivas.
