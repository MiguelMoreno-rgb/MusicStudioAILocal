# Investigación: script `numba` y reparación del traslado

## Causa y evidencia

El reparador anterior reconocía activaciones y lanzadores PE de uv (`UVPY`/`UVSC`). El archivo **`.venv\Scripts\numba` es un script Python de texto sin extensión**, distribuido en `numba-0.63.1.data/scripts/numba` dentro del wheel oficial fijado. No es una activación ni un EXE. No lo genera ACE-Step; uv instala el script suministrado por Numba y ajusta su shebang según el formato wheel.

Se descargó **solo para investigación en la nube**, sin instalarlo, `numba-0.63.1-cp312-cp312-win_amd64.whl`. Su SHA256 coincidió con PyPI y con el lock de dependencias:

`bbad8c63e4fc7eb3cdb2c2da52178e180419f7969f9a685f283b313a70b92af3`

El archivo original empieza por `#!python\r\n`. El cuerpo importa `main` de `numba.misc.numba_entry` y lo llama bajo `if __name__ == "__main__"`. uv 0.9.6, función `install_script`, sustituye esa directiva por `#!<ruta absoluta al Python de .venv>` y la copia a `Scripts/numba`. Por eso conserva la ruta de Downloads después del traslado y activa el rechazo de scripts desconocidos.

Hay un detalle comprobado en el código de uv: al consumir la primera línea original, se detiene al leer `\r`, dejando el `\n` pendiente. Al añadir su propia terminación Windows `\r\n`, queda un LF extra antes del cuerpo. No hay que «limpiarlo»: se conserva exactamente. Se fijaron las huellas de los dos cuerpos posibles: el original y el que conserva ese LF de uv. No se normalizan saltos de línea ni se reemplazan rutas en el cuerpo.

Fuentes:

