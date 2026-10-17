# Candidata integral de memoria: Turbo 2B por fases

Esta rama prepara una prueba de 30 segundos, sin LM, batch 1, ocho pasos, voz cantada en español y los checkpoints ya instalados. **Todavía no demuestra que la canción quepa o se genere en la laptop.** La nube no tiene Windows, CUDA, PyTorch ni pesos musicales. No se cambia el stack ni se descarga ningún modelo. El perfil existente de 05/06 permanece disponible; la candidata se selecciona expresamente con **11_PRUEBA_POR_FASES.bat**.

## Diagnóstico y evidencia disponible

| Prueba | Evidencia aportada por el usuario | Lo que permite concluir |
|---|---|---|
| Primera | Durante transferencia INT8, disponible 6.1 → 0.262 GiB; 33.46 s | Presión de RAM; el protector actuó. Se eliminó el intento especulativo de `Module.to(AQT)` antes del traslado manual. |
| Segunda | WinError 5 durante publicación del marcador | Fallo de E/S, distinto del consumo de modelos. La telemetría append-only fue aceptada después en Windows. No se vuelve a modificar. |
| Tercera | Disponible 7.29 → 0.317 GiB; RSS 8,283,291,648 B; privado 15,117,254,656 B; evento `compute.text_encoder/begin` | Qwen FP32 en CPU era almacenamiento evitable. BF16 resolvió esa representación, pero no la arquitectura completa. |
| Cuarta | Disponible 7.92 → ~0.57 GiB; privado ~13.68 GiB; RSS ~7.67 GiB; CUDA allocated ~1.65 GiB; libre global GPU ~5.84 GiB | Qwen BF16 terminó y comenzó el DiT. La presión de RAM persiste con telemetría correcta; la última capa completada no identifica por sí sola al causante. |

Se revisaron el código y los datos anteriores, más los archivos de diagnóstico de hardware disponibles. No se recibió `memoria.csv` ni el JSONL completo de la cuarta ejecución en esta sesión. No se afirma haber reproducido su curva en Windows.

**Causa confirmada en el código:** el handler mantiene referencias a DiT, VAE y Qwen simultáneamente. El contexto oficial devuelve el componente entero a CPU después de usarlo; no lo descarga. El VAE vuelve a FP32 al regresar a CPU; Qwen BF16 sigue ocupando su almacenamiento después de producir embeddings. Al terminar el DiT, el flujo anterior vuelve a crear sus pesos CPU justo antes de necesitar VAE para decodificar. Estos ciclos de vida son innecesarios para una toma desechable.

Se confirmó además una referencia circular en los envoltorios locales de trazas: el modelo contenía una función que retenía un método ligado al propio modelo. Se sustituyó por `WeakMethod`, eliminando también la referencia `__wrapped__` de `functools.wraps`. La prueba conserva el wrapper y verifica que el objeto modelo se destruya incluso sin GC. No se atribuye a este ciclo la caída completa observada.

**Hipótesis pendientes:** memoria temporal de cuantización, páginas de Safetensors mapeadas, reservas del allocator CPU, DLL/CUDA y recursos del controlador Windows pueden contribuir al pico. Se necesitan muestras reales antes/después de cada liberación para separarlos. No se atribuye el fallo a OneDrive ni a retención de Windows sin evidencia.

RAM disponible es un contador del sistema. RSS es el working set residente del worker; private bytes es compromiso privado, no RAM física ocupada. La diferencia entre ambos no mide una fuga. CUDA allocated cuenta tensores PyTorch; CUDA reserved incluye caché de ese allocator; `nvidia-smi` mide memoria global y otros recursos/procesos. Ni una muestra de VRAM libre ni el último tensor completado demuestran dónde ocurrió el pico.

## Investigación y fuentes

Se consultó el código oficial fijado en `ca1e85fe9430179831e6bc6be790c332190a3866`; los archivos descargados para investigación se contrastaron con los hashes del lock de código cuando correspondía. Solo se consultaron fuentes y metadatos, no pesos.

