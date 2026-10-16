# Etapa 1: plan exacto antes de descargar

Este documento describe lo que harán los BAT **en tu Windows**, si decides ejecutarlos y confirmas sus preguntas. Publicar estos scripts no instala nada en tu laptop. No se ha probado todavía ACE-Step, CUDA ni INT8 en ella.

## Decisión de compatibilidad

| Componente | Versión fijada | Motivo |
|---|---|---|
| Gestor privado uv | 0.9.6 | Descarga oficial por SHA256; instalación de Python sin registro ni bin global |
| CPython x64 privado | 3.12.12, build standalone 20251028 | Cumple Python >=3.11,<3.13 y evita las dependencias Triton/Flash Attention que upstream declara para 3.11 |
| torch / torchaudio | 2.10.0+cu128 | Wheels oficiales CPython 3.12 Windows x64, URLs y hashes fijados |
| torchvision | 0.25.0+cu128 | Pareja correspondiente a torch 2.10 |
| TorchCodec | 0.10.0 | Su tabla oficial lo empareja con torch 2.10; hay wheel Windows CPython 3.12 |
| TorchAO | 0.16.0 | Código construido contra torch 2.10; se usa su wheel Python y `Int8WeightOnlyConfig` versión 1, igual que este ACE-Step |
| FFmpeg privado compartido | 8.0.1, distribución GyanD | Aporta DLL dentro del rango FFmpeg 4–8 documentado por TorchCodec 0.10. No sustituye tu FFmpeg 9 estático |
| ACE-Step | commit `ca1e85fe9430179831e6bc6be790c332190a3866` | Mismo código del diagnóstico, descargado por ZIP y SHA256 |
| transformers / diffusers | 4.57.6 / 0.37.1 | Versiones del lock upstream consultado; APIs usadas por Turbo y VAE |

El conflicto **de versiones declarado** de la Etapa 0 se corrige alineando torch, TorchAO y TorchCodec en 2.10/0.16/0.10. **No significa que toda la aplicación ya esté validada en Windows.** La resolución de 57 dependencias para Windows CPython 3.12 se completó en la nube y se guardó con hashes en `instalacion/requirements-windows.lock.txt`; el instalador no volverá a escoger versiones libres.

La ruta propia de inferencia carga el código fijado desde `.runtime/source`; **no ejecuta `uv sync` ni instala el `pyproject.toml` de ACE-Step**, porque conserva el pin Windows antiguo de torch 2.7.1. Se ha preparado una selección de dependencias para inferencia por consola, basada en ese código, sin Gradio, servidor, entrenamiento, nano-vllm, Flash Attention o Triton. No se editan los manifiestos ni fuentes upstream. `uv pip check` e imports reales detectarán dependencias ausentes antes de descargar pesos.

### INT8 sin compilación

La guía general de ACE-Step activa compilación al recomendar INT8, pero en **este commit** `_configure_initialize_runtime` no la exige para CUDA y `_validate_quantization_setup` solo comprueba TorchAO. El loader llama a `_apply_dit_quantization` independientemente de `compile_model`. TorchAO 0.16 expone `Int8WeightOnlyConfig(version=1)` sin requerir `torch.compile` para ejecutarlo.

La primera ruta usará **`compile_model=False`**, sin instalar compiladores ni Triton. La validación local cuantiza una capa real en CPU BF16, la pasa a CUDA y prueba forward y traslado CPU/GPU. En el modelo completo también se cuentan las capas con almacenamiento INT8: si TorchAO falla o ACE-Step desactiva cuantización, **se detiene**. No se promete que pasar la capa pequeña baste para que todo el modelo funcione.

Corrección posterior: esa transferencia de validación se realiza mediante el helper auténtico **`_recursive_to_device` de ACE-Step**, que contiene el fallback para AQT bajo `inference_mode`; no mediante `Module.to()` directo. Se comprueban también dispositivos internos, conservación de INT8 y dos ciclos de ida y vuelta. La causa y actualización sin reinstalar se explican en [CORRECCION_INT8.md](CORRECCION_INT8.md); las versiones de esta tabla se conservan.

### Adaptación para evitar el LM 1.7B

