# Windows real: eliminar los reemplazos del marcador

**Estado: corrección de código preparada; aceptación en Windows pendiente.** Los resultados Linux y las pruebas simuladas no acreditan NTFS/OneDrive en la laptop.

## Dos fallos distintos en 08144b2

La validación recibida se ejecutó en Windows 10.0.26300.9711/Python privado 3.12.12: 14 pruebas, 12 aprobadas y dos fallidas. No se atribuye a OneDrive: estaba pausado y no hay evidencia que lo identifique como causante.

### 32 intentos frente a 17 nombres

`test_multiple_threads_and_reader_have_unique_temporaries_and_complete_lines` añadía una ruta a su lista **cada vez que se llamaba a `Path.replace`**, incluidos los reintentos del mismo temporal. Comparaba `len(intentos)` contra `len(set(intentos))` como si el primero contara creaciones. Es un defecto de la prueba: 32 intentos sobre 17 nombres distintos no demuestra 15 colisiones.

Se reprodujo con el código exacto de 08144b2 y un WinError 5 inyectado: **dos intentos, un solo temporal**. Una prueba nueva conserva esa distinción. No se ha encontrado evidencia de que `mkstemp` creara dos temporales con el mismo nombre. Los WinError 5 reales siguen siendo fallos de publicación, aunque ese contador de unicidad fuera incorrecto.

### Secuencia 1 en vez de 2 con el lector abierto

La prueba nativa antigua exigía que reemplazar un destino abierto tuviera éxito por utilizar `FILE_SHARE_DELETE`. Esa garantía era incorrecta para la ruta utilizada. El resultado real muestra que la publicación no se completó; no implica que se hubiese transferido mal un peso.

En CPython **v3.12.12**, `os.replace` termina en `internal_rename`, que llama a **`MoveFileExW` con `MOVEFILE_REPLACE_EXISTING`**. No solicita `FILE_RENAME_POSIX_SEMANTICS`. Microsoft documenta reglas específicas para destinos con handles abiertos y una opción ampliada que permite reemplazarlos conservando sus handles. Compartir borrado no convierte automáticamente la operación de CPython en esa operación ampliada.

Por tanto, había una limitación del mecanismo Windows **y un defecto de diseño/prueba al tratarlo como equivalente al reemplazo POSIX**. La corrección anterior toleraba el rechazo y dejaba el JSON antiguo, pero la prueba seguía exigiendo secuencia 2 en ese mismo archivo. Con la traza disponible no podemos identificar cualquier otro handle/filtro que pudiera intervenir en ese intento concreto. No basta con ampliar reintentos para garantizarlo.

Fuentes oficiales examinadas:

