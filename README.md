# Music Studio AI Local

Diagnóstico y scripts de instalación aislada para ACE-Step 1.5 Turbo en Windows con NVIDIA RTX 4060 Laptop de 8 GB. **Lee [PLAN_ETAPA1.md](PLAN_ETAPA1.md) antes de iniciar descargas.** La publicación de scripts no significa que ya se hayan instalado ni probado en tu laptop. No hay página web ni APIs de pago.

Configuración: DiT 2B Turbo, LM musical 0.6B opcional, backend PyTorch (`pt`), INT8 en el decoder elegible, offload CPU/DiT, batch 1 y 30 segundos. Con 16 GiB de RAM y 6.61 GiB libres, recomendamos probar primero **sin LM**, sin prometer que la carga quepa. El BAT ofrece esa opción expresamente.

**Si ya instalaste los 57 paquetes y falló `model.to("cuda")` con `aten._has_compatible_shallow_copy_type`:** aplica la [corrección INT8 sin reinstalar](CORRECCION_INT8.md). Solo requiere copiar `musicstudio/validate.py` y el nuevo `musicstudio/int8_offload.py`, y volver a ejecutar `07_VALIDAR_ENTORNO.bat`. Mantén la descarga de modelos pendiente hasta revisar esa validación.

**Si moviste la instalación de Downloads a OneDrive\Documentos y aparece `No Python at ...Downloads...`:** aplica [REPARAR_TRASLADO.md](REPARAR_TRASLADO.md) y **08_REPARAR_TRASLADO.bat**. Usa el runtime privado que ya existe, comprueba paquetes antes de escribir y repara referencias con backup; no reinstala los 57 paquetes. No extraigas el ZIP encima de `.venv` o `.runtime`: copia únicamente los archivos indicados en esa guía.