`MAIN_MODEL_COMPONENTS` upstream incluye LM 1.7B. Usar el inicializador sin adaptación intentaría descargarlo aunque el usuario pidiera 0.6B. Nuestra subclase `SmallModelHandler` sustituye únicamente la comprobación de componentes presentes por Turbo, VAE y encoder, después de verificar los archivos por hash. No cambia el código de ACE-Step en disco. El LM 0.6B se carga expresamente cuando se selecciona ese perfil.

El código de modelo `configuration_acestep_v15.py` y `modeling_acestep_v15_turbo.py` se toma del mismo commit de ACE-Step, siguiendo la sincronización que hace el loader oficial; sus hashes están fijados. No se ejecuta una versión Python cambiante del repositorio de pesos. Los configs y pesos conservan sus revisiones originales.

## Orden de ejecución y descargas

1. **`00_VER_PLAN.bat`** muestra este resumen y lee inventario. No descarga ni instala.
2. **`03_INSTALAR.bat`** muestra el plan, comprueba Windows x64, ruta local NTFS independiente, espacio, RAM y GPU. Solo tras escribir **INSTALAR** descarga uv privado y Python 3.12.12 con los checksums del distribuidor. Crea `.venv` sin site-packages globales y sincroniza únicamente el lock de wheels con hashes. Después descarga el ZIP del código y el FFmpeg privado, verifica y extrae dentro de `.runtime`.
3. El mismo BAT ejecuta `uv pip check` y pruebas **locales**: versiones, operación BF16 CUDA, forward INT8 y offload, decodificación TorchCodec de un WAV de diagnóstico, exportación MP3 e imports de las APIs reales de ACE-Step. El tono de diagnóstico no es música generada. Un fallo interrumpe el paso; no descarga pesos ni cambia drivers/dependencias globales para arreglarlo.
4. **`04_DESCARGAR_MODELOS.bat`** requiere la validación anterior. Permite seleccionar **con LM 0.6B** o **sin LM** y pide escribir **DESCARGAR**. Solo consulta/descarga los archivos listados en `instalacion/modelos.lock.json`; primero configs y tokenizadores pequeños. Comprueba esos configs contra las clases del código fijado y sus arquitecturas antes de los pesos grandes. Si hay discrepancias, se detiene sin descargar esos pesos. Esta comprobación de configuraciones todavía no se ha ejecutado en la laptop.
5. **`05_PRIMERA_CANCION.bat`** prepara una toma de 30 segundos. Si hay menos de 8 GiB de RAM libre o 6 GiB de VRAM libre ofrece sin LM como recomendación; probar LM con ese margen requiere elegir expresamente la opción experimental. No cambia de perfil ni reintenta un OOM en silencio. **`06_PRUEBA_SIN_LM.bat`** elige directamente la alternativa sin LM.
6. La generación está configurada **offline**, verifica fuente y pesos y no descarga nada. Ofrece letra original de ejemplo o un selector de tu TXT; puedes introducir prompt y semilla sin editar código. Pide escribir **GENERAR** antes de iniciar. Se guardan WAV, MP3 cuando se exporta correctamente y registros bajo `resultados`.
7. **`07_VALIDAR_ENTORNO.bat`** repite las pruebas del entorno sin descargar ni cargar pesos. Un nuevo fallo borra solo nuestro marcador de validación anterior para no dar un éxito antiguo como vigente.

Los BAT no requieren administrador. Antes de usarlos, extrae el ZIP de GitHub a una ruta corta y estable, por ejemplo `C:\MusicStudioAILocal` o `E:\MusicStudioAILocal`, fuera de OneDrive y otras apps. No muevas la carpeta una vez creado `.venv`: el instalador rechaza un marcador con otra raíz. Si ya tienes una carpeta con ese nombre, preserva sus archivos; no reemplaces entornos, pesos o resultados por descargar un ZIP nuevo.

Excepción posterior autorizada: para la instalación ya movida a `%USERPROFILE%\OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main` existe [08_REPARAR_TRASLADO.bat y su guía](REPARAR_TRASLADO.md). Actualiza referencias y marcador tras comprobar conservación de los paquetes, sin una instalación completa. La excepción permite exclusivamente esa ubicación y mantiene todas las demás guardas. Requiere archivos locales y pausa de sincronización durante las operaciones; `.gitignore` no evita que OneDrive sincronice paquetes, cachés o datos privados.

