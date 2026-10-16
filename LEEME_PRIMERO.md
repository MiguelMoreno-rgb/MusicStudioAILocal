# Music Studio AI Local — etapa 0

Este paquete prepara el diagnóstico. No instala Python, PyTorch, ACE-Step, controladores ni modelos. No genera música y no contiene una página web.

1. Extrae TODO el ZIP en una carpeta local con permiso de escritura. Preferiblemente en el disco donde quieras instalar el proyecto más adelante; por ejemplo `D:\MusicStudioAI_Diagnostico`. No lo extraigas dentro de KickClipAI, Ollama o ComfyUI. No ejecutes los BAT directamente dentro del ZIP.
2. Abre `diagnostico` y haz doble clic en **01_DIAGNOSTICO.bat**. No requiere administrador ni Python. Espera a que termine; pulsa una tecla para cerrar.
3. Haz doble clic en **02_VERIFICAR_MODELOS.bat** con conexión a Internet. Consulta JSON públicos de Hugging Face; no descarga pesos. Puede tardar hasta unos minutos si la red no responde.
4. En `diagnostico\resultados` encontrarás carpetas con fecha. Comparte `RESUMEN.txt`, `diagnostico.json` y `manifest-modelos.json`. Si falla la segunda consulta, conserva también el mensaje de error. Las rutas pueden contener tu nombre de usuario: puedes ocultarlo antes de compartirlas.

Los BAT y los PS1 deben permanecer juntos. No tienes que editar código ni ejecutar comandos manuales. El diagnóstico solo observa; no cierra Ollama ni otras aplicaciones. Una primera medición con tu uso habitual permite ver el consumo real; si la memoria libre es baja, podemos comparar después con otra medición tras cerrar aplicaciones que tú elijas.

El primer BAT funciona sin Internet. Si FFmpeg no aparece, eso puede significar que no está en PATH, aunque exista en otra carpeta: no reinstales ni cambies PATH por tu cuenta. Lo revisaremos con el resultado.

Los BAT usan `ExecutionPolicy Bypass` únicamente en el proceso PowerShell que lanzan; no cambian la política guardada de Windows. Si una política de empresa bloquea el script, conserva el mensaje y no cambies políticas globales.

## Qué hace y qué no hace

- Lee RAM, discos locales, GPU/controlador y cinco muestras de VRAM mediante `nvidia-smi`.
- Consulta versiones de Python detectadas por PATH, lanzador y registro; no importa PyTorch ni paquetes de tus proyectos. Omite los alias de Microsoft Store y no inicia ninguna instalación automática.
- Consulta FFmpeg y la presencia del codificador `libmp3lame`; busca DLL `avcodec` junto al ejecutable como indicio de una edición compartida.
- Escribe informes únicamente bajo `diagnostico\resultados`. No modifica registro, PATH, políticas persistentes, entornos virtuales ni carpetas de otras aplicaciones.
- La segunda comprobación registra revisiones Git de los modelos, lista de archivos seleccionados y tamaños/hashes declarados por Hugging Face. No ejecuta contenido recibido ni solicita credenciales.
- No demuestra que CUDA/PyTorch funcionen, que INT8 sea compatible ni que una canción quepa en 8 GB. Esas verificaciones requieren la instalación de la etapa 1, previa aprobación.

## Estado de validación del paquete

Preparado y revisado en Linux. Se comprobó la integridad del ZIP y se analizaron los manifiestos oficiales de dependencias. **No se ejecutaron estos scripts en Windows**: esta sesión no tiene acceso a tu laptop ni un intérprete PowerShell. La primera ejecución en tu equipo es necesaria para completar el diagnóstico; cualquier error debe conservarse, no interpretarse como un resultado favorable.

Lee `DIAGNOSTICO_Y_PLAN.md` para los hallazgos, estimaciones y condiciones para continuar. Las copias en `fuentes` son documentos/código consultados como texto; no hay que ejecutarlas.
