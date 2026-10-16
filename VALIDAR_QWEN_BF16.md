# Validar Qwen3-Embedding-0.6B en CPU/BF16

Esta prueba mide la carga real del codificador Qwen local antes de autorizar otra generación. No carga el DiT ni el VAE, no ejecuta inferencia de embeddings ni generación musical y no contacta Hugging Face. Usa el Python de `.venv`, exige Python 3.12.12, PyTorch 2.10.0+cu128 y Transformers 4.57.6, y comprueba todos los archivos de Qwen contra `instalacion/modelos.lock.json` antes de cargarlos.

## Ejecutar en Windows

1. Asegúrate de que el checkpoint ya existe en `checkpoints\Qwen3-Embedding-0.6B`. La prueba no descarga ni repara archivos.
2. Cierra manualmente aplicaciones que no necesites y confirma que Windows tiene al menos 4 GiB de RAM disponible.
3. Desde la carpeta del proyecto, haz doble clic en `10_VALIDAR_QWEN_BF16.bat`. El BAT usa `.venv\Scripts\python.exe` y el mutex común del proyecto; se detiene si hay otra actualización, reparación, instalación o generación activa.
4. Espera a que termine. No inicies otra operación del proyecto durante la validación.
5. Revisa la carpeta nueva `resultados\validacion-qwen-bf16-*`: `informe.json` contiene las métricas y `consola.log` conserva los mensajes del supervisor y del worker. Cada ejecución crea una carpeta distinta.

No edites código ni ejecutes `pip`, `uv`, `03_INSTALAR.bat`, `04_DESCARGAR_MODELOS.bat` o un BAT de generación para esta prueba. OneDrive Documentos está autorizado únicamente en la ruta definitiva registrada; mantén la carpeta disponible localmente y pausa la sincronización mientras corre.

## Qué valida y cómo se protege

El proceso hijo carga únicamente `AutoModel.from_pretrained` sobre la ruta local, con `dtype=torch.bfloat16`, `low_cpu_mem_usage=True` y `local_files_only=True`. Luego recorre los parámetros reales y exige que todos estén en BF16 y en CPU. No mueve pesos a CUDA ni consulta VRAM, porque no utiliza GPU. El log identifica la carga y registra versiones, duración y tamaño del payload de parámetros.

El preflight exige 4 GiB de RAM disponible antes del worker y vuelve a medir después de verificar los hashes del checkpoint. Mientras carga, un supervisor independiente muestrea la RAM disponible del sistema y el RSS/memoria privada del worker cada segundo. Si no puede leer esas métricas, detiene el worker por seguridad. Si la RAM disponible queda por debajo de 1 GiB durante tres muestras consecutivas, termina únicamente el proceso hijo de validación. No cambia el protector existente de generación, el archivo de paginación, variables persistentes ni aplicaciones ajenas.

`RSS` es la memoria residente/working set del proceso. `private_bytes` usa el contador privado que expone psutil en Windows; si no está disponible, informa el USS como alternativa y etiqueta la fuente. RAM disponible es una métrica del sistema, distinta de ambas. El muestreo periódico puede perder picos que duren menos de un segundo.

El informe y el registro de consola incluyen RAM disponible inicial, antes y después de la carga, mínimo muestreado, RSS/memoria privada antes y después, picos observados, deltas durante `from_pretrained`, su duración, cantidad y dtypes/dispositivos de parámetros, y el tamaño BF16. `bf16_payload_saving_vs_fp32_bytes` es una diferencia teórica de almacenamiento de parámetros (4 bytes frente a los bytes observados por parámetro), no un benchmark contra una carga FP32. No se ejecuta una segunda carga FP32 que pueda duplicar el consumo.

## Interpretación y límites

`status: "passed"` demuestra que esta instalación pudo cargar el checkpoint local completo en CPU/BF16 y que la inspección de parámetros devolvió BF16/CPU. No demuestra velocidad de inferencia, calidad del embedding, ahorro real frente a una ejecución FP32 en la misma laptop, ni disponibilidad de VRAM durante el DiT. El resultado debe revisarse antes de decidir otra generación.

La suite sintética prueba el umbral y las tres muestras consecutivas, el reset del contador, las versiones, la validación dtype/dispositivo y las métricas RSS/privada sin importar torch ni cargar pesos. La validación de carga real requiere ejecutar el BAT en el Windows con el checkpoint ya instalado; la nube no puede confirmar RAM, DLL, lectura local del checkpoint ni funcionamiento en la RTX 4060 del equipo.

Los informes contienen rutas relativas y métricas de hardware. Revísalos antes de compartirlos; `resultados` permanece excluido de Git.