## Selección exacta de modelos

| Repositorio | Revisión inmutable | Selección |
|---|---|---|
| ACE-Step/Ace-Step1.5 | `19671f406d603126926c1b7e2adc169acbcade22` | Turbo 2B + VAE + Qwen3-Embedding-0.6B; ≈5.90 GiB |
| ACE-Step/acestep-5Hz-lm-0.6B | `148d8ea0225bdab342ee1ae3a354275ccd60ca80` | LM 0.6B y tokenizador; ≈1.28 GiB |

El manifiesto de Etapa 0 suma **7.709.383.012 bytes (7.18 GiB)** en 27 entradas; dos archivos Python se obtienen del commit fijado en lugar de descargarse del checkpoint, de modo que el total efectivo difiere unos pocos KiB. No se descarga LM 1.7B, XL, otro motor ni modelos vocales.

Se comprueba tamaño y SHA256 LFS de archivos grandes; los pequeños usan su identidad Git blob declarada. Un archivo que ya existe y no coincide se conserva y causa error, sin sobrescribirlo automáticamente. Los archivos correctos se reutilizan; Hugging Face conserva sus descargas parciales dentro del proyecto. Los errores de TLS o hashes no se ignoran. Los pesos aún no se han descargado ni validado en esta sesión.

## Memoria y espacio: límites y riesgos

La última medición indica 16 GiB instalados y 6.61 GiB de RAM libre, aproximadamente 6.94 GiB de VRAM libre. **Recomendamos que la primera carga sea sin LM.** El perfil con LM sigue disponible como experimento explícito. Ni siquiera sin LM está garantizado que el pico de carga y cuantización quepa en la RAM; puede haber paginación, OOM o un tiempo elevado. No se recomienda comprar RAM basándose solo en estas estimaciones.

Guardas conservadoras del instalador: **60 GiB libres**, al menos **4 GiB RAM libre y 4.5 GiB VRAM libre**. La descarga posterior exige 12 GiB libres; una guarda no es un mínimo oficial ni garantía de éxito. El presupuesto previo sigue siendo aproximadamente 19–29 GiB persistentes y 27–46 GiB de pico, pendiente de medir esta combinación y sus cachés. El código fuente añade unos 9 MB de ZIP, FFmpeg unas decenas/centenas de MB según descompresión. Los temporales y caches se redirigen al disco del proyecto.

Durante generación se limita el allocator de PyTorch a lo menor entre 80% de VRAM total y VRAM libre menos 768 MiB. No controla asignaciones externas al allocator ni memoria de otras aplicaciones. El padre mide memoria periódicamente y detiene **solo el proceso hijo que inició** si detecta menos de 1 GiB de RAM libre en tres muestras consecutivas. No cierra Ollama, navegadores, ComfyUI o KickClipAI. El muestreo no impide todos los picos rápidos ni equivale a un límite de memoria del sistema.

Tu driver 617.42 y su reporte CUDA UMD 13.4 son datos observados; no prueban aún que esta build de PyTorch funcione. El runtime CUDA 12.8 llegará dentro de `.venv`; no se instala un toolkit global ni se cambia el controlador. Si falla, se conserva el error y se revisa antes de continuar.

## Aislamiento

Solo se crean/modifican directorios dentro de esta raíz: `.runtime`, `.venv`, `.cache`, `.state`, `tmp` y `resultados`. Python standalone se instala con **`--no-bin --no-registry`**. Los gestores y Python se invocan por ruta absoluta. Las variables se establecen solo en los procesos BAT/PowerShell/Python y sus hijos; no se usa `setx`, no se modifica PATH persistente ni registro. No se importa el site-packages de otros proyectos. Tu FFmpeg original se conserva y el privado se prioriza solo dentro de nuestros procesos.

La reinstalación reutiliza únicamente el entorno marcado como propio y sincroniza su lock; puede cambiar paquetes de **ese `.venv`**, nunca de otros. Las fuentes extraídas se comparan y se rechaza sobrescribir cambios manuales. No hay actualización automática de versiones/revisiones ni fallback a mirrors o CPU completa sin avisar. Las dependencias y modelos están excluidos de GitHub por `.gitignore`.

## Primera canción y resultados

