# Music Studio AI Local — diagnóstico documental y plan técnico

> Antecedente de Etapa 0. La selección actual de dependencias y los BAT de instalación se describen en [PLAN_ETAPA1.md](PLAN_ETAPA1.md). Los problemas aquí identificados no deben interpretarse como el estado de validación de la nueva ruta, que aún requiere ejecución local.

Consulta: 15 de octubre de 2026, America/Matamoros. Especificación principal: `Informe_IA_Musical_Local_RTX4060.pdf`, especialmente páginas 3, 4 y 9. Se aplican las etapas pedidas por el usuario: la generación queda en la etapa 2 y toda instalación requiere aprobar el diagnóstico. Las propuestas de experimentos posteriores del PDF no autorizan instalarlos ahora.

**Dictamen: candidato adecuado para una prueba controlada, pero todavía NO aprobado para instalar.** La configuración de 8 GB tiene respaldo documental; faltan inventario real, revisión/tamaños de pesos y resolver la coherencia del stack Windows. Esta sesión corre en Linux en la nube, sin acceso a la laptop. No se presentan sus recursos como si fueran los del usuario.

## 1. Requisitos contrastados con el repositorio exacto

Repositorio oficial: https://github.com/ace-step/ACE-Step-1.5

Commit candidato observado con `git ls-remote`:
`ca1e85fe9430179831e6bc6be790c332190a3866`.
Versión declarada en `pyproject.toml`: **1.5.0**. El nombre 1.5 por sí solo no fija una implementación; los archivos consultados se descargaron como texto desde ese commit. Se conserva el lockfile para auditoría; no se ejecutó `uv sync`.

| Componente | Evidencia oficial / conclusión |
|---|---|
| Python | `>=3.11,<3.13`; la guía recomienda 3.11–3.12 estables. **3.10.9 no cumple.** Se conservará sin modificar. |
| Windows NVIDIA | PyTorch `2.7.1+cu128`, torchaudio `2.7.1+cu128`, torchvision `0.22.1+cu128` fijados en el manifiesto. Son los pins publicados, todavía no una combinación certificada para toda la aplicación. |
| Perfil 6–8 GB | Tabla oficial: LM 0.6B, backend `pt`, offload CPU + DiT e INT8. Elegiremos batch 1 aunque la tabla permita más. |
| Modelo | `acestep-v15-turbo`, DiT 2B, 8 pasos. XL/4B queda excluido. |
| Componentes adicionales | VAE y `Qwen3-Embedding-0.6B` son necesarios. Este último es un codificador de texto diferente del LM musical `acestep-5Hz-lm-0.6B`; no son intercambiables. |
| Windows nativo | Tiene ruta oficial y paquete portable. No se descargó el paquete. WSL2 no forma parte del plan inicial. |
| RAM | La guía revisada no fija un mínimo uniforme para este modo. 32 GiB es una recomendación prudente de planificación; con 16 GiB hace falta evaluar memoria disponible/paginación. |
| GPU real | RTX 4060 Laptop 8 GB, según el usuario; faltan controlador y VRAM libre medidos. La tabla de tiers usa memoria total, que no equivale a memoria libre. Forzaremos `pt` y offload para evitar que el límite de 8 GB elija un perfil más agresivo. |
| CUDA | La build de PyTorch lleva runtime CUDA. No se propone instalar globalmente un toolkit ni actualizar el controlador en automático. La suficiencia del controlador se revisará con `nvidia-smi` y, tras aprobación, con una operación CUDA real. |
| Español / voz masculina | Son controles de la prueba. No hay demostración de pronunciación mexicana, género regional ni identidad vocal fija en esta laptop. |

## 2. Incompatibilidades y riesgos concretos

**A. Python 3.10.9.** No sirve para el commit actual. Propuesta: Python estable 3.11 x64 administrado en una carpeta exclusiva del proyecto y `.venv` propio. La versión de parche exacta se fijará junto con el instalador. No se registrará como Python predeterminado ni se añadirá al PATH global.