- [CPython 3.12.12, `Modules/posixmodule.c`, `internal_rename`](https://github.com/python/cpython/blob/v3.12.12/Modules/posixmodule.c).
- [Microsoft: `FILE_RENAME_INFORMATION`, reglas de archivos abiertos y `FILE_RENAME_POSIX_SEMANTICS`](https://github.com/MicrosoftDocs/windows-driver-docs-ddi/blob/staging/wdk-ddi-src/content/ntifs/ns-ntifs-_file_rename_information.md).

## Solución elegida: append-only, sin cambios de nombre

No se implementan llamadas Win32 de renombrado ampliado ni se cambian permisos globales. Se retira por completo la publicación por temporales/reemplazo para la telemetría:

| Archivo | Función actual |
|---|---|
| `memoria-eventos.jsonl` | Diario principal existente. Mantiene su cola acotada, recuperación y diagnósticos de escritura. |
| `memoria-ultima-etapa.jsonl` | Segundo diario de instantáneas completas: solo append, flush y fsync según la política de durabilidad. Permite conocer el evento reciente si falla temporalmente el principal. |
| `memoria-ultima-etapa.json` | Formato anterior, **solo lectura histórica**. El código nuevo no lo crea, sustituye ni borra. Puede conservar una secuencia antigua sin que eso represente el estado actual. |
| `memoria-telemetria-errores.jsonl` | Diagnósticos de E/S existentes, con etapa, error y contadores; stderr/consola siguen como respaldo. |

El supervisor obtiene el evento con mayor secuencia entre las fuentes válidas. En empates prefiere la nueva instantánea append-only. Solo acepta líneas completas terminadas en salto de línea y lee colas acotadas; no necesita leer los diarios completos ni cargar modelos para interpretarlos.

Ya no hay `mkstemp`, `replace`, `rename`, `unlink` ni truncado en la publicación de eventos. El mismo archivo permanece en su lugar mientras lectores y escritor comparten acceso de lectura/escritura. Los lectores Windows siguen utilizando `CreateFileW` con compartición READ/WRITE/DELETE por compatibilidad; **el funcionamiento nuevo no depende de DELETE**.

Si un tercero niega explícitamente **WRITE**, append también puede fallar. Eso se prueba y se informa: el principal puede seguir avanzando si el bloqueo afecta solo al segundo diario. No se oculta el error ni se fuerza su ACL. Ante un append parcial se aísla el fragmento con una nueva línea al recuperar; nunca se truncan registros históricos. El segundo diario puede tener huecos si fue inaccesible; el principal conserva sus eventos y su cola de recuperación. Si fallan ambos destinos de forma persistente, se mantienen el buffer acotado y los contadores explícitos de pérdida de la corrección anterior, sin prometer persistencia imposible.

Se conservan flush por evento, fsync periódico y al error/final del worker. No se promete durabilidad ante apagón entre sincronizaciones ni entrega exactamente una vez tras un fallo de fsync/close. Los diarios crecen durante la toma; no se rotan ni compactan con lectores abiertos. A cambio se añade una copia de los eventos en disco, sin clonar tensores ni aumentar la cola de RAM.

## Las pruebas se hacen más exigentes

La prueba concurrente nueva requiere **los 90 eventos completos en ambos diarios**, secuencias sin pérdidas/duplicados en ejecución sana, pilas aisladas por hilo, un lector que alcance el evento 90 y **cero errores de E/S**. Prohíbe `Path.replace` y creación de temporales durante la prueba. Otra prueba prohíbe además `os.replace` y borrados. No se sustituye la aserción defectuosa por una tolerancia a pérdidas.

Las pruebas Windows nuevas verifican:

1. Lector Win32 compartido abierto: lee secuencia 1, permanece abierto durante append y después lee secuencia 2 del mismo archivo, cuya identidad no cambia.
2. Lector CRT ordinario abierto: mismo contrato, sin exigir sustitución de un archivo abierto.
3. Lector Win32 que deniega WRITE: el segundo diario rechaza append, el fallo queda registrado y el principal alcanza secuencia 2; al liberar el handle se recupera la publicación posterior.

También hay un lector CRT en **otro proceso**, coordinado por stdin/stdout sin sleeps para provocar la carrera: debe ver el siguiente evento después de que el escritor lo confirme. Solo ejecuta Python de biblioteca estándar para leer JSON, no un worker musical.

Se mantienen pruebas de WinError 5 inyectado, fsync rechazado, append parcial en ambos diarios, recuperación de la cola, pérdida explícita al llenarse el buffer y propagación de excepciones de cálculo simuladas. Las pruebas del monitor conservan el corte bajo **1 GiB de RAM disponible durante tres muestras consecutivas**, incluso con GPU lenta o informes fallidos.

## Validación requerida en tu Windows

1. Ejecuta **ACTUALIZAR_MUSICSTUDIO.bat**, con las operaciones del proyecto cerradas. No hace falta copiar ZIP ni reinstalar nada.
2. Ejecuta **09_VALIDAR_TELEMETRIA_WINDOWS.bat**. Usa directamente el Python base privado 3.12.12, con `-I -S -B`; no carga los 57 paquetes, Torch, CUDA ni modelos. Comparte el bloqueo de operación del proyecto y no inicia música.
3. El BAT guarda bajo `resultados/validacion-io-windows-...`:
   - `pruebas-io-windows.log`: resultado detallado y diagnósticos no simulados emitidos a stderr.
   - `validacion-io-windows.json`: versión Windows/Python, hashes del código probado, pruebas ejecutadas, omitidas, errores y fallos.
4. Comparte esos dos archivos. **No repitas una generación musical todavía.** La aceptación de E/S no certifica que la toma completa quepa en RAM ni valida CUDA/calidad musical.

El runner usa archivos temporales **dentro de esa carpeta de resultados del proyecto**, para probar la ubicación OneDrive/NTFS real, no solo `%TEMP%` en otra carpeta. No mueve la instalación ni cambia variables globales; modifica únicamente `tempfile.tempdir` de su proceso durante los tests. Los temporales de cada prueba se limpian normalmente, y el informe/log del runner se conservan.

La suite de E/S contiene **16 pruebas**. Se exige `16` ejecutadas sin fallos y sin omisiones en esta versión; además el runner comprueba que las tres pruebas nativas requeridas se ejecutaron. `WINDOWS_TELEMETRY_TESTS_PASSED` solo puede emitirse en Windows con ese resultado, nunca por pasar en Linux o saltarse pruebas nativas. Si hay fallo, omisión o importación incompleta, devuelve error y **no acredita Windows**.

Se puede repetir el comando anterior `python.exe -B -m unittest pruebas.test_memory_io -v` para ver la suite, pero el BAT elimina ambigüedad de intérprete y conserva evidencia de la ubicación y del código realmente probado.

## Resultados en nube

Suite completa: **88 pruebas ejecutadas, 83 aprobadas y cinco omitidas** (tres requieren Windows nativo y dos CUDA). Se comprobó la sintaxis PowerShell y se ejecutó el runner en Linux: rechazó acreditar Windows con código 2, como corresponde. La suite de E/S aislada tiene 16 pruebas; en Linux se omiten las tres nativas. No se ha ejecutado ninguna generación.

## Qué sigue pendiente

Aquí se dispone de Linux, no de un kernel Windows, NTFS con los filtros de la laptop ni una RTX 4060 real. Se reprodujeron los dos mecanismos antiguos mediante fallos controlados y se comprobaron las fuentes de CPython/Microsoft. No se ha reproducido nativamente ese WinError 5 en esta nube ni se ha probado la solución en tu Windows todavía.

La corrección se publica para **validación de aceptación**, no como afirmación de que Windows ya pasó. Las tres pruebas nativas quedan pendientes del BAT real. La instalación, los modelos, INT8/offload, el archivo de paginación, la carpeta y otros proyectos permanecen fuera de los cambios.
