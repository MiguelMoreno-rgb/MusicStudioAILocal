# Corrección de la validación INT8, sin reinstalar paquetes

## Causa reproducida

Con PyTorch 2.10.0 y TorchAO 0.16.0, un `AffineQuantizedTensor` de `Int8WeightOnlyConfig(version=1)` puede fallar cuando `nn.Module.to()` se ejecuta dentro de `torch.inference_mode()`. `Module._apply` consulta `torch._has_compatible_shallow_copy_type`; en este contexto acaba despachándose como `aten._has_compatible_shallow_copy_type`, que esa subclase no implementa. Es el operador exacto del error reportado.

Se reprodujo en una instalación **CPU aislada de Linux**, con torch 2.10.0+cpu, el mismo wheel Python TorchAO 0.16.0 y Python 3.12.14, sin pesos musicales. Incluso `model.to("cpu")` sobre un modelo cuantizado ya en CPU produjo la misma excepción bajo `inference_mode`. Fuera de ese contexto no apareció en esa reproducción. Por tanto, **no es evidencia de que CUDA, Windows o cuantizar en CPU sean por sí solos la causa**, ni basta para declarar incompatibles todas las funciones de TorchAO 0.16 con torch 2.10.

La validación anterior usaba `model.to("cuda")` directamente dentro de `inference_mode`, aunque su comentario decía seguir la misma secuencia que ACE-Step. Esa comparación era incompleta: omitía su mecanismo real de transferencia. Se corrige esa diferencia; no se eliminan las comprobaciones.

## Comparación con ACE-Step fijado

En `ca1e85fe9430179831e6bc6be790c332190a3866`:

1. `init_service_loader.py` mantiene el DiT en CPU/BF16 con offload CPU + DiT y después aplica cuantización; esa secuencia se conserva.
2. `_load_model_context` utiliza **`_recursive_to_device`** para cargar y devolver el modelo a CPU.
3. Ese helper prueba `model.to()`, captura `NotImplementedError` y recorre los parámetros. Para AQT usa **`_move_quantized_param` → `_apply_fn_to_data(lambda x: x.to(device))`**, reconstruyendo el parámetro y preservando su representación cuantizada.
4. El helper upstream puede registrar transferencias incompletas sin lanzar una excepción. La validación corregida agrega comprobaciones estrictas del dispositivo real del parámetro, buffers y datos INT8/escalas/zero-point; un retorno sin excepción no se da por suficiente.

El código del helper se carga del árbol source original después de verificar sus hashes; **no se edita ACE-Step**, ni se copia una variante modificada de ese algoritmo. La generación ya usaba los métodos upstream; no requiere cambiar el worker para solucionar esta diferencia del validador.

