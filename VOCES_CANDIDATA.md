# Candidata: voz cantada en el pipeline por fases

Rama de desarrollo: `codex/vocal-conditioning-audit`, basada en `a02bcd13f41e92a7bb4357af8830ecf87fb37574`. No integrada en `main`. No se ha ejecutado generación, instalado paquetes ni descargado pesos en la nube.

## Diagnóstico y límites

El usuario informa que la primera toma por fases completó 30 segundos pero resultó instrumental. No se adjuntaron el WAV, `solicitud.json`, `configuracion.json` ni la consola de esa ejecución. **La causa de esa toma no está confirmada.** No sería correcto afirmar que se perdió la letra, que falta obligatoriamente un LM o que INT8 eliminó la voz.

Sí se confirmó una diferencia de configuración: nuestro worker heredaba `GenerationParams.shift=1.0`. La documentación de la misma revisión oficial recomienda **3.0 para Turbo**, y el muestreador implementa efectivamente ese cambio. La candidata aplica 3.0 únicamente al perfil por fases. No es una garantía de canto ni demuestra que shift 1 causara el instrumental.

No encontramos en la ruta inspeccionada una eliminación incondicional de letras al usar `LM=False`. Las comprobaciones nuevas convierten posibles pérdidas silenciosas de condicionamiento en errores identificables antes de difundir. La elección de una letra breve y un caption centrado en canto es una condición de la prueba controlada, no la única corrección.

## Ruta exacta de la letra

Código oficial fijado: `ca1e85fe9430179831e6bc6be790c332190a3866`. Los enlaces siguientes apuntan a esa revisión, no al main cambiante de ACE-Step.