1. [Guía oficial GPU del commit fijado](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/GPU_COMPATIBILITY.md): recomienda INT8 y offload para 6–8 GB VRAM. Su tabla no certifica este Windows con 16 GiB RAM. La descripción de offload de la guía no sustituye al flujo real: [el contexto fijado](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_offload_context.py) rodea una ejecución completa del modelo, no cada bloque del decoder.
2. [Loader DiT](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_loader.py): carga BF16 en CPU y cuantiza solamente lineales del decoder, excluyendo tokenizer/detokenizer. [Loader de componentes](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_loader_components.py) y [política de dtype](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/memory_utils.py): VAE CPU FP32, CUDA BF16 en esta GPU.
3. [Preparación de latentes](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/conditioning_target.py), [embeddings](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/conditioning_embed.py), [DiT](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/service_generate_execute.py) y [decodificación](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/generate_music_decode.py): establecen los límites de fase utilizados. Las entradas/salidas son tensores de acondicionamiento y latentes; bajo inference mode no requieren conservar los módulos productores.
4. [Modelo Turbo](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/models/turbo/modeling_acestep_v15_turbo.py): `prepare_condition` utiliza encoder y audio tokenizer/detokenizer, y `generate_audio` vuelve a preparar acondicionamiento. No se eliminan esos submódulos ni se separa precipitadamente el decoder del resto del DiT.
5. [Transformers 4.57.6](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/modeling_utils.py): `low_cpu_mem_usage` se descarta como argumento heredado. El loader ya crea parámetros en `meta` y materializa Safetensors por parámetro. `dtype` sí controla la representación. Se conserva el loader Qwen compartido con la validación 10, con `local_files_only=True`.
6. [TorchAO 0.16, Int8WeightOnlyConfig](https://github.com/pytorch/ao/blob/v0.16.0/torchao/quantization/quant_api.py): la versión 1 usa AQT y cuantización simétrica por canal con PlainLayout. Está deprecada para versiones futuras, pero es la representación ya validada en este stack; no se migra de formato en esta candidata. [Integración HF](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/quantizers/quantizer_torchao.py) permite cuantizar durante la carga, pero `create_quantized_param` llama a `get_input_embeddings()`. La clase ACE fijada carece de ese método y de la estructura que el [fallback HF](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/modeling_utils.py) reconoce. Por ello no se adopta sin una adaptación y pruebas adicionales.
7. [PyTorch 2.10, gestión CUDA](https://github.com/pytorch/pytorch/blob/v2.10.0/docs/source/notes/cuda.rst): `empty_cache()` libera caché CUDA sin uso, no tensores aún referenciados ni pesos CPU. El [allocator CPU](https://github.com/pytorch/pytorch/blob/v2.10.0/c10/core/CPUAllocator.cpp) y el [helper ACE](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_memory_basic.py) no prueban que el SO devuelva inmediatamente toda la RAM: `malloc_trim` del helper solo se aplica en Linux.

Se intentó consultar la búsqueda de incidencias públicas de GitHub para casos de 16 GB/Windows/offload; el acceso devolvió HTTP 403 también al reintentar fuera del sandbox. No se citan como verificadas discusiones que no pudieron leerse. Las conclusiones anteriores se fundamentan en los fuentes etiquetados y la evidencia real aportada.

## Estrategia elegida

`PhasedPipelineMixin` actúa únicamente en el perfil optativo `phased_v1`. Inicializa config, tokenizer y silence latent local, y registra reservas explícitas sin pesos. La inicialización se informa como **preparada, pendiente de validar INT8/CUDA**, nunca como modelo ya cargado. La verificación completa de hashes de código, configs y checkpoints continúa antes de ello.

```mermaid
flowchart LR
  A[Metadatos y tokenizer] --> B[VAE preparación y liberación]
  B --> C[Qwen BF16 y liberación]
  C --> D[Carga DiT / INT8 / CUDA / 8 pasos]
  D --> E[Liberar DiT]
  E --> F[Recargar VAE / tiled decode / liberar]
  F --> G[Exportar WAV y MP3]
```

El motor fijado abre dos contextos VAE durante preparación, incluso si el audio es silencio. Se respetan ambos límites: el VAE puede cargarse tres veces en total, desde el mismo checkpoint verificado, incluyendo la decodificación final. Es un coste deliberado de lectura/latencia para evitar retener pesos. No se sustituye el procesamiento oficial de silencio por un atajo.

Al entrar en cada contexto se comprueban RAM disponible, orden y exclusión mutua. Los pesos se materializan en CPU; Qwen y DiT usan BF16, VAE se carga directamente en el dtype BF16 que el flujo CUDA original ya usaba al calcular. El DiT conserva el filtro oficial de cuantización del decoder y fija explícitamente `Int8WeightOnlyConfig(version=1)`. Se verifica INT8 real antes de trasladarlo; la transferencia AQT existente valida los datos INT8, escalas, dispositivos y dtype. Si cualquier comprobación falla, no se permite compute ni un fallback silencioso.

Se estima el payload real de tensores antes de trasladar. Debe caber en el menor de VRAM global libre menos 768 MiB y presupuesto restante del allocator del worker. Se exige además reserva de 1.5 GiB para DiT o 0.5 GiB para Qwen/VAE. Estas reservas son preflights conservadores, **no mediciones del pico de activaciones**. El presupuesto original de hasta el 80% de VRAM permanece; el monitor independiente conserva exactamente menos de 1 GiB disponible en tres muestras consecutivas.

Al salir se sincroniza CUDA, se retira la referencia del handler y se ejecuta el GC/limpieza ya existente. **No se llama a traslado CPU al retirar componentes.** Permanecen embeddings, latentes y metadatos necesarios. Los wrappers no retienen el módulo. Los errores originales se conservan aunque también falle la limpieza; se registra ese segundo error y el handler queda inutilizable. No se permiten fases simultáneas/anidadas, LM, LoRA ni reintentos dentro de la candidata.

Los loaders se ejecutan con `inference_mode(False)` y `no_grad`, reproduciendo la creación normal de parámetros de la inicialización previa aunque ACE entre desde su hilo de inferencia. `torch.random.fork_rng` conserva los estados RNG de CPU y CUDA alrededor de la carga, para que inicializar módulos tardíamente no consuma el flujo aleatorio del sampler. El cálculo conserva el inference mode del motor. La semilla, texto, letra, sampler, duración, pasos y formatos no se reducen.

## Alternativas descartadas por ahora

| Alternativa | Decisión |
|---|---|
| Mantener todo en GPU | La muestra de 5.84 GiB libres no prueba el pico posterior. Se evita sumar pesos de componentes ya terminados. |
| Offload de cada bloque | Puede reducir VRAM, pero conserva pesos CPU y aumenta transferencias. Añade hooks AQT y estados que aún no están validados en Windows. Reservado si falla VRAM después de separar fases. |
| Cuantizar durante la carga HF | Hay una incompatibilidad concreta del contrato `get_input_embeddings` con esta clase. No se introduce una segunda ruta TorchAO sin verificarla. |
| INT4, FP8 o cuantizar también el encoder interno | Cambia cobertura/precisión o kernels y exigiría nuevas validaciones de calidad y Windows. No se adopta. |
| Subprocesos por componente | Liberaría recursos al salir cada proceso, pero requiere cambiar la supervisión y asegurar que ningún descendiente sobreviva al protector. Se mantiene el único worker protegido existente. |
| Trimming del working set o más paginación | No elimina referencias vivas y podría maquillar contadores. No se usa. |

## Presupuesto de memoria y límites

Los tamaños de archivo proceden del manifiesto fijado: DiT BF16 4,787,825,604 B (~4.46 GiB), Qwen 1,191,586,416 B (~1.11 GiB), VAE 337,431,388 B (~0.314 GiB). **Archivo no equivale a RAM física ni a VRAM.** El VAE puede tener otro dtype serializado; su payload BF16 real se registrará localmente.

| Fase | Estimación de pesos y máximo de planificación |
|---|---|
| Registro | Sin pesos grandes; tokenizer, config y silence latent. El coste de imports/DLL/CUDA se mide aparte. |
| Qwen | ~1.11 GiB de parámetros BF16 más temporales/activaciones. DiT/VAE no residentes. Se confirma dtype local, no se infiere solo del tamaño. |
| Carga/cuantización DiT CPU | Hasta ~4.46 GiB BF16 antes de cuantizar. La representación INT8 final queda entre ~2.23 y 4.46 GiB más escalas, porque no se cuantiza todo. Si persistieran simultáneamente todas las fuentes BF16 y todos los datos INT8, la suma aproximada sería 6.69 GiB más escalas y scratch; es un escenario conservador de almacenamiento, no un pico RSS observado ni un límite del allocator. |
| DiT CUDA | Payload real medido + reserva de 1.5 GiB debe caber en el presupuesto de hasta ~6.4 GiB del allocator. Recursos del controlador y procesos ajenos quedan fuera de ese límite. El pico real puede superar la reserva; OOM sigue siendo posible. |
| VAE decode | Solo VAE, latentes, audio y temporales tiled; DiT/Qwen ya retirados. El motor conserva su fallback oficial CPU ante presión de VRAM. |

La RAM máxima se estima como **base residente del proceso + pesos vivos de la fase + conversiones/scratch + recursos del controlador**. No se dispone de una medida de esa base ni de los temporales de carga para esta candidata; no existe un máximo numérico fiable de RSS. Antes del DiT se evita al menos la residencia teórica de ~1.11 GiB de Qwen, más todo el VAE que el perfil anterior conservaba. También se evita crear el DiT CPU al finalizar difusión. El ahorro físico depende de referencias, allocator y SO; no se promete que los 0.57 GiB libres de la cuarta prueba se conviertan en una cifra determinada.

No hay evidencia suficiente para declarar inviable Turbo 2B con 16 GiB: el intento anterior no aislaba las fases. Tampoco se ha demostrado viable. Si la fase DiT sola vuelve a agotar RAM, se revisarán el pico de su carga/cuantización y la retención posterior a Qwen. Si agota VRAM se revisará la reserva de activaciones y offload por bloques antes de cambiar pesos. Una alternativa que use un modelo más pequeño o otro motor requerirá revisar calidad/licencias y autorizar su descarga; no se instala ni recomienda como solución demostrada sin pruebas.

Los 2–4 minutos quedan pendientes: aumentan activaciones y audio, y no se extrapolan de los pesos ni de una toma de 30 s. La candidata rechaza duraciones diferentes.

## Validación y aceptación local

Las pruebas puramente sintéticas verifican inicialización sin pesos, un solo componente vivo, liberación por referencias débiles, orden, concurrencia, fallos de carga/traslado/cálculo/limpieza, dtypes, preflight RAM/VRAM y rechazo de perfiles incompatibles. Las nativas añadidas usan pesos aleatorios diminutos: Qwen3 HF cargado BF16/CPU y forward CUDA; Oobleck Diffusers BF16/CPU y encode/decode CUDA; cuantización ACE real, transferencia AQT, forward y liberación por fases con lineales pequeñas. No usan checkpoints musicales.

En nube Linux, la suite completa ejecutó **123 casos: 104 aprobados y 19 omitidos**, sin fallos. Entre los nuevos casos, pasaron 12 pruebas de ciclo de vida/seguridad y una conversión real WAV estéreo → MP3 → WAV con FFmpeg Linux 7.1.5, manteniendo canales y duración del seno de 0.1 s. Las tres pruebas nuevas con Torch/Transformers/Diffusers/CUDA se omitieron porque esas bibliotecas y GPU no están disponibles. Las otras 16 omisiones corresponden a pruebas anteriores que requieren Windows o el stack nativo. Se comprobaron además sintaxis Python, hashes, rutas permitidas del actualizador y CRLF/ASCII de BAT/PowerShell.

El FFmpeg Linux no acredita las DLL ni el FFmpeg privado Windows 8.0.1. La existencia de pruebas CUDA tampoco acredita su funcionamiento: deberán aprobarse realmente en la laptop. No se ejecutó PowerShell/Windows ni se generó música aquí. La telemetría nativa aprobada por el usuario permanece sin modificaciones.

Además se ejecutó una simulación del actualizador con el árbol real de `main` en `6282379` y el árbol de esta candidata como dos versiones en un remoto Git temporal. Se aplicaron los 13 archivos cambiados, se verificó el respaldo transaccional y permanecieron intactos siete archivos centinela en entorno, runtime, caché, estado, checkpoints, resultados y una ruta personal. Esta prueba acredita el plan y la aplicación con Git local/Linux; no acredita la E/S de Windows ni ejecutó código nuevo o instaladores.

`11_PRUEBA_POR_FASES.bat` ejecuta primero el check nativo existente de versiones, pertenencia del Python privado, integridad de source y FFmpeg, CUDA/BF16, AQT y WAV/MP3 de un seno diagnóstico. Después exige que las pruebas nuevas pasen con **cero omisiones**. Guarda `resultados\*-validacion-fases-sintetica\informe.json` y `consola.log`. Ese proceso termina antes de preparar la generación, liberando sus recursos. No modifica el marcador de 07 ni lo sustituye.

Luego solicita letra, prompt y semilla con el flujo existente y exige escribir **GENERAR**. Hasta esa confirmación no carga checkpoints musicales. La única toma posterior usa `memory_profile: phased_v1`, 30 s, ocho pasos y batch 1. El supervisor y mutex existentes siguen activos. No se repite una toma automáticamente.

Para revisar el resultado real, conservar la carpeta de la toma: `configuracion.json`, `ciclo-componentes.json`, `memoria.csv`, `memoria-eventos.jsonl`, `monitor.json`, `consola.log` y cualquier error/interrupción. Las nuevas operaciones `initialize.phase_registry`, `phase.load.*`, `phase.model.quantize`, `phase.vram_preflight` y `phase.release` usan el escritor de telemetría existente. Incluyen métricas antes/después y se mantienen los eventos por tensor de transferencia. `ciclo-componentes.json` registra payload, mayor parámetro denso, cargas/liberaciones, tiempos e INT8/CUDA efectivo; si Windows mata el worker antes de escribirlo, los eventos ya emitidos siguen siendo la evidencia.

El éxito exige validación CUDA/INT8 real, completar el decode, equilibrar cargas/liberaciones, devolver exactamente una toma finita, duración esperada y exportar WAV. MP3 mantiene el manejo de error explícito existente. Voz, pronunciación, letra, estilo y calidad se evalúan por escucha; ningún test sintético los acredita.

## Cómo incorporar y ejecutar sin ZIP ni reinstalación

Esta candidata se publica **solo en una rama de desarrollo**. `ACTUALIZAR_MUSICSTUDIO.bat` consulta exclusivamente `main`; no puede instalar esta rama mientras siga sin integrar. No se cambia ese mecanismo para forzar una prueba.

Recomendación: revisar esta candidata y sus límites, y autorizar después su integración en `main` para validación. Tras esa integración:

1. Mantener el proyecto en su ruta definitiva de OneDrive Documentos, disponible localmente y con sincronización pausada.
2. Ejecutar **ACTUALIZAR_MUSICSTUDIO.bat**, revisar los archivos y confirmar **ACTUALIZAR**. Solo se actualiza código con respaldo; entornos, modelos, cachés, resultados y paquetes permanecen protegidos. Si hay cambios locales, detenerse y revisar el conflicto.
3. Ejecutar **11_PRUEBA_POR_FASES.bat**. No hace falta editar código ni ejecutar comandos sueltos. Si falla un check, conservar los informes y no continuar.
4. Confirmar **GENERAR** únicamente cuando se quiera ejecutar la toma local. El BAT no se ejecuta automáticamente al actualizar.
5. Revisar registros y escuchar el WAV antes de cualquier repetición o ampliación de duración.

La recomendación de integración habilita una **prueba controlada**, no certifica que la laptop ya pueda generar música. No se modifica `main` sin autorización.