Fuentes: [transferencia real](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_memory_transfer.py), [contexto de offload](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_offload_context.py), [loader/cuántización](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/acestep/core/generation/handler/init_service_loader.py), [Module._apply de torch 2.10](https://github.com/pytorch/pytorch/blob/v2.10.0/torch/nn/modules/module.py), [AQT de TorchAO 0.16](https://github.com/pytorch/ao/blob/v0.16.0/torchao/dtypes/affine_quantized_tensor.py).

## Decisión

- **Conservar Python 3.12.12, torch/torchaudio 2.10.0+cu128, torchvision 0.25.0+cu128, TorchCodec 0.10.0 y TorchAO 0.16.0.** Los archivos de dependencias y sus hashes no cambian.
- Conservar `Int8WeightOnlyConfig(version=1)`, cuantización inicial en CPU y offload CPU/GPU. TorchAO advierte que v1 está deprecada; cambiar a v2 sería un cambio de representación distinto al usado por este ACE-Step y no se hace para ocultar el fallo.
- No mover el DiT BF16 completo a CUDA antes de cuantizar como supuesto arreglo: aumentaría el pico de VRAM y el siguiente offload dentro de inferencia seguiría necesitando una transferencia correcta.
- No desactivar `inference_mode`, INT8, offload ni sus comprobaciones; no activar flags globales de conversión ni registrar un handler artificial del operador faltante.
- Mantener límites del allocator, guardas de RAM/VRAM y monitor de generación ya preparados. La nueva prueba sigue siendo una capa de 64×64 y un buffer, sin pesos musicales ni carga grande. No modifica otros programas.

## Qué comprueba ahora

La validación carga los mixins auténticos del commit fijado y ejecuta dos ciclos de CPU → CUDA → CPU **dentro de `inference_mode`**. Tras cada transferencia verifica:

- Dispositivo del parámetro y buffers, y de los componentes internos `int_data`, `scale` y `zero_point`.
- Almacenamiento de pesos realmente INT8, sin una conversión silenciosa a BF16.
- Forward finito y comparación numérica tolerante con la capa sin cuantizar.
- Conservación exacta de bytes INT8 y escalas al regresar a CPU, y forward en CPU después del offload.

Registra el método usado, los ciclos y errores numéricos en `validacion.json`. El resto de la validación —CUDA, codecs, imports, marcador solo si todo pasa— permanece activo. El log upstream puede seguir mostrando `model.to() raised NotImplementedError ... Moving parameters individually`: es la activación del fallback esperado; las comprobaciones posteriores determinan si funcionó.

## Regresión y evidencia

`pruebas/test_int8_offload.py` reproduce con tensores reales el operador exacto de la ruta anterior; prueba el helper, rechazo de dispositivo incorrecto y transferencia del almacenamiento AQT a `meta`. `meta` solo comprueba ese mecanismo sin almacenamiento físico, **no sustituye una GPU**. También incluye dos round trips CUDA reales, que se ejecutan cuando CUDA está disponible.

En nube: **11 pruebas pasadas, 1 omitida por ausencia de CUDA**, de 12 totales contando las 7 de integridad anteriores. No se simula CUDA. No se descargaron modelos musicales. La ejecución Windows/CUDA y el resto de la validación completa siguen pendientes en tu laptop.

## Aplicar sin reinstalar los 57 paquetes

1. Cierra las ventanas BAT de este proyecto que sigan ejecutándose. Conserva la carpeta instalada, su `.venv`, `.runtime`, `.cache`, `.state` y `resultados`.
2. Descarga el [ZIP actualizado de GitHub](https://github.com/MiguelMoreno-rgb/MusicStudioAILocal/archive/refs/heads/main.zip) y **extráelo en otra carpeta temporal**, fuera de la carpeta instalada. No muevas la instalación existente.
3. Desde ese ZIP, copia únicamente estos archivos a la carpeta `musicstudio` de tu instalación, conservando sus nombres:
   - `musicstudio/validate.py`: reemplazar el anterior.
   - `musicstudio/int8_offload.py`: archivo nuevo, dejar junto a `validate.py`.
4. Opcionalmente copia `pruebas/test_int8_offload.py` a `pruebas` y los documentos/checksums actualizados si deseas mantener tu copia documental completa. Si copias solo los dos archivos del arreglo, el `SHA256SUMS.txt` antiguo ya no describe esa copia parcial; eso no altera la verificación independiente de fuente/dependencias usada por el validador.
5. Ejecuta el **`07_VALIDAR_ENTORNO.bat` que ya tienes**, desde la carpeta instalada. Usa su Python privado; no ejecuta `pip`, no reinstala paquetes y no descarga modelos. No ejecutes `03_INSTALAR.bat` para aplicar esta corrección.
6. Comparte el nuevo `validacion.json` o `error.txt` de la carpeta más reciente de `resultados`. **No ejecutes `04_DESCARGAR_MODELOS.bat` todavía**: primero revisaremos que termine toda la validación local.

No hace falta editar código manualmente. Los cambios funcionales son los dos archivos del paso 3. Los restantes archivos del commit son la regresión, documentación y checksums; ningún lock de dependencias ni archivo de tus otras aplicaciones cambia.