| Punto | Comportamiento comprobado por lectura del código |
| --- | --- |
| `instalacion/Generar.ps1` → supervisor → worker | Lee UTF-8, guarda `lyrics` y `prompt` en la solicitud local y los entrega a `GenerationParams`. El nuevo BAT usa constantes compartidas y muestra ambos textos antes de confirmar. |
| [inference.py, generate_music](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/inference.py#L510) | Inicializa `dit_input_lyrics=params.lyrics`. Sin LM conserva caption, letra e idioma y los pasa al handler. `audio_codes` queda vacío; no se usa una referencia musical. |
| [generate_music_request.py](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/generate_music_request.py) y [batch_prep.py](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/batch_prep.py) | Repiten letra e idioma para el lote. La ausencia de códigos no convierte la solicitud en instrumental. |
| [prompt_utils.py](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/prompt_utils.py#L27) | Formatea `# Languages\nes\n\n# Lyric\n{letra}<|endoftext|>`. Metadatos pueden sustituir caption/idioma; ahora se comprueba que no suceda en este perfil. No debemos añadir manualmente esa cabecera a un TXT. |
| [conditioning_text.py](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/conditioning_text.py) | Tokeniza caption completo hasta 256 tokens y letra hasta 2048. Los límites de caracteres del formulario no garantizan ausencia de truncamiento. La candidata compara los tokens efectivos con la tokenización completa y rechaza truncamiento. |
| [conditioning_embed.py](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/conditioning_embed.py#L71) | Caption: forward de Qwen. Letra: `text_encoder.embed_tokens(lyric_token_ids)`. Ambos se calculan durante la misma fase Qwen; la letra no se transforma en voz con un TTS aparte. |
| [service_generate_execute.py](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/service_generate_execute.py) | Envía `lyric_hidden_states` y `lyric_attention_mask` a `prepare_condition` y `generate_audio`. La candidata verifica identidad del tensor, máscara completa, ausencia de cover/hints LM y los parámetros de muestreo en esta frontera. |
| [modeling_acestep_v15_turbo.py](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/models/turbo/modeling_acestep_v15_turbo.py#L1515) | `AceStepConditionEncoder` procesa embeddings de letra en `lyric_encoder`, los combina con timbre y caption y entrega condicionamiento para cross-attention del decoder. `generate_audio` genera latentes condicionados. |
| Decodificación y exportación | El VAE decodifica esos latentes como mezcla musical; el worker exporta el tensor completo estéreo. No selecciona una pista instrumental ni elimina una pista vocal. El VAE no recibe la letra ni añade una voz después. |

El ciclo de componentes continúa siendo VAE → VAE → Qwen → DiT → VAE. Los embeddings son resultados de cálculo con almacenamiento propio; retirar Qwen no significa borrarlos. La candidata guarda únicamente listas pequeñas de tokens, hashes, dimensiones y escalares, sin retener módulos ni copiar pesos.

## Qué permiten y qué no permiten los parámetros

- **Sin LM puede cantar.** La [guía oficial](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/Tutorial.md#choosing-the-planner-lm-models) presenta No LM como opción soportada. El LM 5 Hz es un planificador opcional de códigos semánticos/metadatos; no es el sintetizador de voz. Sin él perdemos esa planificación adicional, no la rama de letras del DiT. No se puede cuantificar aquí la tasa de obediencia vocal en español con y sin LM.
- `instrumental=False` ya estaba configurado. Aunque la documentación lo presenta como opción, el cuerpo de `generate_music` de esta revisión no usa ese booleano para activar un generador vocal. No equivale a forzar canto; son importantes la letra efectiva y su condicionamiento.
- `vocal_language="es"` es válido y llega a la cabecera de letra. Es un condicionamiento, no un alineador que obligue a pronunciar cada palabra. Masculino se expresa mediante caption; no hay un parámetro de speaker masculino determinista en esta ruta.
- `task_type="text2music"`, sin source/reference/codes, es apropiado. `cover`/`extract`/`lego` no son atajos para obtener voz nueva sin audio de entrada adecuado.
- Turbo destila CFG. La [guía de inferencia](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/INFERENCE.md#L354) explica que CFG se fuerza a 1 y **shift debe fijarse explícitamente a 3**. Subir guidance o activar ADG no reforzaría la letra en este modelo. La candidata usa 8 pasos, ODE, shift 3, sin timesteps personalizados, mismo número de evaluaciones.
- `use_cot_lyrics` está reservado/no consumido en esta API; habilitarlo no arreglaría la letra. No se activa CoT ni LM silenciosamente.
- Las etiquetas `[Verse]` y `[Chorus]` son compatibles con la [guía de letras](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/Tutorial.md#about-lyrics-the-temporal-script). Una introducción/solo largo puede ocupar gran parte de 30 segundos. La prueba usa cuatro líneas originales, sin etiquetas instrumentales, manteniendo voz masculina, español, requinto y acompañamiento de corrido romántico. No se garantiza el género exacto ni se clona un cantante.

## Cambios y costo de memoria

`musicstudio/vocal_conditioning.py` concentra parámetros, fixture de prueba y controles. `generate_worker.py` lo incorpora exclusivamente al handler por fases y registra `condicionamiento-vocal.json`. `generate.py` valida solicitudes vocales por fases antes de iniciar el worker. `Generar.ps1` admite el modo controlado y `PrepararPruebaVoz.ps1` obtiene su configuración del mismo módulo Python. `phase_checks.py` incluye las nuevas pruebas antes de permitir escribir GENERAR.

No se modifica `phased_pipeline.py`, cuantización, loader BF16 de Qwen, transferencia INT8, pesos, lock de dependencias, `memory_trace.py`, actualizador ni umbrales. Continúan el preflight de 4 GiB RAM/4.5 GiB VRAM, margen por componente, límite del allocator y corte de RAM **<1 GiB durante tres muestras**.

Las comprobaciones añaden tokenización CPU de textos pequeños y reducciones sobre embeddings existentes (finitud/no vacío). No hacen forward adicional de Qwen, clones de pesos, dequantización ni retención del encoder. Una máscara de finitud temporal ocupa un byte por elemento; con 2048 tokens × 1024 dimensiones sería aproximadamente 2 MiB, y la prueba breve utiliza mucho menos. Ese cálculo es teórico; pico RSS/private bytes/VRAM real pendiente de medir. Shift cambia valores de timesteps, no dimensiones ni cantidad de pasos, por lo que no añade pesos ni otra fase. No se promete idéntico pico medido.

**¿Añadir LM 0.6B?** No es necesario para habilitar técnicamente la rama vocal y no está justificado hacerlo antes de esta prueba. El manifiesto existente fija 1,325,804,024 bytes (1.235 GiB) de safetensors y 1.278 GiB de archivos LM totales: son tamaños de archivos comprobados en el lock, no consumo RAM/VRAM medido. Pesos BF16 tendrían una magnitud similar, más tokenizer, buffers, activaciones y KV cache. En el perfil antiguo el objeto LM permanecería vivo junto a los otros modelos; en fases aún está prohibido. No conocemos su pico real en esta laptop. Una fase LM separada, con liberación antes de Qwen/DiT y medición de KV/cache, requeriría otro diseño y autorización; no se incorpora aquí. Modelos mayores, otro DiT o un TTS añadirían costo y alterarían el alcance sin evidencia suficiente.

## Una sola prueba local controlada

El actualizador existente sigue deliberadamente **main**. Esta rama no se instala al ejecutar `ACTUALIZAR_MUSICSTUDIO.bat`; no se cambia su fuente ni sus controles. Primero revisar/aprobar esta candidata. Tras una integración autorizada posterior en main, actualizar normalmente: se incorporarán solo archivos de código, sin reinstalar ni reemplazar `.venv`, `.runtime`, checkpoints o datos. No descargar un ZIP encima de la instalación.

Cuando la candidata revisada esté incorporada en la instalación local:

1. Mantener la ubicación actual y OneDrive pausado; cerrar aplicaciones manualmente para liberar RAM. No cambiar paginación ni configuración global.
2. Hacer doble clic en **`12_PRUEBA_VOZ_POR_FASES.bat`**. Usa el Python privado y el mutex existente. Ejecuta primero validaciones nativas/sintéticas sin pesos musicales, exige cero fallos/omisiones y comprueba integridad/versiones, INT8 y codecs. Si falla, conservar registros y detenerse.
3. Leer el prompt y las cuatro líneas originales que muestra. Semilla fija 40601, duración 30 s, lote 1, 8 pasos, shift 3, sin LM, INT8, Qwen BF16 y ciclo por fases. No lee ni sustituye `config/letra_ejemplo.txt` o un TXT personal. Escribir **GENERAR** solo para autorizar esa única toma. Otro texto cancela. El script no reintenta ni encadena variantes.
4. Al terminar, abrir la nueva carpeta de la toma dentro de `resultados` (la consola indica su nombre) y escuchar **`primera_cancion.wav` completo**. Escuchar específicamente si existe canto, si suena masculino, si es español inteligible y cuáles de las cuatro líneas se reconocen, indicando sus tiempos aproximados. Si hay voz pero canta otra letra, la prueba de obediencia tampoco está superada.
5. Conservar `solicitud.json`, `configuracion.json`, `condicionamiento-vocal.json`, `ciclo-componentes.json`, `motor-estado.json`, `resultado.json`, `consola.log` y `memoria.csv` de esa toma (y errores/interrupción si existen). El JSON de condicionamiento dice `routing_checked` solo si se verificó la ruta hasta el sampler; **no certifica voz audible**. Si se interrumpe antes, puede decir `incomplete` o no existir aún.
6. No repetir automáticamente si sigue instrumental. Comparar registros con la primera toma y aportar ambos WAV/registros para distinguir condicionamiento correcto con desobediencia musical de un error de integración. La siguiente decisión podrá ser una fase LM medida o una prueba aislada de otra variable, con autorización nueva.

El texto original de esta prueba es un fixture diagnóstico, no un fragmento atribuido a «Ya No Me Toca». No se han usado sus grabaciones ni pesos adicionales.

## Verificación realizada y pendiente

En Linux/nube: 135 pruebas descubiertas, **115 aprobadas, 20 omitidas, cero fallos**. Incluyen 11 regresiones vocales nuevas: API oficial sin LM, conservación de tokens, idioma/metadatos, truncamiento, embeddings inválidos, sustitución del tensor/máscara, cover/códigos LM inesperados y schedule incorrecto; además de suite previa de reparación, actualización, fases e integridad. Las copias de código usadas por las pruebas en `fuentes/vocal_*.py` son byte por byte las del lock oficial y no se importan en producción.

Las pruebas ejecutan las clases/métodos reales de las fuentes fijadas con tensores/tokenizer sintéticos; no sustituyen una inferencia real. La prueba nativa adicional usa `torch.nn.Embedding` BF16 diminuto en CUDA, comprueba entrega al sampler y supervivencia del resultado tras liberar el módulo. **No se ejecutó aquí por falta de PyTorch/CUDA**; se exige en el BAT local. Las omisiones restantes corresponden a Windows y al stack nativo de pruebas ya existente. No se atribuye a Linux la validación de handles/telemetría Windows.

También se simuló la actualización de esta candidata con el actualizador existente y un remoto Git temporal: se aplicaron sus 16 archivos de código/documentación, conservando ocho archivos testigo de entorno, runtime, caché, estado, checkpoints, resultados, respaldo anterior y archivo personal. La primera preparación del remoto temporal fue rechazada porque no anunciaba soporte de descarga parcial; al configurarlo como los remotos de pruebas existentes, pasó. No se relajó ninguna comprobación del actualizador ni se cambió su código. Esto verifica el plan/transacción en Linux, no las particularidades de OneDrive en Windows.

Pendiente en Windows: sintaxis/ejecución real del nuevo BAT/PowerShell, tokenización con el tokenizer Qwen instalado, tensores de embeddings reales, memoria y muestreo Turbo con INT8, y escucha del WAV. Los tests no acreditan aún canto masculino, pronunciación, fidelidad a la letra ni calidad de corrido tumbado. Cambiar shift, caption y longitud de letra a la vez produce una candidata práctica, **no un experimento que identifique cuál variable causaba el instrumental**.
