# Continuación en Windows — revisión 0.4.2

Usar este repositorio como fuente común. Antes de trabajar, revisar `git status` y
hacer `git pull --ff-only`; conservar cualquier modificación local antes de integrar.
No usar los ZIP anteriores como base de nuevos cambios.

La edición está en `src/cleverswitch/flow/version.py`: 0.4.2, protocolo 2. Conserva
el enlace y la coordinación de Windows 0.4.0 integrada con el Mac en 0.4.1.

La revisión posterior reproduce y corrige estos problemas:

- Una escritura GATT agotada podía continuar pendiente y repetir por HID una orden
  de canal ya ejecutada. Se cancela la tarea; un cambio de canal de resultado incierto
  se resuelve con pruebas de llegada, sin reenviarlo por otro transporte.
- El regreso manual y la reconstrucción tras reposo/red dejaban el borde rearmado.
  Ahora también requieren movimiento seguro hacia dentro y conservan la pausa de 3 s.
- Traer aquí descartaba la llegada si se perdía la respuesta y sólo esperaba 5 s.
  Ahora comprueba presencia hasta 8 s, despierta la espera pasiva y respeta el cierre.
  El fallo de un dispositivo no impide intentar devolver el otro.
- Una pulsación durante la llegada cancelaba todo el traslado. Se aplaza la colocación
  del cursor dentro del plazo original; la sesión bloqueada sigue impidiendo el cambio.
- Un fallo al anunciar por UDP impedía intentar la dirección TLS ya configurada.
  Se separan ambos resultados y se cierran los sockets de un inicio UDP fallido.
- La reconexión conservaba observaciones antiguas del teclado/compañero; se descartan.
  Una confirmación final recuperada deja de mostrar un error contradictorio.
- SIGTERM no despertaba el menú y los lectores en espera pasiva. Ahora el cierre
  despierta esos componentes mediante eventos, sin añadir sondeos periódicos.

En macOS 26.6.2 ARM64 pasan 646 pruebas sin exclusiones, cobertura 91,20 % y Ruff.
Se observaron 17 casos de regresión fallando antes de sus correcciones. La suite
incluye TLS real local con periféricos simulados y cancelación de una tarea asíncrona
Bluetooth real con la radio simulada. La compilación, firma, vista previa y cierre
nativo por SIGTERM pasan. Véase `validation/macos-0.4.2.json`.

La app 0.4.2 está instalada y arrancada en Aplicaciones. Identidad, enlace e Inicio
se conservaron. Los permisos guardados no coinciden con la firma ad-hoc actual;
Bluetooth y Accesibilidad requieren renovación manual. No hay aceptación física
nueva ni compilación Windows de esta revisión.

Cuando se continúe en Windows, compilar y ejecutar la suite según el README.
Mantener la ruta estable permitida por el firewall, cerrar la copia anterior y
conservar una copia recuperable del ejecutable. No regenerar el enlace ni publicar
claves, certificados, caché o diagnósticos privados. Comprobar ambos sentidos,
llegadas lentas, regreso manual, pérdida de enlace y reposo; distinguir siempre
las pruebas simuladas del comportamiento de los periféricos reales.
