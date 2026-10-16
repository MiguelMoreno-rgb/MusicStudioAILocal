# MusicStudioAILocal: instrucciones para cambios futuros

El usuario mantiene una instalación de ZIP ya vinculada mediante un repositorio Git de control en `.musicstudio-update`. Publicar código en `main` debe permitir que `ACTUALIZAR_MUSICSTUDIO.bat` lo incorpore sin ZIP ni reinstalar dependencias.

- No tocar `.venv`, `.runtime`, `.cache`, `.state`, checkpoints, resultados ni archivos personales. No subir modelos ni inventarios del equipo. No ejecutar instalación, reparación o generación automáticamente después de una actualización de código.
- Mantener la ruta autorizada de OneDrive Documentos, la reparación de `numba`, la corrección INT8 y los controles de integridad, memoria y concurrencia.
- El actualizador debe funcionar con el Python base existente, aunque `.venv` esté pendiente de reparación. No agregar dependencias para actualizar.
- Si cambia `instalacion/actualizar.py`, regenerar su SHA256 en `ConfigurarActualizaciones.ps1`. Si cambia ese PowerShell, regenerar el comando inicial verificado. Ejecutar `python3 pruebas/actualizar_huellas.py` para ambos y para `SHA256SUMS.txt`; no relajar validaciones para que pase.
- Preservar CRLF y ASCII de BAT/PowerShell. Revisar el plan de rutas permitidas si se añade un nuevo tipo de archivo; nunca habilitar rutas protegidas. Los archivos retirados publicados solo se eliminan tras preflight, confirmación y respaldo.
- Probar la suite en `pruebas`, incluida la actualización con remotos Git temporales. Mantener la distinción entre pruebas Linux y validación real Windows/CUDA.
- Usar el checkout existente en nube; no crear worktrees sin petición explícita. Usar las herramientas ya disponibles y los locks existentes. Publicar solo cuando el usuario lo autorice; no pedir de nuevo lo ya autorizado.