- [Metadatos oficiales PyPI de Numba 0.63.1](https://pypi.org/pypi/numba/0.63.1/json).
- [Wheel Windows cp312 examinado](https://files.pythonhosted.org/packages/af/fd/6540456efa90b5f6604a86ff50dabefb187e43557e9081adcad3be44f048/numba-0.63.1-cp312-cp312-win_amd64.whl).
- [uv 0.9.6: `format_shebang` e `install_script`](https://github.com/astral-sh/uv/blob/0.9.6/crates/uv-install-wheel/src/wheel.rs).
- [Especificación wheel: scripts](https://packaging.python.org/en/latest/specifications/binary-distribution-format/#recommended-installer-features).

No tenemos acceso al archivo de tu laptop. La evidencia identifica el formato esperado y explica el bloqueo; la herramienta comprobará el archivo real antes de escribir. Si difiere, no asume que es este caso.

## Reparación limitada

`instalacion/reparacion.lock.json` identifica nombre, versión, wheel y huellas del cuerpo permitido:

- Cuerpo original: `4f09858b22ba5bb3cafb5b8d2c4c35c97877d9fc04235cd77e46533f38233add`.
- Cuerpo con LF conservado por uv: `b8b084de6c17aa7e337cc82417878401e878dcefbe5203d4d029f3893b750a12`.

`musicstudio/relocate.py` exige para este caso:

1. Nombre exacto `numba`, distribución instalada `numba==0.63.1` y una única entrada correspondiente con SHA256 y tamaño en su `RECORD`. Las comprobaciones existentes validan ese hash y tamaño contra el inventario original antes de escribir.
2. Texto con primera línea `#!` terminada en CRLF, apuntando exactamente al Python privado `.venv\Scripts\python.exe` de la ruta anterior o definitiva, sin argumentos adicionales ni intérpretes globales.
3. SHA256 del cuerpo idéntico a uno de los dos auditados. Cualquier otra modificación cancela la reparación.
4. Modificación exclusiva del shebang. Todos los bytes posteriores a esa línea permanecen idénticos. Si ya apunta al destino, no se reescribe.
5. Actualización del hash y tamaño de esa entrada en `numba-0.63.1.dist-info/RECORD`, dentro de la misma transacción, con respaldo de ambos originales. Si falla la comprobación posterior, se restauran ambos según las reglas de rollback existentes.

No se ejecuta `numba`, `pip` ni `uv`; no se reinstala nada. No cambian el lock de las 57 dependencias, Python, PyTorch, CUDA, TorchAO, la corrección INT8 ni la excepción precisa de OneDrive. Persisten el inventario completo, controles de recursos, detección de concurrencia, respaldo y validación local.

## Otros scripts comprobados

Se consultaron por HTTPS los directorios ZIP de los **57 wheels para Windows/Python 3.12 seleccionados por el lock**, mediante lecturas HTTP Range; para los paquetes PyPI se seleccionaron archivos cuyos SHA256 figuran en el lock, y para los tres Torch se usaron sus URLs directas fijadas. No se descargaron sus payloads completos ni se instalaron. Esta inspección de nombres identifica **solo Numba con contenido `.data/scripts/`**. Los archivos `entry_points.txt` usan otro mecanismo: uv genera lanzadores, ya cubiertos por `UVSC`.

Esto no inventaría tu carpeta real ni prueba hashes de los otros 56 archivos ZIP completos. La herramienta examina todos los archivos presentes en tu `Scripts`; si hay otros scripts de texto con referencias antiguas desconocidas, ahora informa sus nombres juntos y cancela antes de escribir. Los EXE desconocidos siguen provocando rechazo. No se concede permiso genérico para reparar scripts arbitrarios.

| Wheel examinado | Scripts `.data/scripts` | Archivo `entry_points.txt` |
|---|---|---|
| `accelerate-1.12.0-py3-none-any.whl` | — | Sí |
| `anyio-4.15.1-py3-none-any.whl` | — | Sí |
| `certifi-2026.7.22-py3-none-any.whl` | — | — |
| `cffi-2.1.1-cp312-cp312-win_amd64.whl` | — | Sí |
| `charset_normalizer-3.5.2-cp312-cp312-win_amd64.whl` | — | Sí |
| `colorama-0.4.6-py2.py3-none-any.whl` | — | — |
| `diffusers-0.37.1-py3-none-any.whl` | — | Sí |
| `einops-0.8.2-py3-none-any.whl` | — | — |
| `einx-0.3.0-py3-none-any.whl` | — | — |
| `filelock-4.2.0-py3-none-any.whl` | — | — |
| `frozendict-2.4.7-py3-none-any.whl` | — | — |
| `fsspec-2026.9.0-py3-none-any.whl` | — | — |
| `h11-0.16.0-py3-none-any.whl` | — | — |
| `hf_xet-1.7.0-cp38-abi3-win_amd64.whl` | — | — |
| `httpcore-1.0.9-py3-none-any.whl` | — | — |
| `httpx-0.28.1-py3-none-any.whl` | — | Sí |
| `huggingface_hub-0.36.0-py3-none-any.whl` | — | Sí |
| `idna-3.20-py3-none-any.whl` | — | Sí |
| `importlib_metadata-9.0.1-py3-none-any.whl` | — | — |
| `jinja2-3.1.6-py3-none-any.whl` | — | Sí |
| `llvmlite-0.46.0-cp312-cp312-win_amd64.whl` | — | — |
| `loguru-0.7.3-py3-none-any.whl` | — | — |
| `markupsafe-3.0.4-cp312-cp312-win_amd64.whl` | — | — |
| `mpmath-1.3.0-py3-none-any.whl` | — | — |
| `networkx-3.7-py3-none-any.whl` | — | Sí |
| `numba-0.63.1-cp312-cp312-win_amd64.whl` | `numba` | — |
| `numpy-2.3.5-cp312-cp312-win_amd64.whl` | — | Sí |
| `packaging-26.3-py3-none-any.whl` | — | — |
| `peft-0.18.1-py3-none-any.whl` | — | — |
| `pillow-12.3.0-cp312-cp312-win_amd64.whl` | — | — |
| `psutil-7.2.1-cp37-abi3-win_amd64.whl` | — | — |
| `pycparser-3.11-py3-none-any.whl` | — | — |
| `pytorch_wavelets-1.3.0-py3-none-any.whl` | — | — |
| `pywavelets-1.9.0-cp312-cp312-win_amd64.whl` | — | — |
| `pyyaml-6.0.3-cp312-cp312-win_amd64.whl` | — | — |
| `regex-2026.9.29-cp312-cp312-win_amd64.whl` | — | — |
| `requests-2.34.2-py3-none-any.whl` | — | — |
| `safetensors-0.7.0-cp38-abi3-win_amd64.whl` | — | — |
| `scipy-1.17.0-cp312-cp312-win_amd64.whl` | — | — |
| `setuptools-71.1.0-py3-none-any.whl` | — | Sí |
| `six-1.17.0-py2.py3-none-any.whl` | — | — |
| `soundfile-0.13.1-py2.py3-none-win_amd64.whl` | — | — |
| `sympy-1.14.0-py3-none-any.whl` | — | Sí |
| `tokenizers-0.22.2-cp39-abi3-win_amd64.whl` | — | — |
| `toml-0.10.2-py2.py3-none-any.whl` | — | — |
| `torch-2.10.0%2Bcu128-cp312-cp312-win_amd64.whl` | — | Sí |
| `torchao-0.16.0-py3-none-any.whl` | — | — |
| `torchaudio-2.10.0%2Bcu128-cp312-cp312-win_amd64.whl` | — | — |
| `torchcodec-0.10.0-cp312-cp312-win_amd64.whl` | — | — |
| `torchvision-0.25.0%2Bcu128-cp312-cp312-win_amd64.whl` | — | — |
| `tqdm-4.67.1-py3-none-any.whl` | — | Sí |
| `transformers-4.57.6-py3-none-any.whl` | — | Sí |
| `typing_extensions-4.16.0-py3-none-any.whl` | — | — |
| `urllib3-2.8.0-py3-none-any.whl` | — | — |
| `vector_quantize_pytorch-1.27.20-py3-none-any.whl` | — | — |
| `win32_setctime-1.2.0-py3-none-any.whl` | — | — |
| `zipp-4.1.1-py3-none-any.whl` | — | — |

## Regresión y límites

Se añadieron seis pruebas de regresión: ambos cuerpos byte a byte e idempotencia; rechazo de cuerpo/intérprete/formato alterados; preflight y transacción completos de 57 distribuciones con actualización válida de `RECORD` y respaldo; rechazo de propietario ausente/hash/tamaño incorrectos antes de escribir; rollback de `numba` y `RECORD`; y detección de otros scripts desconocidos en la misma pasada.

Resultado en nube: **34 pruebas ejecutadas, 33 aprobadas y 1 omitida por necesitar CUDA**. La suite conserva además las pruebas de integridad, concurrencia y la regresión INT8 real en CPU. Estas comprobaciones en Linux no ejecutan lanzadores Windows ni verifican CUDA de la laptop. La prueba Windows pendiente se ejecuta con **08_REPARAR_TRASLADO.bat** tras aplicar [los dos archivos indicados](REPARAR_TRASLADO.md#si-la-reparación-se-detuvo-en-numba).