**B. TorchCodec y PyTorch no están alineados en Windows.** El `uv.lock` selecciona TorchCodec **0.10.0**, mientras Windows fija Torch **2.7.1**. La tabla actual oficial de TorchCodec empareja 0.10 con Torch 2.10 y las series 0.3–0.5 con Torch 2.7. El README de la etiqueta v0.10.0 contiene una tabla atrasada; se conserva también la tabla actual para no confundirlas. No ejecutar a ciegas el instalador oficial y darlo por compatible. Que un resolvedor acepte las dependencias no verifica ABI ni carga de DLL.

**C. TorchAO 0.16 y cuantización.** El manifiesto requiere `torchao>=0.16,<0.17`; el lock fija 0.16.0. El código oficial de TorchAO v0.16.0 declara construcción contra Torch 2.10.0 y puede omitir extensiones C++ ante versiones incompatibles. Esto es un riesgo adicional con Torch 2.7.1, no una prueba de que toda cuantización Python vaya a fallar. El orquestador de ACE-Step puede desactivar cuantización si falla su importación: será obligatorio comprobar el modo efectivo `int8_weight_only` y detenerse si desaparece; no aceptar un arranque que silenciosamente use más memoria.

**D. Compilación/Triton/Flash Attention.** La documentación activa compilación junto con INT8. Aunque usemos backend LM `pt`, el paquete declara `nano-vllm`; con Python 3.11 Windows, este arrastra `triton-windows` y un wheel comunitario de Flash Attention fijado a Torch 2.7.1/cu128. Es otra razón para no sustituir Torch por 2.10 de forma aislada ni prometer una instalación mínima solo por elegir `pt`. La compilación puede requerir herramientas adicionales en Windows; se comprobará después de aprobar, sin instalar compiladores globales en automático.

**E. FFmpeg estático frente a compartido.** `ffmpeg.exe -version` puede funcionar y exportar MP3, pero TorchCodec necesita bibliotecas compartidas compatibles. La etiqueta TorchCodec v0.10.0 cita FFmpeg 4–8. La presencia de DLL junto al EXE es un indicio, no un test de carga. No se reemplazará el FFmpeg de otros proyectos.

**Resolución propuesta antes de descargar pesos:** buscar una revisión oficial con stack Windows coherente. Si no existe, evaluar un conjunto de versiones compatible mediante configuración/constraints exclusivos de este proyecto, comprobando sus APIs y licencias. No bajar TorchAO o TorchCodec a ojo, no editar el lock upstream ni saltarse validaciones. Un cambio necesario al código se presentaría por separado. La instalación se mantendrá bloqueada si no se obtiene una combinación verificable. El portable oficial también necesita revisión de versiones y procedencia; no se usa como forma de ocultar el problema.

## 3. Pesos, disponibilidad y reproducción

El código y los documentos oficiales estaban disponibles. Las consultas al endpoint de GitHub API recibieron 403; la referencia Git y los archivos raw sí se pudieron consultar. En Hugging Face el proxy de esta nube rechazó el túnel CONNECT con **403 antes de llegar al servicio**. Esto no prueba que los modelos estén retirados, sean privados ni que falte un token del usuario.

| Repositorio de pesos | Selección propuesta | Revisión / bytes exactos |
|---|---|---|
| `ACE-Step/Ace-Step1.5` | Solo `acestep-v15-turbo/**`, `vae/**`, `Qwen3-Embedding-0.6B/**` y metadatos/licencias necesarios | Pendientes de consulta a Hugging Face |
| `ACE-Step/acestep-5Hz-lm-0.6B` | LM 0.6B y sus archivos de configuración/tokenizador | Pendientes de consulta a Hugging Face |