**Si `08_REPARAR_TRASLADO.bat` se detuvo en «script no reconocido: numba»:** sigue [estos pasos de actualización de solo dos archivos](REPARAR_TRASLADO.md#si-la-reparación-se-detuvo-en-numba). No reinstales paquetes ni sustituyas `.venv` o `.runtime`.

**Si aparece WinError 5 en `memoria-ultima-etapa.json`:** aplica la [publicación append-only para Windows](CORRECCION_WINDOWS_APPEND.md) mediante el actualizador. Elimina los reemplazos del marcador y añade **09_VALIDAR_TELEMETRIA_WINDOWS.bat** para aceptación nativa sin modelos ni música. La aceptación en tu Windows sigue pendiente hasta ejecutar ese BAT.

**Si la primera generación fue detenida por RAM baja:** lee [CORRECCION_RAM.md](CORRECCION_RAM.md). La actualización elimina el intento redundante de transferencia INT8 y añade trazas de memoria por operación; no acredita aún que la toma quepa en tu laptop. Actualiza el código y revisemos antes de ejecutar otra generación.

**Antes de autorizar otra generación tras la tercera prueba:** ejecuta [10_VALIDAR_QWEN_BF16.bat](10_VALIDAR_QWEN_BF16.bat) y sigue [VALIDAR_QWEN_BF16.md](VALIDAR_QWEN_BF16.md). Esta comprobación carga solo Qwen local en CPU/BF16, verifica dtype/dispositivo, mide RAM/RSS/memoria privada y no inicia el DiT, VAE ni generación.

## Actualizar tu instalación existente sin ZIP

Si ya tienes Python privado y los 57 paquetes, configura una sola vez la [actualización directa desde GitHub](ACTUALIZACIONES.md#primera-vez-un-comando-de-powershell): un comando de PowerShell descarga el configurador de un commit concreto, verifica SHA256 y pide revisión/confirmación antes de ejecutarlo. Funciona aunque la reparación de `.venv` siga pendiente.

Después solo ejecuta **ACTUALIZAR_MUSICSTUDIO.bat**. Muestra los cambios de `main`, bloquea modificaciones locales y procesos activos, y crea respaldo recuperable. Mantiene intactos entorno, modelos y archivos personales. La corrección pendiente de `numba` llega por este flujo; luego ejecuta **08_REPARAR_TRASLADO.bat** por separado. No reinstales paquetes ni descargues modelos para actualizar código.

## Descargar todo como ZIP desde GitHub

1. Abre [MiguelMoreno-rgb/MusicStudioAILocal](https://github.com/MiguelMoreno-rgb/MusicStudioAILocal).
2. Comprueba que está seleccionada la rama **main**.
3. Pulsa el botón verde **Code** y después **Download ZIP**.
4. En Windows, haz clic derecho sobre el ZIP y selecciona **Extraer todo**. No ejecutes los BAT desde dentro del ZIP.

También puedes usar [este enlace directo al ZIP de main](https://github.com/MiguelMoreno-rgb/MusicStudioAILocal/archive/refs/heads/main.zip). Si GitHub pide iniciar sesión para acceder al repositorio, usa tu cuenta autorizada.

Extrae la carpeta en un disco local con permiso de escritura, preferiblemente donde quieras instalar el proyecto después. No uses carpetas de KickClipAI, Ollama ni ComfyUI. No necesitas Git ni editar código.

## Etapa 1: instalar y probar, sin editar código

Usa una ruta corta y estable, por ejemplo `C:\MusicStudioAILocal` o `E:\MusicStudioAILocal`. No muevas un `.venv` creado ni sobrescribas entornos/resultados con un ZIP. Para el traslado ya realizado se autoriza específicamente `%USERPROFILE%\OneDrive\Documentos\MusicStudioAILocal\MusicStudioAILocal-main`, mediante la [reparación controlada](REPARAR_TRASLADO.md); no es un permiso general para otras carpetas de OneDrive.

1. Ejecuta **00_VER_PLAN.bat**. Solo muestra el plan y comprueba recursos; no descarga nada.
2. Lee [PLAN_ETAPA1.md](PLAN_ETAPA1.md). Si estás de acuerdo, ejecuta **03_INSTALAR.bat** y escribe **INSTALAR** cuando lo solicite. Prepara Python 3.12.12 privado, `.venv`, dependencias con hashes, código fijado y FFmpeg compartido privado. No descarga pesos. Necesita Internet y una reserva de 60 GiB libres.
3. Espera a las pruebas locales de CUDA, INT8/offload y codecs. **Si aparece un error, detente y comparte el registro; no ejecutes el paso siguiente.** No cambies tus versiones globales para intentar solucionarlo.
4. Ejecuta **04_DESCARGAR_MODELOS.bat**, elige con LM 0.6B o sin LM y escribe **DESCARGAR**. Solo baja los componentes fijados: aproximadamente 7.18 GiB con LM o 5.90 GiB sin él. Primero comprueba configs; si no coinciden, no descarga los pesos grandes.
5. Ejecuta **05_PRIMERA_CANCION.bat** o directamente **06_PRUEBA_SIN_LM.bat**. Elige la letra de ejemplo o tu TXT mediante un selector de archivos; puedes introducir otro prompt y semilla. Escribe **GENERAR** para iniciar una toma de 30 segundos, sin descargas ni servidor web.
6. Abre **resultados**: cada toma conserva WAV, MP3 cuando se exporta, configuración, métricas y errores. Escucha antes de evaluar voz, letra y calidad. Los informes y audios no se suben a GitHub.

**07_VALIDAR_ENTORNO.bat** repite las pruebas locales sin descargar modelos. El ejemplo de letra es original de prueba y **no pertenece a “Ya No Me Toca”**; para esa canción debes aportar tu fragmento en un archivo TXT UTF-8. Puedes escribir o pegar letra en Bloc de notas, sin editar código Python.

No ejecutes los BAT oficiales de Gradio ni `uv sync` dentro del código descargado: esta ruta usa un stack Windows corregido y fijado para inferencia por consola. No instala compiladores, Triton, Flash Attention, entrenamiento ni otra web. Las bibliotecas solo se instalan dentro de `.venv`.

## Ejecutar el diagnóstico en Windows

1. Abre la carpeta extraída y entra en **diagnostico**.
2. Haz doble clic en **01_DIAGNOSTICO.bat**. Funciona sin Internet y no necesita Python instalado ni permisos de administrador. Lee RAM instalada/disponible, GPU, VRAM libre, controlador NVIDIA, discos, versiones de Python y FFmpeg. Espera a que termine.
3. Haz doble clic en **02_VERIFICAR_MODELOS.bat** con conexión a Internet. Consulta exclusivamente metadatos públicos de Hugging Face: revisiones, archivos, tamaños y hashes declarados. **No descarga pesos ni instala programas.**
4. Los informes se guardan en **diagnostico/resultados**, en subcarpetas con fecha. Para continuar, comparte `RESUMEN.txt`, `diagnostico.json` y `manifest-modelos.json`. Si hay un error, conserva también el mensaje.

Las rutas de herramientas en los informes pueden incluir tu nombre de usuario; revísalas antes de compartirlos. Los resultados están excluidos de Git por `.gitignore`: no hace falta subirlos al repositorio.

Los BAT deben permanecer junto a sus archivos PS1 auxiliares. Usan PowerShell 5.1 o posterior, incluido normalmente en Windows 10/11. Si una política de empresa impide ejecutarlos, conserva el error y no cambies la política global. `ExecutionPolicy Bypass` se aplica únicamente al proceso lanzado; no modifica la configuración persistente de Windows.

## Diagnóstico inicial y límites

- Python **3.10.9 no cumple** el requisito del commit consultado: se necesita Python estable 3.11 o 3.12 de 64 bits, que se prepararía de forma aislada después de aprobar el diagnóstico.
- La guía oficial contempla el perfil de 6–8 GB con LM 0.6B, PyTorch, INT8 y offload; eso no garantiza que la prueba funcione sin medir RAM/VRAM y validar dependencias.
- El desajuste upstream PyTorch 2.7.1/TorchCodec 0.10 se corrige en nuestra ruta fijando PyTorch 2.10, TorchCodec 0.10 y TorchAO 0.16; sus wheels se resolvieron para Python 3.12 Windows. La compatibilidad en ejecución debe pasar las pruebas locales antes de descargar pesos.
- Reserva provisional propuesta: **60 GiB libres** en el disco del proyecto. Es una estimación prudente pendiente de los tamaños exactos de la selección de pesos.
- La consulta de metadatos desde la laptop confirmó los repositorios/revisiones y una selección de 7.18 GiB. La nube no pudo consultar esos checkpoints directamente por una restricción del proxy. Los configs exactos se contrastarán localmente antes de los pesos; los hashes se verificarán después de descargar. No se han cargado modelos en esta sesión.

Lee [DIAGNOSTICO_Y_PLAN.md](DIAGNOSTICO_Y_PLAN.md) y [LEEME_PRIMERO.md](LEEME_PRIMERO.md) como antecedentes de Etapa 0; [PLAN_ETAPA1.md](PLAN_ETAPA1.md) contiene la decisión actual. Los BAT y la instalación **todavía no se han ejecutado ni validado en Windows**. En la nube se comprobó sintaxis con Python y el parser PowerShell, integridad y resolución de dependencias; eso no valida CUDA ni DLL de tu laptop.

## Protección de tus otras aplicaciones

Los scripts de diagnóstico solo leen inventario. Los BAT de Etapa 1, tras confirmación local, crean el runtime y entorno dentro de esta carpeta. No modifican KickClipAI, Ollama, ComfyUI, dependencias globales, PATH persistente, controladores ni registro. No cierran otras aplicaciones ni eliminan archivos para liberar memoria.

Python se obtiene con uv privado y opciones `--no-bin --no-registry`, sin sustituir Python 3.10.9. La instalación usa rutas absolutas y variables de proceso. La carpeta contiene modelos, caches y resultados ignorados por Git. El monitor puede detener únicamente su propio hijo de generación ante presión severa de RAM.

## Contenido

| Ruta | Función |
|---|---|
| `00_VER_PLAN.bat` | Leer plan y recursos, sin descargas |
| `03_INSTALAR.bat` | Entorno exclusivo y pruebas locales, sin pesos |
| `04_DESCARGAR_MODELOS.bat` | Descarga selectiva por revisión y hashes |
| `05_PRIMERA_CANCION.bat` | Una toma de 30 s, prompt/letra/semilla configurables |
| `06_PRUEBA_SIN_LM.bat` | Alternativa explícita sin LM |
| `07_VALIDAR_ENTORNO.bat` | Repetir CUDA, INT8 y codecs sin descargar |
| `08_REPARAR_TRASLADO.bat` | Reparar el traslado específico a Documentos conservando paquetes |
| `09_VALIDAR_TELEMETRIA_WINDOWS.bat` | Pruebas nativas de E/S, sin modelos ni música |
| `10_VALIDAR_QWEN_BF16.bat` | Carga aislada de Qwen local en CPU/BF16, sin música |
| `REPARAR_TRASLADO.md` | Archivos a copiar, respaldo y riesgos de OneDrive |
| `PLAN_ETAPA1.md` | Versiones exactas, riesgos y orden de instalación |
| `instalacion/` y `musicstudio/` | Scripts y locks propios de integración |
| `config/` | Prompt y letra de ejemplo; no contienen tu letra privada |
| `diagnostico/01_DIAGNOSTICO.bat` | Ejecutar inventario local sin conexión |
| `diagnostico/Diagnostico.ps1` | Script auxiliar de inventario |
| `diagnostico/02_VERIFICAR_MODELOS.bat` | Consultar metadatos oficiales por HTTPS |
| `diagnostico/Verificar-Modelos.ps1` | Script auxiliar de revisiones/tamaños |
| `DIAGNOSTICO_Y_PLAN.md` | Hallazgos, estimaciones y etapas futuras |
| `fuentes/` | Copias documentales del código/manifiestos consultados; no ejecutarlas |
| `PROCEDENCIA.json` | URLs originales, commit y límites de verificación |
| `SHA256SUMS.txt` | Integridad de los archivos publicados; no contiene hashes de pesos descargados |

Las copias de fuentes de terceros conservan sus licencias en `fuentes/LICENCIAS/`. Su presencia sirve para auditar el diagnóstico; no constituye una instalación de esos proyectos.
