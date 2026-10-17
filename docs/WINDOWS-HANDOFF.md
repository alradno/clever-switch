# Continuación en Windows — candidata conjunta 0.5.1 experimental

Usar este repositorio como fuente común. Revisar `git status` antes de
`git pull --ff-only` y conservar cambios locales. No partir de ZIP anteriores.

Fuentes de los binarios 0.5.1: `5241be90f146df0529eaba137b05d8209c19fbe7`.
Pasan 954 pruebas en cada plataforma. Windows está compilado y su ejecutable
empaquetado pasa la comprobación nativa; se ha registrado/cerrado también el
observador nativo del teclado sin capturar entrada. Paquete y manifiesto verificados:
[Windows candidate](https://github.com/alradno/clever-switch/actions/runs/39076287821).

La corrección saca las consultas de permisos del hilo de entrada en ambas
plataformas y recupera teclas pendientes al cambiar de canal físico y volver.
Windows recibe avisos de interfaz de teclado mediante CM_Register_Notification
y renueva el hook en su hilo original. La geometría se prepara antes de recibir
movimiento y SendInput usa MOVE_NOCOALESCE. Mac tiene la misma recuperación por
avisos IOKit, sin abrir ni leer dispositivos desde ese observador.

El Windows doméstico aún responde como 0.5.0. No hay vía de administración remota
para instalar 0.5.1. El Mac 0.5.1 ya está instalado en Aplicaciones, pero faltan
los permisos de su nueva firma ad-hoc. No modificar TCC ni controles de seguridad.
El usuario ya probó 0.5.0 y comunicó microcortes y fallo de teclado; la aceptación
física de 0.5.1 no está comprobada. Validar ambos orígenes, teclas mantenidas al
cambiar Easy-Switch, reconexión, arrastre local en destino y salida segura.

La simulación compara el p99 de entrega con consultas de permisos de 18 ms:
23–24 ms antes y menos de 0,7 ms después. No mide Wi-Fi, pantalla ni aplicaciones.
Reproducción: `PYTHONPATH=src .venv/bin/python benchmarks/input_pacing.py`.
Evidencia: `validation/input-0.5.1-reliability.json`. No se cambian vinculación,
protocolo, puertos, instalador o firewall. Detalles: [INPUT-LAN.txt](INPUT-LAN.txt).

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
