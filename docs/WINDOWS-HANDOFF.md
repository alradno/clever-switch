# Continuación en Windows — candidata conjunta 0.5.11 experimental

Usar este repositorio como fuente común. Revisar `git status` antes de
`git pull --ff-only` y conservar cambios locales. No partir de ZIP anteriores.

La 0.5.11 recupera la vía rápida después de una pausa del receptor. Una
confirmación UDP tardía, autenticada y del control actual permite una comprobación
con un reto nuevo; mientras tanto, la entrada continúa por TLS. Sólo vuelve a
UDP si responde ese reto. Hay como máximo una ventana adicional por conexión,
con tres intentos y plazo de 100 ms, sin búsquedas periódicas. Un corte sin esa
confirmación mantiene TLS. Cambiar o cancelar el control retira la comprobación.
También aprovecha una prueba inicial que termina durante un cruce, conservando
TLS para el movimiento hasta que el destino acepta. Se aplica en ambos sentidos.

Conserva además el movimiento local que quedó en cola durante un bloqueo breve
de sesión: entrar al interior y volver al borde sigue iniciando el cruce. Un
movimiento anterior a otra sesión no se reproduce al recuperar el control.

La 0.5.10 recupera también las desconexiones del ratón. Cuando el sistema
anuncia su llegada o retirada, libera los botones cuyo evento de soltar pudo
perderse y conserva el teclado, el control remoto y el enlace. Windows renueva
sólo el capturador del dispositivo afectado; Mac observa teclado y ratón por
separado. Los avisos tardíos de una sesión cerrada se descartan. No añade
búsquedas periódicas ni hilos. Un cambio de ratón en el receptor no interrumpe
un arrastre enviado desde el otro equipo.

La 0.5.9 conserva la prioridad del uso local cuando coincide con una llegada
remota. Una tecla, clic, rueda o movimiento que quedó en cola durante la
aceptación cancela esa llegada al procesarse. El cursor quieto no la cancela.
Las teclas remotas pendientes no se reproducen después de recuperar el control;
el enlace permanece abierto y permite el siguiente cruce. Se aplica en ambos
sentidos, sin añadir esperas ni consultas nativas al recorrido de entrada.

La 0.5.8 evita que el destino reclame al otro equipo con un pequeño movimiento
junto al borde después de recibir control remoto. Cada nuevo foco descarta la
habilitación anterior del borde; un movimiento local nuevo hacia el interior
lo vuelve a habilitar. Se aplica en ambos sentidos y también tras cancelar con
un teclado o ratón local. Si el movimiento físico ya entra en el interior,
permite cruzar en el siguiente movimiento, sin otro paso previo. En Mac, el primer
movimiento local usa la última posición recibida, igual que Windows, sin calcular
un salto desde una posición antigua.

La 0.5.7 conserva el movimiento que queda después de cruzar de vuelta al
ordenador de origen. Calcula la altura donde la trayectoria toca el borde,
incluso en diagonales rápidas o con pantallas de distinta altura; los huecos
entre monitores no autorizan el cruce. Ambos sistemas calculan el primer
movimiento local desde la posición restaurada, sin perderlo ni usar una antigua.
Si el regreso ya ha entrado en pantalla, permite invertir el recorrido enseguida;
los regresos pequeños mantienen la protección contra rebotes. Si hay una tecla o un
botón pulsado, espera a soltarlo antes de cambiar de equipo.

También evita cerrar el enlace por confundir una restauración de teclado en curso
con un fallo. La comprobación lee el resultado al terminar la transición, sin
bloquear el hilo de entrada. Una restauración que falla sigue cerrando la sesión.

Desde 0.5.6 se evita reproducir posiciones antiguas cuando llegan varios movimientos
juntos. Autentica todo el lote y aplica la posición más reciente entre cada clic,
tecla o cambio de foco. No añade espera para agrupar. Los mensajes alterados,
repetidos o antiguos no sustituyen una posición válida; cancelar el control
descarta los movimientos pendientes. Se aplica igual en ambos sentidos.

En la validación de 0.5.6 en Mac, una ráfaga de 64 movimientos con un coste simulado
de 1 ms por aplicación pasa de unos 86 ms a 1,5 ms hasta la posición final.
Son medianas de recuperación del receptor en loopback, no latencia física.
La ráfaga necesita una aplicación y una confirmación, frente a 64 antes.
El banco recorre 54 casos por versión, con 1/16/64 movimientos y tres costes.
Reproducir con benchmarks/input_bursts.py.

Desde 0.5.5, la app envía movimiento cifrado por UDP y mantiene teclas, clics,
rueda y cambios de foco en TLS. Cada acción conserva la posición exacta del
cursor; un movimiento posterior espera a las acciones que lo preceden. La vía
rápida se negocia entre ambos equipos y se comprueba con intentos limitados.
Si UDP falla o el compañero es una versión anterior, continúa por TLS.

Se reutiliza UDP 47655, junto a TCP 47654 y la vinculación existente. No hay
puertos nuevos ni cambios de firewall. La confirmación de la última posición
detiene sus reintentos; si se pierde, tienen plazo finito. Una vía fallida no
se vuelve a probar con cada cruce. Las claves
son nuevas por conexión y se retiran al cerrar. El diagnóstico muestra el
transporte y contadores agregados, sin guardar teclas ni historial del cursor.

La validación de 0.5.5 recorrió 52 casos por plataforma con TLS mutuo y UDP
locales. Una retención artificial de 80 ms en TLS produjo pausas de movimiento de 81–88 ms con el
transporte fiable y de 1–10 ms con el híbrido, a 125/1.000 Hz y en ambos sentidos.
Las 104 acciones de cada ejecución conservan orden y posición; también se prueba pérdida
artificial de uno de cada diez datagramas. Son medidas locales con entrada
sintética, no latencia de Wi-Fi o de periféricos. Una acción retenida sigue
frenando el movimiento que la sucede para conservar su significado.
Reproducir con benchmarks/input_hybrid.py.

Pasan 1.359 pruebas por plataforma, Ruff y ambas compilaciones. Se reprodujeron
diez fallos antes de corregirlos; hay 30 casos nuevos y 333 pruebas focalizadas.
El banco recorre 36 escenarios y conserva las 144 acciones, con duplicados,
desorden, alteraciones, pérdidas y pausas. Tras una pausa inicial de 150 ms,
una retención posterior de TLS causa huecos de 81–89 ms antes y 1,3–9,7 ms
después en Mac. Son medidas de loopback con entrada sintética; la pausa inicial
permanece y no se mide la latencia física. La aceptación física sigue pendiente. La comprobación
del ejecutable Windows y las medidas del banco en CI se registran con su commit
en `validation/input-0.5.11-path-recovery.json`. `experiments/` reutiliza los módulos de
producción; la vía rápida ya forma parte de la candidata. Mac y Windows deben
probarse desde el mismo commit y edición.

El usuario ha pedido seguir desarrollando y probar ambas versiones al volver.
No pedir instalaciones ni renovación de permisos ahora. Mac conserva 0.5.1;
el último Windows doméstico observado tenía 0.5.0. Validar fluidez en ambos
sentidos, Easy-Switch del teclado, teclas/arrastres locales, reposo/despertar,
DHCP y cambios físicos de pantalla. Se mantienen los límites de recuperación
y la instalación estable por usuario; no hay administración remota.

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
La aceptación física de UAC, NetSecurity, Inicio y actualización en el equipo
doméstico sigue pendiente. Las pruebas PowerShell
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
