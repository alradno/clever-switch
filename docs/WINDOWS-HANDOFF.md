# Continuación en Windows — candidata conjunta 0.5.0 experimental

Usar este repositorio como fuente común. Antes de trabajar, revisar `git status` y
hacer `git pull --ff-only`; conservar cualquier modificación local antes de integrar.
No usar los ZIP anteriores como base de nuevos cambios.

La edición está en `src/cleverswitch/flow/version.py`: 0.5.0 experimental, protocolo 2.
La entrada por LAN está implementada en los mismos fuentes para ambos equipos,
con protocolo binario 1 opcional y activación explícita. El Mac está compilado e
instalado y sus permisos comprobados. Pasan 936 pruebas en ambas plataformas.
Windows está compilado y su diagnóstico empaquetado carga las APIs nativas y
confirma sesión disponible en CI. La instalación en el equipo doméstico y la
captura/inyección físicas siguen pendientes. Paquete y evidencia del commit
`4797a3e78fa5c02951bf3c02ee95f71500d6e1d7`:
[Windows candidate](https://github.com/alradno/clever-switch/actions/runs/39069359804).
El workflow Windows candidate valida y compila los mismos fuentes, sin instalar
la app, capturar entrada ni alterar el firewall del runner. Sus artefactos incluyen
ejecutable, instalador y manifiesto del mismo commit; no equivalen a aceptación física.
No confundir las pruebas de APIs simuladas con una aceptación en Windows.
Consolidar y validar antes de distribuirla al usuario.
La corrección final mantiene ordenados los reinicios del enlace dentro del proceso,
incluso con un reloj de baja resolución o ajustado hacia atrás. Las cuatro regresiones
de ambos sentidos fallaron antes y pasan después; los avisos de la sesión antigua
siguen rechazándose. No se ha debilitado la validación TLS ni la cancelación.
Véase [INPUT-LAN.txt](INPUT-LAN.txt). Conserva
el enlace y la coordinación de Windows 0.4.0 integrada con el Mac en 0.4.1.

«Comprobar permisos de entrada…» revisa la sesión sin registrar hooks ni enviar
eventos. El ejecutable admite `--input-check archivo.json` para guardar esa misma
comprobación desde su propia identidad. No modifica el enlace ni exige elevación.
El estado de entrada suspendida distingue ahora este equipo del compañero.

La 0.4.6 comparte las mejoras de rendimiento en ambas direcciones: borde de 80 ms,
cursor al confirmar el ratón sin esperar al teclado, seguimiento separado del
monitor del cursor y reconocimiento rápido sólo durante un traslado autorizado.
Se mantienen el rearme a 80 px, la pausa de 3 s y todas las confirmaciones frescas.
Las medidas de `validation/macos-0.4.6.json` usan TLS local real y radios simuladas;
no prueban la reconexión Bluetooth física ni las APIs nativas de Windows. Compilar
esta candidata consolidada desde el mismo commit, sin instalar versiones intermedias.

La 0.4.5 incluye los instaladores que faltaban en el checkout. `build_tray.py`
prepara app, dos scripts y manifiesto en `dist`. `Install-CleverSwitch.ps1`, ejecutado
sin administrador, instala en `%LOCALAPPDATA%\Programs\CleverSwitchFlow`, conserva
datos/Inicio y abre la app. Sólo el helper del firewall se eleva cuando hace falta.
Las reglas se limitan a esa app, dos puertos y la subred guardada, para DHCP en ambos
sentidos con perfiles Private/Public. No cambian por cada IP ni por versión.
El menú detecta rutas temporales, reglas pendientes y bloqueos expresos; sólo revisa
al arrancar, vincular, cambiar de red o por acción del usuario. [Detalles](UPDATES.md).
No pedir al usuario instalar aún: falta compilar y comprobar nativamente el paquete,
UAC, NetSecurity, Inicio y la aceptación física conjunta. Las pruebas PowerShell
ejecutadas en Mac usan archivos reales y simulan las APIs de Windows.

La revisión 0.4.4 añade reconexión por eventos y gestión del consumo en ambos:

- Arranque, despertar y regreso a casa anuncian disponibilidad. Una única respuesta
  directa informa de la dirección actual del receptor; TLS confirma la conexión.
- Pausa, reposo y cierre avisan de la partida cuando sea posible. El compañero
  aparca sus consultas; un apagado brusco conserva el máximo de siete intentos.
- Sin anuncios periódicos en una conexión sana ni sondeos de red/cursor/HID/BLE
  durante la ausencia. Recuperación de traslados pendiente siempre limitada.
- Avisos ordenados por sesión, sin ecos ni duplicados; se descartan callbacks
  de conexiones anteriores para no reactivar falsamente un enlace tras DHCP.
- IP Helper observa además NotifyUnicastIpAddressChange; Mac observa el servicio
  DHCP en SystemConfiguration. No se necesita un servicio ni sondeos del sistema.

Pasan 690 pruebas en Mac, cobertura 91,98 %, incluido aviso UDP y reconexión TLS
reales en loopback. La evidencia está en `validation/macos-0.4.4.json`.
Compilar y probar nativamente Windows sigue pendiente. Para comprobar reinicios,
activar Inicio al entrar en la sesión una vez desde la copia instalada estable.
El instalador 0.4.5 conserva la elección anterior de arranque. No probar con
copias en carpetas temporales ni cambiar las reglas de firewall sin comprobar la ruta.

La 0.4.4 está compilada, instalada y arrancada en Aplicaciones. Tras 130 segundos
sin intervención, el cierre nativo terminó en 0.32 s; el ejecutable
coincide con los cuatro módulos de producción probados. La vinculación, caché
y configuración de Inicio se conservaron. La aceptación física sigue pendiente.

La revisión anterior 0.4.3 sincroniza estas correcciones en ambos sistemas:

- Cancelación antes de enviar el ratón si se pausa/cierra, se bloquea la sesión
  o se pulsa un botón/modificador durante la espera de preparación.
- Rearme del borde y pruebas nuevas del teclado al reconectar incluso cuando
  se conserva el listener existente; sin reutilizar una permanencia anterior.
- Plazo total por RPC TLS, incluidos conexión, handshake y mensajes fragmentados.
- El cierre nunca libera desde otro hilo un recurso HID cuyo lector sigue activo
  (relevante para CancelIo en Windows); descarta escrituras tras cerrar.
- Menú y diagnóstico disponibles durante la sustitución del enlace.
- Cierre por señales integrado con el bucle nativo de Mac. La app 0.4.2 que
  llevaba horas en espera no atendió SIGTERM; la prueba corta anterior no cubría
  ese caso. Windows y el CLI conservan su manejo normal de señales.

Pasan 665 pruebas con 91,36 % de cobertura y Ruff en Mac. Los nuevos casos de
coordinación/reconexión están parametrizados para Windows y Mac. Las llamadas
nativas Windows están simuladas: falta compilar y probar esta candidata allí.
La evidencia está en `validation/macos-0.4.3.json`. No se ha pedido al otro equipo
una instalación intermedia; usar un único commit para preparar la pareja final.

La revisión 0.4.2 anterior reprodujo y corrigió estos problemas:

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

La candidata 0.4.3 anterior se instaló y arrancó en Aplicaciones; el cierre nativo
tras 130 s de espera terminó en 0,33 s. Los siete módulos modificados del ejecutable
coinciden con los fuentes probados. Identidad, enlace, caché e Inicio se conservaron.
Los permisos guardados no coinciden con la firma ad-hoc actual;
Bluetooth y Accesibilidad requieren renovación manual. No hay aceptación física
nueva ni compilación Windows de esta revisión.

Cuando se continúe en Windows, compilar y ejecutar la suite según el README.
Mantener la ruta estable permitida por el firewall, cerrar la copia anterior y
conservar una copia recuperable del ejecutable. No regenerar el enlace ni publicar
claves, certificados, caché o diagnósticos privados. Comprobar ambos sentidos,
llegadas lentas, regreso manual, pérdida de enlace y reposo; distinguir siempre
las pruebas simuladas del comportamiento de los periféricos reales.