El descargador predeterminado incluye **LM 1.7B**. No usaremos descarga completa, `--all` ni primer arranque con descargas automáticas sin controlar. No se añadirán XL, base, SFT, otros LM, otros motores ni referencias de voz para esta prueba.

`02_VERIFICAR_MODELOS.bat` obtiene el SHA actual de cada repositorio, vuelve a consultar sus metadatos mediante ese SHA inmutable y calcula el tamaño de los archivos seleccionados. Si falta un tamaño o componente, lo marca incompleto. Registra hashes LFS declarados cuando existen, sin descargarlos ni verificarlos todavía. No requiere token; si aparece una restricción real, se diagnosticará sin pedir secretos en chat.

El manifiesto resultante será candidato a revisión. Antes de instalar se contrastarán configuraciones de los checkpoints con el código fijado, licencias actuales y archivos seleccionados; antes de usar los pesos descargados se verificarán tamaños y hashes disponibles. **Todavía no está validada la pareja exacta código/pesos.** No se inventan revisiones ni se supone que cualquier `main` futuro es equivalente.

## 4. Espacio de disco

La guía habla de aproximadamente **10 GB de modelos centrales**, incluyendo su selección predeterminada; no es una medida exacta de nuestra selección 0.6B. El INT8 de ejecución no significa que todos los archivos descargados pesen un byte por parámetro. Los siguientes valores son **estimaciones en GiB** (1 GiB = 2^30 bytes), redondeadas al alza para planificar, pendientes del manifiesto:

| Partida | GiB estimados |
|---|---:|
| Python privado + `.venv` + PyTorch/CUDA y dependencias instaladas | 8–12 |
| Turbo 2B + VAE + codificador + LM 0.6B | 10–12 |
| Código, metadatos y utilidades locales | 1–2 |
| Cachés retenidas de ejecución/compilación | 3–7 |
| Tres salidas, registros y manifiestos | 0.1–0.3 |
| **Estado persistente estimado** | **22.1–33.3** |
| Temporales y paquetes comprimidos durante instalación/descarga | **+8–17** |
| **Pico estimado, sin duplicar una biblioteca completa de pesos** | **30.1–50.3** |

**Propuesta: disponer de 60 GiB libres en el disco del proyecto antes de instalar.** Es una reserva prudente, no un mínimo oficial ni una medición; amplía el margen de 35–50 GB del PDF mientras se resuelven dependencias. Si la versión final requiere más, recalcularemos antes de descargar. No duplicar caché HF y checkpoints; si se necesita duplicación por limitaciones de enlaces de Windows, añadir aproximadamente otra copia de los pesos al pico y revisar la reserva.

Todos los caches y temporales del futuro instalador se dirigirán a carpetas del proyecto cuando sea compatible. Si Windows o el instalador de un runtime utiliza el disco del sistema, revisaremos también su espacio libre; reservar provisionalmente 5–10 GiB allí si es otro disco. No se cambiará el archivo de paginación ni se contará la RAM compartida como VRAM dedicada.

Para 3 tomas de 45 s, estéreo, 48 kHz: WAV PCM16 ≈24.7 MiB en total; PCM24 ≈37.1 MiB; float32 ≈49.4 MiB. MP3 a 192 kb/s añade ≈3.1 MiB. Los 0.1–0.3 GiB permiten registros y metadatos; no incluyen colecciones de intentos ilimitadas ni tensores de diagnóstico grandes.

## 5. Aislamiento y protección de otras aplicaciones

Ahora: los scripts solo leen inventario y escriben resultados en su propia carpeta. No se instaló software ni se descargaron pesos; no se ejecutó ni alteró KickClipAI, Ollama o ComfyUI. La revisión confirma ausencia de comandos para modificar esas aplicaciones; no equivale a una auditoría completa de sus carpetas ni a una medición de sus estados en tu laptop.

