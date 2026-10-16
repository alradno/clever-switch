# Instrucciones para trabajar en este repositorio

## Contexto y fuente de verdad

- Leer `README.md`, `README-FLOW.txt` y `docs/MAC-HANDOFF.md` antes de cambiar el flujo.
- Edición actual: `src/cleverswitch/flow/version.py`. No confundir la versión del
  paquete upstream/SCM con `EDITION` y `PROTOCOL` de esta aplicación.
- Entrada de producción de esta edición: `tray_main.py`, compilada con `build_tray.py`.
- `docs/UPSTREAM-*`, `docs/Installation.md`, el CLI y los instaladores de `scripts/`
  documentan o implementan el original; no sustituir con ellos el flujo de esta edición.
- Antes de integrar trabajo de otro equipo: inspeccionar `git status`, obtener los
  cambios remotos y conservar las modificaciones locales. No sobrescribir trabajo
  del otro Codex ni hacer push forzado. Usar commits claros y publicar la evidencia.

## Requisitos que deben conservarse

- MX Keys original + MX Master 3; Windows canal 1/izquierda, Mac canal 2/derecha.
- Botones Easy-Switch nativos: modo observe-only. No volver a habilitar desvíos
  temporales/persistentes ni hooks de comandos o scripts configurables.
- Comparación exacta de nombres de hardware admitidos; no confiar en aliases editables
  ni en coincidencias parciales que admitan MX Keys S o MX Master 3S.
- Prueba HID++ nueva con nonce para confirmar presencia. La caché permite reconocer
  modelos, nunca confirmar una llegada o autorizar una transferencia por sí sola.
- Un único bloqueo compartido entre origen/destino durante un traslado. Operaciones
  idempotentes, plazos limitados, estado terminal persistido y resolución de ACK perdido.
- Protocolo 2 en ambos equipos; no permitir traslados automáticos con el protocolo antiguo.
- TLS mutuo/pinning en LAN privada. No añadir control remoto arbitrario, nube o cuentas.
- Reconexión finita y espera pasiva; no introducir sondeos indefinidos fuera de casa.
- Icono sin ventana permanente y arranque de sesión por usuario; Salir debe cerrar la app.
- No registrar caracteres, contenido de entrada, certificados, secretos o historial del cursor.

## Conservación de datos y pruebas

- No borrar/regenerar `~/.config/cleverswitch-flow` ni `~/.config/cleverswitch-tray`
  durante una actualización o diagnóstico. No pedir repetir la vinculación por actualizar.
- No subir identidades, claves, archivos de vinculación ni diagnósticos con información privada.
- No desactivar Gatekeeper/Defender, retirar cuarentena ni conceder permisos automáticamente.
- Usar la suite `python -m pytest` y Ruff en el entorno apropiado al cambio. En Windows
  se verificaron 615 pruebas con Python 3.12; no presentar eso como evidencia física en Mac.
- Algunas pruebas de APIs Windows usan mocks y pueden necesitar ajustes de portabilidad
  al ejecutarlas en Mac. Corregir los límites de plataforma; no ocultar fallos relevantes
  quitando pruebas ni afirmar que la suite de Mac pasa sin ejecutarla.
- Distinguir evidencia de código/simulación, compilación nativa y prueba con periféricos reales.
- Documentar cada validación de Mac en `docs/validation/`: versión de app/OS, conexión,
  pasos, resultado, fallos y pendientes. Nunca copiar las claves del usuario al informe.
