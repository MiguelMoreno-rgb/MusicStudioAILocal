# Music Studio AI Local

Diagnóstico inicial para una futura prueba local de ACE-Step 1.5 Turbo en Windows con NVIDIA RTX 4060 Laptop de 8 GB. **Este repositorio contiene únicamente la Etapa 0: no instala ACE-Step, no descarga modelos y no genera música.** No contiene una página web.

Configuración candidata: DiT 2B Turbo, LM musical 0.6B, backend PyTorch (`pt`), INT8, offload CPU/DiT y una generación por lote. Su funcionamiento en tu equipo todavía requiere las comprobaciones descritas en el diagnóstico.

## Descargar todo como ZIP desde GitHub

1. Abre [MiguelMoreno-rgb/MusicStudioAILocal](https://github.com/MiguelMoreno-rgb/MusicStudioAILocal).
2. Comprueba que está seleccionada la rama **main**.
3. Pulsa el botón verde **Code** y después **Download ZIP**.
4. En Windows, haz clic derecho sobre el ZIP y selecciona **Extraer todo**. No ejecutes los BAT desde dentro del ZIP.

También puedes usar [este enlace directo al ZIP de main](https://github.com/MiguelMoreno-rgb/MusicStudioAILocal/archive/refs/heads/main.zip). Si GitHub pide iniciar sesión para acceder al repositorio, usa tu cuenta autorizada.

Extrae la carpeta en un disco local con permiso de escritura, preferiblemente donde quieras instalar el proyecto después. No uses carpetas de KickClipAI, Ollama ni ComfyUI. No necesitas Git ni editar código.

## Ejecutar el diagnóstico en Windows

1. Abre la carpeta extraída y entra en **diagnostico**.
2. Haz doble clic en **01_DIAGNOSTICO.bat**. Funciona sin Internet y no necesita Python instalado ni permisos de administrador. Lee RAM instalada/disponible, GPU, VRAM libre, controlador NVIDIA, discos, versiones de Python y FFmpeg. Espera a que termine.
3. Haz doble clic en **02_VERIFICAR_MODELOS.bat** con conexión a Internet. Consulta exclusivamente metadatos públicos de Hugging Face: revisiones, archivos, tamaños y hashes declarados. **No descarga pesos ni instala programas.**
4. Los informes se guardan en **diagnostico/resultados**, en subcarpetas con fecha. Para continuar, comparte `RESUMEN.txt`, `diagnostico.json` y `manifest-modelos.json`. Si hay un error, conserva también el mensaje.

Las rutas de herramientas en los informes pueden incluir tu nombre de usuario; revísalas antes de compartirlos. Los resultados están excluidos de Git por `.gitignore`: no hace falta subirlos al repositorio.

Los BAT deben permanecer junto a sus archivos PS1 auxiliares. Usan PowerShell 5.1 o posterior, incluido normalmente en Windows 10/11. Si una política de empresa impide ejecutarlos, conserva el error y no cambies la política global. `ExecutionPolicy Bypass` se aplica únicamente al proceso lanzado; no modifica la configuración persistente de Windows.

## Qué se sabe y qué falta

- Python **3.10.9 no cumple** el requisito del commit consultado: se necesita Python estable 3.11 o 3.12 de 64 bits, que se prepararía de forma aislada después de aprobar el diagnóstico.
- La guía oficial contempla el perfil de 6–8 GB con LM 0.6B, PyTorch, INT8 y offload; eso no garantiza que la prueba funcione sin medir RAM/VRAM y validar dependencias.
- El stack publicado para Windows presenta un desajuste entre PyTorch 2.7.1 y TorchCodec 0.10, además de riesgos de compatibilidad de TorchAO 0.16. Hay que resolverlo antes de instalar.
- Reserva provisional propuesta: **60 GiB libres** en el disco del proyecto. Es una estimación prudente pendiente de los tamaños exactos de la selección de pesos.
- La nube donde se preparó el diagnóstico no pudo consultar Hugging Face por una restricción del proxy. El segundo BAT permite comprobarlo desde tu laptop; un fallo no demuestra que falte un token.

Lee [DIAGNOSTICO_Y_PLAN.md](DIAGNOSTICO_Y_PLAN.md) para las fuentes oficiales, incompatibilidades, presupuesto y plan completo. [LEEME_PRIMERO.md](LEEME_PRIMERO.md) amplía el alcance de los scripts. Los scripts se revisaron como texto en Linux; **todavía no se han ejecutado ni validado en Windows**.

## Protección de tus otras aplicaciones

Los scripts solo leen inventario y escriben sus informes en su propia carpeta. No modifican KickClipAI, Ollama, ComfyUI, dependencias globales, PATH persistente, controladores ni registro. No cierran aplicaciones ni eliminan archivos para liberar memoria.

La futura instalación usará una carpeta, Python, entorno virtual, modelos y cachés exclusivos del proyecto. **La Etapa 1 requiere tu aprobación explícita después de revisar el diagnóstico.** La publicación de estos archivos no autoriza instalaciones.

## Contenido

| Ruta | Función |
|---|---|
| `diagnostico/01_DIAGNOSTICO.bat` | Ejecutar inventario local sin conexión |
| `diagnostico/Diagnostico.ps1` | Script auxiliar de inventario |
| `diagnostico/02_VERIFICAR_MODELOS.bat` | Consultar metadatos oficiales por HTTPS |
| `diagnostico/Verificar-Modelos.ps1` | Script auxiliar de revisiones/tamaños |
| `DIAGNOSTICO_Y_PLAN.md` | Hallazgos, estimaciones y etapas futuras |
| `fuentes/` | Copias documentales del código/manifiestos consultados; no ejecutarlas |
| `PROCEDENCIA.json` | URLs originales, commit y límites de verificación |
| `SHA256SUMS.txt` | Integridad de los archivos publicados; no contiene hashes de pesos descargados |

Las copias de fuentes de terceros conservan sus licencias en `fuentes/LICENCIAS/`. Su presencia sirve para auditar el diagnóstico; no constituye una instalación de esos proyectos.