Después de aprobar, la raíz independiente propuesta será una ruta corta fuera de esos proyectos, por ejemplo `D:\MusicStudioAILocal` (según tus discos). Contendrá `.runtime`, `.venv`, `vendor`, `checkpoints`, `.cache`, `tmp`, `config`, `outputs` y `logs`. En el diagnóstico inicial el repositorio del usuario estaba vacío. Por petición posterior del usuario se preparó la publicación de los archivos de Etapa 0 en MusicStudioAILocal; esto no autoriza instalar ni generar música. En la etapa 1 usaremos ese mismo repositorio independiente sin sobrescribir trabajo existente ni crear duplicados innecesarios.

El futuro instalador invocará el ejecutable privado y el gestor privado por ruta absoluta; sin `pip` global, `pip --user`, activación de Conda ajeno ni reutilización de `.venv`. Usará `PYTHONNOUSERSITE` y rutas de caché/temporales solo en el proceso BAT (`setlocal`), desactivará actualizaciones automáticas y evitará heredar `PYTHONPATH/PYTHONHOME` de otro proyecto. No usará `setx`, cambiará PATH persistente, registro, servicios, driver o asociaciones de archivos. Una actualización de controlador que resulte necesaria será una acción externa explícita; no se hará silenciosamente.

No cerraremos ni borraremos procesos para liberar VRAM. Si Ollama, ComfyUI, juegos o navegadores están consumiéndola, el diagnóstico lo reflejará y podrás decidir qué cerrar. Como son el mismo hardware, aislamiento de carpetas/dependencias no significa aislamiento de RAM/VRAM.

## 6. Plan de etapas y condiciones para avanzar

**Etapa 0, ahora.** Ejecutar los dos BAT; revisar RAM instalada/disponible, VRAM libre, controlador, espacio de cada disco, Python y FFmpeg. Completar revisiones/tamaños/licencias de pesos y resolver el stack Windows documentalmente. El diagnóstico no necesita cuentas, secretos, administrador, GPU activa en esta nube ni paquetes nuevos. Recibir el informe no autoriza instalar: hace falta la aprobación posterior del usuario.

**Etapa 1, después de aprobar.** Preparar repositorio independiente y un instalador BAT no interactivo en tareas repetibles, con opciones simples para el principiante. Reutilizar la descarga verificada; fallar ante archivos incompletos o versiones distintas. Primero validar el entorno sin modelos: versiones efectivas, imports, `pip check` o equivalente, operación CUDA con un tensor pequeño, BF16/precisión necesaria, INT8 real en una capa de prueba y codecs. Esto es necesario: una GPU visible en `nvidia-smi` no basta. Solo después descargar los componentes seleccionados por SHA y registrar hashes, sin fallback de repositorio/revisión automático.

Validar carga del modelo y controles efectivos: `int8_weight_only`, CPU offload y DiT offload activos, backend LM `pt`, lote 1, VAE adaptativo. Comprobar el camino de compilación antes de gastar tiempo con generaciones; si requiere herramientas globales, parar y presentar alternativa aislada. Detener ante fallback silencioso que elimine INT8, OOM, paginación problemática o excepciones. El modo sin LM será una alternativa explícita si hace falta; no reemplaza sin avisar la configuración seleccionada. No probar entrenamiento, LoRA, clonación de voz ni canciones largas en esta etapa.

**Etapa 2, prueba musical.** Generar secuencialmente 3 variantes de **35 segundos** (dentro de 30–45), con semillas propuestas **40601, 40602 y 40603**, 8 pasos Turbo, batch 1. Un BAT permitirá escoger duración, prompt y un archivo de letra mediante preguntas/selector de archivos; no requerirá editar Python. No arrancará web ni enlace público. WAV será la salida principal; MP3 se exportará solo si FFmpeg tiene un codificador usable. Conservar WAV si falla MP3 y registrar el error sin marcar toda la generación como pérdida.