Parámetros efectivos: `text2music`, voz solicitada en español (`es`), duración 30 s, batch 1, Turbo 8 pasos, `int8_weight_only` en las capas elegibles del decoder DiT, CPU/DiT offload, backend LM `pt` si se usa, sin compile/Flash Attention. INT8 no significa que todo el pipeline esté cuantizado: VAE, encoder, capas excluidas y LM conservan su precisión correspondiente. El archivo registra esa diferencia.

Semilla inicial 40601, configurable; prompt de corrido tumbado romántico con requinto, guitarra de doce cuerdas y voz masculina solista. La letra de ejemplo fue redactada para esta prueba y **no es un fragmento de “Ya No Me Toca”**. Para esa canción, elige el TXT con tu fragmento original: no se ha recibido su letra todavía. No se clona la voz de un cantante ni se usan APIs de pago.

Cada toma crea una carpeta nueva bajo `resultados` con solicitud, prompt/letra, configuración efectiva, revisiones, versiones, consola/errores, muestras de RAM y VRAM, tiempos por etapas proporcionados por el motor, tiempos totales, pico de memoria PyTorch y WAV/MP3. El WAV final se comprueba con SoundFile y se exige duración próxima a 30 s; un error de MP3 no elimina el WAV correcto. Los audios intermedios guardados por el motor pueden permanecer junto al WAV final; no se borran tomas automáticamente.

El monitor registra RSS del proceso, RAM libre, swap global y uso total de GPU: ninguna de esas métricas es consumo exclusivo exacto del modelo. El pico reservado/asignado de PyTorch se registra por separado. No se promete una velocidad en esta laptop.

Una toma técnicamente completada **no acredita** voz masculina, español correcto, cumplimiento de letra, originalidad exclusiva ni calidad musical. Esos aspectos requieren escuchar. Esta etapa permite una toma inicial; las tres variantes y comparación musical siguen pendientes de ejecución y evaluación local.

## Qué se verificó y qué sigue pendiente

Verificado en nube: fuentes oficiales/artefactos y checksums declarados, disponibilidad de wheels Windows, resolución con hashes para 57 paquetes, sintaxis Python y parser PowerShell 7.4, pruebas de integridad y protección de rutas/archivos, y publicación documental en GitHub cuando se confirme el push. El parser Linux no valida CIM, BAT ni carga de DLL de Windows; la resolución de dependencias no instala ni ejecuta sus wheels.

Pendiente en laptop: ejecución Windows PowerShell 5.1/BAT, descarga/creación del entorno, imports reales, CUDA/BF16, INT8/offload completo, DLL/codecs, configs exactos de checkpoint, integridad de pesos, carga con 16 GiB, generación y escucha. **No se declara el equipo listo para generar antes de completar esas pruebas.** Si falla cualquier etapa, comparte sus registros por chat; no los publiques en GitHub porque pueden contener rutas y letras privadas.

## Fuentes de la decisión

- [ACE-Step: manifiesto original](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/pyproject.toml) y [loader](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_loader.py).
- [TorchCodec: tabla de compatibilidad](https://github.com/pytorch/torchcodec#installing-torchcodec) y [FFmpeg compartido v0.10.0](https://github.com/pytorch/torchcodec/blob/v0.10.0/README.md).
- [TorchAO 0.16: compatibilidad](https://github.com/pytorch/ao/blob/v0.16.0/torchao/__init__.py) y [Int8WeightOnlyConfig](https://github.com/pytorch/ao/blob/v0.16.0/torchao/quantization/quant_api.py).
- [Wheels CUDA 12.8](https://download.pytorch.org/whl/cu128/torch/), [TorchCodec 0.10](https://pypi.org/project/torchcodec/0.10.0/), [TorchAO 0.16](https://pypi.org/project/torchao/0.16.0/).
- [uv 0.9.6](https://github.com/astral-sh/uv/releases/tag/0.9.6) y [metadatos fijados de Python](https://github.com/astral-sh/uv/blob/0.9.6/crates/uv-python/download-metadata.json).
- [FFmpeg 8.0.1 compartido GyanD: assets y SHA256](https://github.com/GyanD/codexffmpeg/releases/expanded_assets/8.0.1). Se conservan las licencias incluidas en el archivo descargado; no se redistribuyen sus binarios en este repositorio.