Prompt inicial propuesto, editable: «Corrido tumbado romántico, requinto y guitarra de doce cuerdas, bajo cálido, arreglo íntimo, voz masculina solista original, fraseo emotivo, letra en español mexicano». Tempo, compás y tonalidad se ajustarán al fragmento. No usar nombres de cantantes como promesa de identidad ni referencias de audio no aportadas. El informe no contiene la letra de **“Ya No Me Toca”**: el usuario debe aportar el fragmento original elegido antes de generar. No se inventa que un texto nuevo pertenezca a esa canción.

Por cada ejecución: commit, revisiones/hashes, versiones, Windows/controlador/GPU, configuración solicitada y efectiva, prompt/letra, semilla, duración pedida/real, lote/pasos/precisión, estado LM/offload/compilación, tiempos de carga, LM, difusión, VAE y total, RAM/paginación observada, VRAM reservada/asignada del proceso y muestras globales de `nvidia-smi`, archivos/tamaños y error completo. La muestra de VRAM total y el pico de PyTorch miden cosas distintas: se conservarán ambos; un muestreo periódico puede perder picos breves. Registrar también fallos, no solo la mejor salida.

Criterio técnico: 3 ejecuciones terminadas sin OOM, WAV decodificable y duración medida compatible con lo pedido. Criterio musical separado: escuchar pronunciación, cumplimiento de letra, voz masculina, arreglo regional, mezcla y cierre. No afirmar calidad, originalidad exclusiva ni consistencia vocal persistente sin evaluación. La voz fija entre canciones y canciones de varios minutos quedan fuera de esta primera prueba.

## 7. Pendientes que impiden cerrar el diagnóstico

1. Resultados reales de los BAT en Windows; esta nube no puede medir la laptop.
2. SHA, lista de archivos y tamaños oficiales de ambos repositorios de pesos; no alcanzables desde la red actual.
3. Validar las configuraciones de esos checkpoints contra el commit seleccionado y sus licencias actuales.
4. Elegir una combinación coherente Windows/PyTorch/TorchAO/TorchCodec/compilación antes de instalar. No se declara resuelto por haber leído documentación.
5. Aprobación del diagnóstico antes de cualquier instalación; fragmento de letra antes de etapa 2.

Los scripts Windows se revisaron como texto, **no se ejecutaron ni probaron en Windows**. No se ha medido calidad musical ni rendimiento. No se guardó ni ejecutó un script de instalación en la configuración de la nube ni se publicó un entorno ejecutable. La publicación documental de Etapa 0 en GitHub, solicitada posteriormente, no cambia estos límites.

## Fuentes primarias

- [Instalación, fijada al commit](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/INSTALL.md).
- [Dependencias por plataforma](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/pyproject.toml) y [lockfile](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/uv.lock).
- [Tabla GPU](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/GPU_COMPATIBILITY.md), [detección de memoria total](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/gpu_config.py).
- [Descargador y contenido del paquete predeterminado](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/model_downloader.py).
- [Dependencias de nano-vllm](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/third_parts/nano-vllm/pyproject.toml).
- [TorchCodec: tabla oficial actual](https://github.com/pytorch/torchcodec#installing-torchcodec) y [README v0.10.0, FFmpeg compartido](https://github.com/pytorch/torchcodec/blob/v0.10.0/README.md).
- [TorchAO v0.16.0: compatibilidad de extensiones](https://github.com/pytorch/ao/blob/v0.16.0/torchao/__init__.py).
- [Pesos centrales](https://huggingface.co/ACE-Step/Ace-Step1.5) y [LM 0.6B](https://huggingface.co/ACE-Step/acestep-5Hz-lm-0.6B): referencias oficiales del repositorio, no verificación directa de disponibilidad en esta sesión.

En `fuentes` se conservan las copias consultadas. No son una instalación ni deben ejecutarse. `SHA256SUMS.txt` permite detectar cambios del paquete; no es una firma digital del autor ni un hash de pesos aún no descargados.
