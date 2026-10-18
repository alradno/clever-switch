CLEVERSWITCH FLOW — candidata experimental 0.5.20, Windows 1 y Mac 2

Para MX Keys original + MX Master 3. Windows delante/a la izquierda, Mac a la derecha.
Basado en CleverSwitch v1.5.7 (de0a4f4f4b996a63bbdf2e368c93eb3843eb2732), GPL-3.0-or-later.
Modificación independiente, sin mantenimiento ni certificación del autor original.

QUÉ HACE
Entrada continua por LAN con los periféricos conectados físicamente a un equipo.
No transmite el escritorio. Conserva el enlace, protocolo de control 2, protocolo
de entrada 1 y los puertos. El menú Ratón y teclado por red (experimental) requiere
activación explícita en ambos. Easy-Switch permanece en el propio dispositivo.

REVISIÓN 0.5.20
La 0.5.20 evita un cruce involuntario después de recolocar el cursor en Mac.
Si una aplicación lo mueve sin generar un evento de movimiento, el siguiente
clic o rueda conserva esa posición como origen del próximo desplazamiento.
Así, un movimiento vertical junto al borde no se interpreta como uno horizontal
desde la posición anterior. Windows ya conservaba esa referencia y se comprueba
con los mismos casos. Los eventos propios marcados no alteran la referencia
física. No añade esperas, sondeos, hilos ni consultas nativas.

Pasan 1.836 pruebas por plataforma, Ruff y ambas compilaciones. Hay 30 casos
nuevos: 12 reproducen el cruce involuntario anterior y 18 controles ya pasaban.
Las 243 pruebas focalizadas comprueban ambos orígenes, TLS/híbrido, clics primarios
y adicionales, ambas ruedas y callbacks inmediatos o en cola. Conservan el
control local, el enlace, el siguiente cruce intencionado y la escritura.
Las APIs de entrada son simuladas; TLS/UDP usan loopback real. La fluidez,
Easy-Switch y el consumo físicos siguen pendientes de aceptación conjunta.

Evidencia: docs/validation/input-0.5.20-pointer-baseline.json. Candidata sin instalar.

REVISIÓN 0.5.19
La 0.5.19 conserva el control local si un cruce quedó en cola y después
llegó una tecla, clic, rueda o movimiento a la aplicación local antes de empezar
la captura. Al procesar esa entrada cancela el cruce que acaba de iniciarse,
sin reenviarla al otro equipo. Conserva la posición local, las liberaciones y
el enlace para el siguiente cruce. Un cursor quieto no lo cancela y los eventos
de un periodo local anterior no afectan a un cruce nuevo. La corrección es común
a Mac y Windows; no añade esperas, sondeos, hilos ni consultas nativas, y conserva
la ruta de los callbacks que se procesan al momento.

Pasan 1.806 pruebas por plataforma, Ruff y ambas compilaciones. La corrección inicial tiene
22 casos: 16 reproducen el fallo anterior; cuatro conservan el cruce con el cursor
quieto y dos descartan movimiento de un periodo local ya terminado. Pasan 257
pruebas focalizadas. Se comprueban ambos orígenes y TLS/transporte híbrido.
Las APIs nativas y la entrega a aplicaciones están simuladas; TLS/UDP usan
loopback real. La aceptación física de fluidez, recuperación y consumo
sigue pendiente.

Evidencia: docs/validation/input-0.5.19-queued-edge.json. Candidata sin instalar.

La validación ampliada de 0.5.19 añade 24 casos por plataforma: 64 reconexiones
repetidas y ocho cortes de TLS en un solo sentido. Comprueba que se sueltan
teclas y arrastres, se recupera una liberación rechazada temporalmente y funciona
el siguiente cruce. Los trabajadores y observadores de entrada terminan al cerrar
el enlace; sus avisos tardíos no borran pulsaciones de la conexión nueva.
También se comprueba por UDP real que un paquete cifrado de un enlace anterior
se rechaza durante un foco nuevo. Las APIs de entrada siguen simuladas.
Esta ampliación no detectó otro fallo ni cambia el código o los paquetes 0.5.19;
la fluidez y el consumo físicos siguen pendientes.
Evidencia: docs/validation/input-0.5.19-reconnect.json.

REVISIÓN 0.5.18
La 0.5.18 evita dejar una tecla o un arrastre pulsados al iniciar un cruce.
Si la pulsación llegó a la aplicación local antes de empezar la captura,
su repetición y su liberación siguen llegando allí aunque la captura ya haya
empezado. El cruce se cancela y puede repetirse después, sobre el mismo enlace.
Conserva el destino de las nuevas pulsaciones capturadas. La corrección es común
a Mac y Windows y no añade esperas, sondeos, hilos ni consultas nativas.

Pasan 1.760 pruebas por plataforma, Ruff y ambas compilaciones. Se añaden
39 casos de pulsaciones: 32 reproducen la pérdida anterior de repetición o
liberación y siete comprueban avisos de dispositivos, cola saturada, inicio
y cierre. Pasan 227
pruebas focalizadas. Además, 16 casos fuerzan la cola de movimiento y rueda
sin cambiar sus posiciones esperadas. Se comprueban teclas, modificadores
y botones en ambos sentidos, con TLS o transporte híbrido. Las APIs nativas y la entrega a las
aplicaciones son simuladas; TLS/UDP usan loopback real. La aceptación física
de fluidez, recuperación y consumo sigue pendiente.

Evidencia: docs/validation/input-0.5.18-press-ownership.json. Candidata sin instalar.

REVISIÓN 0.5.17
La 0.5.17 conserva Bloq Mayús en los cruces rápidos. El nuevo cruce usa el
último estado enviado aunque macOS aún no lo haya aplicado. Evita recuperar
un estado antiguo y alterar las mayúsculas o minúsculas al escribir.
Si falla la creación, preparación o publicación nativa del evento, conserva
el estado anterior. Un cambio físico de Bloq Mayús sigue teniendo prioridad.
Se comprueban cruces iniciados en ambos equipos, con TLS y transporte híbrido.
No añade esperas, sondeos, hilos ni consultas al recorrido del ratón.

La validación de 0.5.17 registró 1.705 pruebas por plataforma, Ruff y ambas compilaciones. Los 26 casos
nuevos reproducen el fallo anterior en la comparación local; también pasan
256 pruebas focalizadas. Se comprueban Bloq Mayús activado y desactivado,
cruces rápidos con eventos nativos pendientes y fallos de preparación del evento.
Las APIs de entrada y su aplicación tardía están simuladas; TLS/UDP usan loopback
real. La aceptación física de fluidez, recuperación y consumo sigue pendiente.

Evidencia: docs/validation/input-0.5.17-caps-order.json. Candidata sin instalar.

REVISIÓN 0.5.16
La 0.5.16 conserva el destino de la rueda al mover y desplazar enseguida.
En Mac, cada evento de rueda usa la posición ordenada del movimiento anterior,
aunque WindowServer todavía no lo haya aplicado. Evita desplazar la zona donde
estaba antes el cursor. Conserva ambas direcciones de rueda, sus fracciones y
las coordenadas negativas o fraccionarias. Windows mantiene el orden de sus
eventos nativos; ambos sentidos se comprueban con TLS y transporte híbrido.
No añade esperas, consultas del cursor, sondeos, hilos ni cambios de protocolo.

La validación de 0.5.16 registró 1.679 pruebas por plataforma, Ruff y ambas compilaciones. La corrección
de rueda conserva sus 22 casos: 12 reproducían el fallo anterior y 10 ya pasaban.
Se añaden 64 secuencias de continuidad con 6.144 operaciones generadas: mezclan
reconexiones de teclado y ratón, escritura, arrastre, rueda y movimiento.
Se comprueban ambos orígenes, TLS/híbrido y entrega inmediata o en cola. Cada
acción conserva orden y posición; las teclas y botones pendientes se liberan,
el canal sigue abierto y funciona el siguiente cruce. La prueba ampliada no
detectó otro fallo y no cambió el código operativo de 0.5.16.
Las APIs nativas están simuladas y TLS/UDP usan loopback real.
La aceptación física de fluidez, recuperación y consumo sigue pendiente.

Evidencia: docs/validation/input-0.5.16-scroll-position.json. Candidata sin instalar.

REVISIÓN 0.5.15
La 0.5.15 evita enviar dos veces una entrada que coincide con la preparación
de un cruce. Si una tecla, clic, rueda o movimiento ya llegó a las aplicaciones
locales antes de terminar la captura, cancela ese cruce y conserva su destino
local. Restaura también la posición física del cursor en ambos sistemas.
La entrada que ya estaba capturada sigue hacia el compañero; un cursor quieto
no cancela el cruce. Conserva el canal y permite volver a cruzar, sin añadir
esperas, sondeos, hilos ni consultas nativas al recorrido del movimiento.
También despierta la comprobación de limpieza después de liberar el callback
nativo si quedó una liberación fallida; evita aparcarla hasta el siguiente
intervalo de reposo. Este aviso adicional sólo se emite si queda limpieza pendiente.

La validación de 0.5.15 registró 1.593 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
48 casos y pasan 200 pruebas focalizadas. Se reprodujeron 24 fallos previos:
16 al preparar la captura y ocho al completar una liberación fallida.
Se comprueban teclas, clics, rueda y movimiento durante la preparación del cruce,
antes y después de la captura, en ambos sentidos y con TLS o transporte híbrido.
Se conserva el destino de cada entrada, la posición local, el canal y el siguiente
cruce. Las APIs de entrada están simuladas y TLS/UDP usan loopback.
La aceptación física de fluidez, recuperación y consumo sigue pendiente.

Evidencia: docs/validation/input-0.5.15-capture-race.json. Candidata sin instalar.

REVISIÓN 0.5.14
La 0.5.14 evita que un estado antiguo de compañero no disponible cierre
una conexión de entrada que ya se ha autenticado y está preparada. Al recuperar
la disponibilidad, conserva ese canal y sus observadores en Mac y Windows.
La sesión y los permisos locales siguen comprobándose; un fallo local libera
la entrada. Sin un canal preparado, el estado no disponible sigue impidiendo
iniciar la captura o reintentar la conexión. No añade sondeos, hilos ni cambios
de protocolo y conserva la espera pasiva cuando el compañero está ausente.

La validación de 0.5.14 registró 1.545 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
26 casos y pasan 110 pruebas focalizadas. De los 26 casos, 16 fallaban con 0.5.13.
Se comprueba el estado antiguo en cualquiera de los extremos, con el control
local o remoto, en ambos sentidos y con TLS o transporte híbrido. Se conservan
las teclas, el arrastre, el canal y los observadores; la indisponibilidad local
sigue liberando ambos dispositivos. Las APIs de entrada están simuladas y TLS/UDP
usan loopback. La aceptación física de fluidez, recuperación y consumo sigue pendiente.

Evidencia: docs/validation/input-0.5.14-readiness.json. Candidata sin instalar.

REVISIÓN 0.5.13
La 0.5.13 conserva la posición del ratón local cuando recupera el control
durante un arrastre remoto. Si había un movimiento remoto en curso, primero
suelta sus botones y modificadores y después restaura la posición física.
Si alguna liberación falla, conserva su registro para recuperarla y evita
recolocar el cursor mientras quede pendiente. Se aplica en Mac y Windows.
También corrige el historial de clics del Mac: un clic local intermedio rompe
la secuencia remota anterior, y un doble o triple clic local conserva el contador
que entrega macOS. No añade sondeos, hilos ni consultas de pantalla por movimiento.

La validación de 0.5.13 registró 1.519 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
70 casos y pasan 293 pruebas focalizadas. De 56 casos comparados con 0.5.12,
44 fallaban antes y 12 ya pasaban. Otros 12 casos comprueban fallos de preparación,
restauración y liberación. Se prueba movimiento, clic y rueda locales durante
un arrastre remoto, incluso con un movimiento remoto en curso, en ambos sentidos
y con TLS o transporte híbrido. También se verifica que teclas y movimiento
usan la misma cola al recuperar el teclado. Las APIs de entrada están simuladas y TLS/UDP
usan loopback. La aceptación física de fluidez, recuperación y consumo sigue pendiente.

Evidencia: docs/validation/input-0.5.13-local-pointer.json. Candidata sin instalar.

REVISIÓN 0.5.12
La 0.5.12 corrige la precisión del cursor en Windows. Cada posición apunta
al centro del rango que representa su píxel: algunas coordenadas anteriores
caían en el píxel vecino. Al recuperar control local, el primer desplazamiento
parte de la posición física redondeada y evita un pequeño salto. El punto donde
se mantiene el cursor mientras controla el otro equipo también usa un píxel
exacto en pantallas de dimensiones impares. No añade consultas de pantalla por
movimiento. Mac conserva sus coordenadas fraccionarias y ambos sistemas
comparten la misma edición y las pruebas de entrada en ambos sentidos.

La validación de 0.5.12 registró 1.449 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
90 casos y pasan 231 pruebas focalizadas. De las 26 regresiones de precisión,
22 fallaban antes y cuatro ya protegían el comportamiento del Mac. El banco
recorre 53.000 posiciones de ocho geometrías: pasa de 453 coordenadas incorrectas
a ninguna en el modelo de rangos de píxel de Windows. Otros 64 casos mezclan
19.200 operaciones de movimiento, teclas, botones y rueda con ambos sentidos,
TLS e híbrido, y un receptor inmediato o con coste simulado de 1 ms.
Las APIs de entrada están simuladas y TLS/UDP usan loopback. La aceptación
física de fluidez, recuperación y consumo sigue pendiente.

Evidencia: docs/validation/input-0.5.12-pointer-precision.json. Candidata sin instalar.

REVISIÓN 0.5.11
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

La validación de 0.5.11 registró 1.359 pruebas por plataforma, Ruff y ambas compilaciones. Se reprodujeron
diez fallos antes de corregirlos; hay 30 casos nuevos y 333 pruebas focalizadas.
El banco recorre 36 escenarios y conserva las 144 acciones, con duplicados,
desorden, alteraciones, pérdidas y pausas. Tras una pausa inicial de 150 ms,
una retención posterior de TLS causa huecos de 81–89 ms antes y 1,3–9,7 ms
después en Mac. Son medidas de loopback con entrada sintética; la pausa inicial
permanece y no se mide la latencia física. La aceptación física sigue pendiente.

Evidencia: docs/validation/input-0.5.11-path-recovery.json. Candidata sin instalar.

REVISIÓN 0.5.10
La 0.5.10 recupera también las desconexiones del ratón. Cuando el sistema
anuncia su llegada o retirada, libera los botones cuyo evento de soltar pudo
perderse y conserva el teclado, el control remoto y el enlace. Windows renueva
sólo el capturador del dispositivo afectado; Mac observa teclado y ratón por
separado. Los avisos tardíos de una sesión cerrada se descartan. No añade
búsquedas periódicas ni hilos. Un cambio de ratón en el receptor no interrumpe
un arrastre enviado desde el otro equipo.

La validación de 0.5.10 registró 1.329 pruebas por plataforma, Ruff y ambas compilaciones. Hay 40 casos
nuevos: recuperación de los cinco botones, conservación del teclado y del control,
avisos en cola al terminar un cruce, aislamiento del receptor y vida de los
observadores. Las 233 pruebas focalizadas usan APIs de entrada simuladas y
TLS/UDP reales en loopback. También se comprueba el registro y cierre nativos
de los observadores pasivos, sin abrir HID ni leer informes. La aceptación
física sigue pendiente.

Evidencia: docs/validation/input-0.5.10-mouse-recovery.json. Candidata sin instalar.

REVISIÓN 0.5.9
La 0.5.9 conserva la prioridad del uso local cuando coincide con una llegada
remota. Una tecla, clic, rueda o movimiento que quedó en cola durante la
aceptación cancela esa llegada al procesarse. El cursor quieto no la cancela.
Las teclas remotas pendientes no se reproducen después de recuperar el control;
el enlace permanece abierto y permite el siguiente cruce. Se aplica en ambos
sentidos, sin añadir esperas ni consultas nativas al recorrido de entrada.

La validación de 0.5.9 registró 1.289 pruebas por plataforma, Ruff y ambas compilaciones. Los 20 casos
nuevos simulan la carrera durante la aceptación: 16 reproducían pérdida de
prioridad local y cuatro comprueban que un cursor quieto no interrumpe el cruce.
También verifican que no se reproduzcan teclas remotas pendientes y que el
siguiente cruce funcione sobre el mismo enlace. Las 78 pruebas focalizadas usan
APIs simuladas y TLS/UDP reales en loopback. La aceptación física sigue pendiente.

Evidencia: docs/validation/input-0.5.9-takeover.json. Candidata sin instalar.

REVISIÓN 0.5.8
La 0.5.8 evita que el destino reclame al otro equipo con un pequeño movimiento
junto al borde después de recibir control remoto. Cada nuevo foco descarta la
habilitación anterior del borde; un movimiento local nuevo hacia el interior
lo vuelve a habilitar. Se aplica en ambos sentidos y también tras cancelar con
un teclado o ratón local. Si el movimiento físico ya entra en el interior,
permite cruzar en el siguiente movimiento, sin otro paso previo. En Mac, el primer
movimiento local usa la última posición recibida, igual que Windows, sin calcular
un salto desde una posición antigua.

La validación de 0.5.8 registró 1.269 pruebas por plataforma, Ruff y ambas compilaciones. Los 16 casos nuevos
cubren llegada, regreso, cancelación local y el primer desplazamiento en ambos
adaptadores, con y sin notificación del evento generado. También comprueban la
inversión inmediata tras recuperar control moviendo físicamente hacia el interior.
Catorce fallaban antes de sus respectivas correcciones.
Las 157 pruebas focalizadas usan APIs simuladas y TLS/UDP reales en loopback;
conservan además los 64 ciclos de ida y vuelta rápida. La aceptación física sigue pendiente.

Evidencia: docs/validation/input-0.5.8-arrival.json. Candidata sin instalar.

REVISIÓN 0.5.7
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

La validación de 0.5.7 registró 1.253 pruebas por plataforma, Ruff y ambas compilaciones. Las 201 pruebas
centradas en estos cambios incluyen 64 ciclos de ida y vuelta rápida, con las
APIs de ambos sistemas simuladas y TLS/UDP reales en loopback. Se reprodujeron
21 casos de fallo antes de corregirlos. La prueba física de fluidez y estabilidad
sigue pendiente; estos resultados no miden la latencia del ratón por Wi-Fi.

Evidencia: docs/validation/input-0.5.7-crossing.json. Candidata sin instalar.

REVISIÓN 0.5.6
La 0.5.6 evita reproducir posiciones antiguas cuando llegan varios movimientos
juntos. Autentica todo el lote y aplica la posición más reciente entre cada clic,
tecla o cambio de foco. No añade espera para agrupar. Los mensajes alterados,
repetidos o antiguos no sustituyen una posición válida; cancelar el control
descarta los movimientos pendientes. Se aplica igual en ambos sentidos.

En el banco local de Mac, una ráfaga de 64 movimientos con un coste simulado
de 1 ms por aplicación pasa de unos 86 ms a 1,5 ms hasta la posición final.
Son medianas de recuperación del receptor en loopback, no latencia física.
La ráfaga necesita una aplicación y una confirmación, frente a 64 antes.
El banco recorre 54 casos por versión, con 1/16/64 movimientos y tres costes.
Reproducir con benchmarks/input_bursts.py.

La validación de 0.5.6 registró 1.205 pruebas en cada plataforma, Ruff y ambas compilaciones.
Evidencia: docs/validation/input-0.5.6-bursts.json. Sin instalar;
la aceptación con periféricos reales queda para la prueba conjunta.

REVISIÓN 0.5.5
La candidata 0.5.5 integra movimiento cifrado por UDP y mantiene teclas, clics,
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

En 52 casos por plataforma con el servicio real, TLS mutuo y UDP locales, una retención
artificial de 80 ms en TLS produce pausas de movimiento de 81–88 ms con el
transporte fiable y de 1–10 ms con el híbrido, a 125/1.000 Hz y en ambos sentidos.
Las 104 acciones de cada ejecución conservan orden y posición; también se prueba pérdida
artificial de uno de cada diez datagramas. Son medidas locales con entrada
sintética, no latencia de Wi-Fi o de periféricos. Una acción retenida sigue
frenando el movimiento que la sucede para conservar su significado.
Reproducir con benchmarks/input_hybrid.py.

La validación de 0.5.5 registró 1.191 pruebas por plataforma, Ruff y ambas compilaciones. Evidencia:
docs/validation/input-0.5.5-hybrid.json. Conserva las mejoras de recuperación de
teclado, pantallas, permisos y ahorro. La candidata no se instala ahora:
Mac conserva 0.5.1 y el último Windows observado tenía 0.5.0. La aceptación
con periféricos reales en ambos sentidos queda para la prueba conjunta.

HISTORIAL DEL MODO FÍSICO
REVISIÓN 0.4.6: borde de 80 ms; cursor colocado tras la primera prueba nueva del
ratón en destino, antes del teclado y sin recolocarlo al terminar. Las consultas
de seguimiento ya no bloquean el monitor del cursor. El descubrimiento acelera
a intervalos de 50 ms sólo durante la preparación autenticada, con un máximo de
30 s, y vuelve al ritmo normal al terminar; no activa trabajo fuera de casa.
Las lecturas HID ceden a órdenes pendientes y Bluetooth del Mac despierta al
conectarse HID. La espera entre respuestas de ausencia baja a 40 ms durante el
traslado; los fallos de red conservan su espera y todos los plazos siguen limitados.
El diagnóstico incluye elapsed_ms de la última transferencia. Son duraciones
de fases, nunca caracteres ni historial del cursor. Protocolo 2 sin revincular.
En loopback TLS con reconexión simulada de 200 ms por dispositivo, cursor ~223 ms
y pareja ~453 ms, frente a ~685/~687 ms en 0.4.5, sin incluir la permanencia en borde.
No son medidas físicas. Cambiar canales Bluetooth requiere reconectar las radios;
no equivale a una pantalla por cable ni al envío continuo de entrada por red.

REVISIÓN 0.4.5: instalación estable de Windows, conservando los datos y la preferencia
de Inicio. El firewall usa la subred doméstica, no direcciones DHCP individuales,
y sólo TCP 47654/UDP 47655 para la app instalada. Funciona también con perfil Público.
El menú comprueba las reglas al arrancar y permite repararlas expresamente; no las
consulta de forma periódica. Una actualización normal no vuelve a pedir elevación
si las reglas están correctas. Cambiar de router/subred sigue siendo una elección
explícita en Red local; conserva el vínculo y actualiza las reglas con permiso UAC.
Mac admite una identidad de firma existente. Sin ella, la firma ad-hoc puede exigir
renovar permisos del sistema. Configuración y permisos son cosas distintas.
Detalles de instalación y límites: docs/UPDATES.md.

REVISIÓN 0.4.4: reconexión por eventos en Mac y Windows. Arranque, regreso a casa
y despertar producen anuncios autenticados con respuesta directa; después se
confirma el enlace TLS. Los cambios DHCP se recuperan en ambas direcciones.
Al pausar, dormir o cerrar se avisa al compañero cuando el sistema lo permite.
El compañero deja de buscar hasta un aviso de regreso. Un apagado brusco conserva
un máximo de siete intentos y luego espera pasivamente, sin consultas periódicas
al cursor, red ni periféricos. Los traslados incompletos conservan su recuperación
limitada antes de aparcar los lectores. Los anuncios tienen orden y sesión para
rechazar duplicados y avisos anteriores; las respuestas no generan otro eco.
Una conexión sana no difunde anuncios. Windows observa además cambios de IP;
Mac, cambios del servicio DHCP. Ver diagnóstico muestra el estado de ahorro.
690 pruebas pasan en Mac (91,98 %), con UDP/TLS reales locales y hardware simulado.
La aceptación física y compilación Windows siguen pendientes. Para reconectar
tras encender el ordenador, la app debe arrancar al entrar en la sesión; activa
esa opción una vez en ambos equipos cuando se despliegue la candidata conjunta.

REVISIÓN 0.4.3: Mac y Windows comparten las mismas correcciones y versión.
Antes de enviar el ratón se vuelve a comprobar que no se ha pausado/cerrado la
app, bloqueado la sesión ni empezado un arrastre durante la preparación remota.
Una reconexión que conserva el enlace exige también moverse hacia dentro antes
de rearmar el borde y obtiene nuevas comprobaciones del teclado. Los plazos TLS
son totales, no se reinician por cada fragmento recibido. Al cerrar, el lector
conserva el recurso HID hasta terminar; no se ejecutan nuevas escrituras después
del cierre. El menú sigue disponible mientras se reemplaza el enlace.
En Mac, las señales despiertan el bucle nativo incluso en espera pasiva; la prueba
corta de cierre de 0.4.2 no había detectado ese bloqueo tras una espera prolongada.
665 pruebas pasan en Mac (91,36 %); las APIs Windows y dispositivos se simulan.
La compilación Windows y la aceptación física conjunta siguen pendientes.

REVISIÓN 0.4.2: mantiene protocolo 2 y el enlace existente. Una orden de canal
enviada por Bluetooth no se repite por HID si su resultado queda incierto; se
cancela el trabajo pendiente y se comprueba la llegada. El borde también exige
entrar hacia el centro después de reiniciar/reconectar o usar Traer aquí.
Una pulsación breve durante la llegada aplaza la colocación del cursor dentro
del mismo plazo, sin devolver inmediatamente los dispositivos. Traer aquí espera
hasta 8 s, comprueba la llegada aunque se pierda la respuesta y aplica la pausa
de 3 s. Un error de un dispositivo no impide intentar devolver el otro.
Si falla un anuncio UDP, se sigue intentando la conexión TLS configurada.
El cierre por señal despierta también el menú y los lectores en espera pasiva.

INTEGRACIÓN 0.4.1: conserva la coordinación y protocolo 2 de Windows, añade la
protección del borde del Mac, registro local rotativo de fases y recuperación
del aviso al reconectar. El registro flow.log tiene límite de 256 KiB más una
copia, sin pulsaciones, secretos ni historial continuo del cursor.

ACTUALIZACIÓN 0.4.0 DE ESTABILIDAD: requiere esta versión en AMBOS equipos.
Conserva el enlace/certificados; no vuelvas a intercambiar archivos de vinculación.
El menú muestra la versión y avisa si el compañero utiliza el protocolo anterior.
Cierra la app anterior en cada equipo antes de abrir la nueva. Compila la app de
Mac con el código fuente de esta versión y sustituye la .app, manteniendo su ruta.
Revisa Accesibilidad en Mac si el sistema vuelve a pedir permiso tras sustituirla.

El equipo usa un único bloqueo para iniciar o recibir un traslado, desde la
preparación hasta la confirmación/cancelación. Una respuesta perdida se reintenta
dentro del mismo plazo; repetir la preparación no crea una nueva transferencia.
Un ping final fallido deja la confirmación pendiente; no devuelve los dispositivos
inmediatamente. El origen reintenta con pruebas nuevas y conserva plazos limitados.
Después de terminar hay una pausa de 3 s para evitar cambios opuestos accidentales.
El seguimiento alternativo del teclado requiere dos comprobaciones separadas de
su presencia y ausencia del ratón, para no interpretar un ping perdido como cambio.
Los eventos de teclas nativas no reescriben el canal actual ni envían el teclado
por su cuenta cuando el otro equipo no responde. Los botones físicos siguen activos.

Ver diagnóstico muestra versión, estado del enlace y motivos de bloqueo, sin claves,
certificados, contenido tecleado ni historial del cursor. last-transfer.json conserva
sólo versión, última fase y motivo de fallo de la transferencia más reciente.
Los cambios se verifican con dispositivos simulados y TLS real local. La estabilidad
física en este Windows y el Mac requiere comprobar de nuevo ambos equipos actualizados.

Corrección 0.3.2: reconoce también los nombres HID++ completos observados en
los dispositivos originales por Unifying: MX Keys Wireless Keyboard y
Wireless Mouse MX Master 3. Continúa comparando nombres exactos y roles;
un alias editable, MX Keys S o MX Master 3S no autorizan el cambio.
La identidad de vinculación y la caché existentes se conservan.
Al salir se detiene el refresco del icono antes de destruir su menú, evitando
un error observado en la prueba real de cierre del ejecutable de Windows.

Icono de bandeja en Windows y barra de menús en Mac, sin consola ni ventana permanente.
Inicio al entrar en sesión, pausa, estado y opción de traer ambos dispositivos aquí.
Borde derecho de la pantalla principal de Windows -> Mac; izquierdo de Mac -> Windows.
Mantén el cursor 80 ms en el borde. Esquinas (40 px), arrastre, modificadores y sesión
bloqueada inhiben el inicio por borde. El cursor entra a la misma altura relativa,
24 px dentro de la pantalla de destino. Después de un cruce o llegada, el borde
no se rearma hasta entrar al menos 80 px en pantalla con una muestra segura,
sin botones ni modificadores pulsados. Un pequeño movimiento, salir de pantalla
o esperar sin entrar hacia el centro no lo rearman. Instala esta protección en
ambos equipos. Se conservan la pausa de llegada y los plazos de coordinación.
No incluye portapapeles ni archivos compartidos. La entrada continua por red es
un modo opcional separado del cambio físico de canal descrito en esta sección.

Al usar 1/2 del teclado, observa su notificación cuando el firmware la ofrece.
Como alternativa, el equipo que recibe el teclado detecta su presencia con una consulta
HID++ y pide que llegue el ratón. Esta detección puede tardar unos segundos.
NO intercepta ni desvía los botones Easy-Switch en la aplicación de bandeja.
Se permite observación por analytics, y se limpia cualquier desvío temporal anterior.
El canal 3 continúa siendo manual; esta configuración de dos equipos sincroniza 1/2.
No modifica el firmware ni los emparejamientos Bluetooth/USB de los dispositivos.

VINCULACIÓN, UNA SOLA VEZ
Corrección 0.3.1 para Windows: selector IFileDialog en un hilo STA independiente
del icono. Las acciones de vinculación se serializan; Salir continúa disponible.
Cancelar o cerrar la aplicación mientras se elige un archivo no crea un enlace.
Si el selector de Windows no responde, usa Guardar enlace en Descargas (sin selector).
Esta alternativa crea un nombre nuevo en la carpeta Descargas real del usuario;
si ya hay un enlace pendiente, exporta la misma identidad sin sustituirla.
No sustituye un enlace completado. Elimina los archivos de intercambio al terminar.
La actualización conserva las identidades/configuración local existentes y el ahorro.

1. Arranca esta aplicación en ambos equipos. Usa solamente una aplicación que gestione
   los cambios de Logitech a la vez para evitar órdenes en conflicto.
2. En Windows, menú Vincular Windows 1 y Mac 2: guarda CleverSwitch-enlace.json.
   Alternativa: Guardar enlace en Descargas (sin selector), con nombre
   CleverSwitch-enlace-XXXXXXXX.json y aviso de la ruta al terminar.
   Si hay varias interfaces, selecciona primero Wi-Fi/Ethernet del router en Red local.
3. Lleva ese archivo al Mac, por un medio privado. Desde su icono, importa el enlace
   y guarda CleverSwitch-respuesta.json. Lleva la respuesta a Windows e impórtala allí.
   Borra los dos archivos de intercambio al terminar. La invitación contiene un secreto
   de enlace; nunca una clave privada. No compartas esa invitación con terceros.
4. Conecta ambos dispositivos a cada equipo una vez: Windows=1, Mac=2, en ratón y teclado.
   Las aplicaciones recordarán los modelos/capacidades, aunque luego estén en el otro equipo.
5. Comprueba primero los botones 1/2, y después activa Cambio por bordes en AMBOS iconos.
   Inicialmente está desactivado. La preferencia se conserva al reiniciar.
6. Tras comprobar los dos sentidos, activa Iniciar al entrar en la sesión en ambos.
   En Mac deja la aplicación en /Applications antes de hacerlo.

La conexión cifrada se establece automáticamente al arrancar. Tolera desconexiones
y cambios de dirección dentro de la misma subred. Si cambias de router/subred, selecciona
Red local y Actualizar la red del enlace en cada equipo. No se crea una cuenta.
La red debe permitir comunicación local entre equipos: compartir router por sí solo
no evita aislamiento de clientes/invitados. La aplicación no debilita el firewall.
Permite únicamente esta aplicación en la red doméstica cuando el sistema lo solicite.
macOS puede pedir Bluetooth, Red local y Accesibilidad. No se conceden automáticamente.

RECONEXIÓN Y AHORRO, EN AMBOS EQUIPOS
La búsqueda activa queda limitada a 7 intentos durante aproximadamente 2 minutos.
Espera progresiva de 1, 2, 5, 10, 30 y 60 segundos, con una pequeña variación para
evitar que ambos equipos repitan a la vez. Después se detiene: espera pasiva sin
sondeos TCP ni anuncios UDP periódicos, sin consultar el cursor o los dispositivos.
Los lectores HID, la enumeración y las consultas BLE también se aparcan mientras
el compañero esté ausente. El icono deja de actualizarse con un temporizador en espera.

En una conexión sana se comprueba presencia cada 30 segundos; cada traslado obtiene
además su propia confirmación fresca antes de mover dispositivos. Se puede detectar
una desconexión antes si falla una operación. La reconexión no requiere volver a
emparejar Bluetooth ni intercambiar las claves de las aplicaciones otra vez.

La aplicación escucha avisos NATIVOS de cambio de red y reposo/despertar:
Windows: IP Helper y PowrProf. Mac: SystemConfiguration y NSWorkspace.
Al volver a la red, se inicia una búsqueda limitada; un anuncio autenticado del
compañero que regresa despierta al otro, aunque ese equipo no haya cambiado de red.
Un anuncio por sí solo da un intento adicional limitado, no una nueva búsqueda infinita.
El receptor devuelve un único aviso directo para informar de su dirección actual.
Una respuesta no genera otra respuesta; broadcast y unicast llevan el mismo evento.
Al salir o dormir, un aviso de partida permite aparcar inmediatamente al compañero.
Si ese aviso se pierde, se aplican las comprobaciones e intentos limitados habituales.
En reposo, al pausar o fuera de la red reconocida, se suspenden conexión y lectores.
No envía Wake-on-LAN ni instala un servicio que mantenga despierto el ordenador.

Tras la primera conexión TLS válida, recuerda el gateway y su MAC cuando la caché
del sistema los ofrece. Otra red con el mismo rango de IP y distinto router queda
fuera del perfil. Esta huella es una ayuda para ahorrar, no sustituye a los certificados.
No necesita permiso de Ubicación ni leer el nombre de la Wi-Fi. No provoca consultas
ARP: lee las tablas/caché locales. Si DHCP/ARP aún no están listos, hace sólo tres
lecturas locales adicionales (a los 2, 10 y 30 segundos); después queda esperando.
Si no puede reconocer el router, puede escuchar pasivamente; no presume que sea casa.
Al escoger explícitamente Actualizar la red del enlace, conserva la identidad del
compañero y aprende el router nuevo después de una conexión autenticada.

La espera pasiva necesita un EVENTO para despertar: red, despertar del ordenador,
aviso autenticado del compañero o Reconectar ahora en el menú. Si ambos quedaron
esperando tras un problema que el sistema no notificó, usa Reconectar ahora en uno.
Sin ese evento no se puede descubrir mágicamente el regreso sin volver a sondear.
Si los avisos nativos fallan, el menú indica la opción manual; no usa un sondeo infinito.
La recuperación de un traslado incompleto tiene prioridad durante un tiempo acotado.
Un fallo del disco/archivo de recuperación tampoco deja consultas indefinidas.
No se afirma consumo cero: el proceso, los listeners pasivos y el sistema siguen ahí.
El ahorro real y los avisos nativos de Mac quedan pendientes de validar en el Mac M4.

CONFIRMACIÓN Y RECUPERACIÓN
El cambio por borde exige respuesta del otro equipo antes de enviar el ratón.
Envía el ratón primero, confirma una respuesta HID++ nueva en destino y luego el teclado.
La presencia recordada no sirve como prueba de llegada: usa un ping con nonce nuevo.
Se espera hasta 8 s por cada llegada para permitir la reconexión Bluetooth.
Antes de confirmar el final, el destino vuelve a comprobar ambos dispositivos.
Los movimientos por botones físicos pueden haber cambiado ya el teclado de forma nativa;
en esa ruta no se puede prometer retenerlo en origen antes de comprobar el enlace.

Si falla, intenta devolver los dispositivos al origen. El destino mantiene un plazo
independiente de 30 s antes de la confirmación; si el enlace se corta, intenta devolverlos.
Guarda el estado incompleto para intentar devolverlos también después de reiniciar la app.
Una confirmación final perdida se consulta otra vez, evitando deshacer un cambio ya confirmado.
Traer ratón y teclado aquí solicita un regreso y comprueba la llegada en el equipo actual.

La recuperación NO es una garantía física: si el destino se apaga o su aplicación cae,
nadie allí puede enviar la orden de regreso hasta que vuelva. Usa 1/2 en teclado y ratón.
Los botones quedan nativos; no se habilita desvío temporal ni persistente en esta versión.
El filtro identifica modelos, no números de serie. Usa una sola pareja de estos modelos;
el registro del original está indexado por producto y no distingue todas las parejas idénticas.

SEGURIDAD Y DATOS
Control TCP 47654: TLS 1.3, certificados propios, autenticación mutua, certificado exacto
fijado para cada compañero; servidor limitado a la IPv4 privada seleccionada y al IP del peer.
Descubrimiento UDP 47655: mensajes públicos pequeños autenticados con HMAC-SHA256;
pueden actualizar el IP del compañero, nunca emitir órdenes a los dispositivos.
UDP escucha broadcast; se verifican secreto, identidad de enlace, subred, antigüedad y nonce.
TLS verifica después la identidad, incluso si alguien altera un anuncio de red.
Mensajes de control limitados a 2048 bytes, operaciones fijas y tiempos acotados.
No hay comandos remotos, scripts configurables, telemetría ni servidor web de configuración.
No hay dependencias de cuentas, nube, descubrimiento por Internet ni DNS para el enlace.

~/.config/cleverswitch-flow: identidad, clave privada local, enlace y estado de recuperación.
También contiene last-transfer.json, diagnóstico local de la última transferencia.
El enlace también recuerda la huella local del router; no la transmite al compañero.
Permisos por usuario (0600/0700 en Mac; DACL de propietario + SYSTEM en Windows).
~/.config/cleverswitch-tray: caché de modelos/capacidades y bloqueo de instancia.
No guarda las teclas/letras que escribes, ni el historial de posiciones del cursor.
Sin notificaciones en cada cambio; consulta el estado en el menú cuando lo necesites.
El modo --preview no inicia HID, conexiones de red ni modificaciones de Inicio.

WINDOWS
CleverSwitchTray.exe: Windows x64, sin firma comercial. Compilar esta candidata allí.
Prueba de icono sin acceso a dispositivos: CleverSwitchTray.exe --preview --preview-seconds 15
Desde PowerShell sin administrador, con la app cerrada: .\dist\Install-CleverSwitch.ps1
El paquete contiene la app, Install-CleverSwitch.ps1, Configure-CleverSwitchFirewall.ps1
y cleverswitch-package.json. Verifica todos los hashes antes de sustituir archivos;
si una copia falla, restaura la anterior. Los hashes comprueban integridad, no autoría.
Instala en %LOCALAPPDATA%\Programs\CleverSwitchFlow y abre la app sin elevarla.
Conserva Inicio: desactivado sigue desactivado; si estaba activo desde una carpeta
anterior, actualiza HKCU Run. No modifica StartupApproved ni la configuración privada.
Sólo el asistente de firewall pide UAC si faltan reglas o cambia la subred del enlace.
Con reglas correctas, instalar otra versión no requiere reconfigurar el firewall.
No cambia PATH, la clasificación de la red ni políticas de ejecución.
No se desactiva Defender ni se eliminan marcas de seguridad del sistema.

MAC M4 PRO
La 0.4.4 está compilada, instalada y arrancada en Aplicaciones. Tras 130 segundos
sin intervención, el cierre nativo terminó en 0.32 s; el ejecutable
coincide con los cuatro módulos de producción probados. La vinculación, caché
y configuración de Inicio se conservaron. La aceptación física sigue pendiente.

La validación histórica de la .app 0.4.3 se hizo en macOS 26.6.2 ARM64; su vista previa funcionó y
el cierre nativo tras 130 s de espera terminó en 0,33 s. Se instaló y arrancó
en /Applications/CleverSwitchTray.app;
la firma nueva requiere renovar manualmente Bluetooth y Accesibilidad. La
estabilidad con los periféricos físicos y ambos equipos actualizados sigue pendiente.
Para compilar en el Mac, usa Python ARM64 3.12 o superior, sin Rosetta, y HIDAPI:
  brew install hidapi
Desde la carpeta source descomprimida:
Puedes ejecutar en Terminal: bash Compilar-Mac.command
Compila localmente con Python ARM64; no sustituye la app ni modifica Inicio/permisos.
O realiza los pasos manuales:
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements-tray.txt
  .venv/bin/python build_tray.py
Resultado: dist/CleverSwitchTray.app. Ponla en /Applications y arráncala.
LSUIElement evita un icono de Dock. Firma ad-hoc; sin Developer ID ni notarización.
No se retira cuarentena ni se desactiva Gatekeeper. Inicio utiliza un LaunchAgent de usuario
sin KeepAlive, de modo que Salir sigue cerrado hasta el próximo inicio de sesión.
La posición exacta en la zona derecha de la barra la decide macOS.

VERIFICACIÓN
Suite original adaptada, pruebas nuevas de TLS entre dos peers reales en loopback,
rechazo de certificado ajeno, límites/replay, secuencia de llegada, rollback por fallo,
plazo independiente, reinicio incompleto, ACK final perdido, bordes y controles de menú.
En 0.4 se prueban pérdidas de cada ACK, pérdida transitoria de presencia al confirmar,
exclusión de traslados cruzados, versiones mezcladas, bloqueo liberado ante fallos de disco,
ausencias transitorias frente a cambio de teclado y transferencias repetidas con TLS real.
Los dispositivos y las llamadas gráficas de Mac se simulan en esas pruebas.
La vista previa compilada sólo comprueba el arranque/cierre del icono en Windows.
FALTA probar el cambio físico Windows <-> Mac M4 Pro, permisos de macOS 26.6.2,
reposo y bloqueo reales, firewall/router, y comportamiento HID++ de estos firmwares.
No se presenta el programa como listo para uso diario hasta completar esas pruebas.

Código fuente y licencia acompañan al ejecutable. build_tray.py sólo compila; no instala.
El punto de entrada de esta edición es tray_main.py. El antiguo CLI de CleverSwitch se
conserva como código de referencia/pruebas; su política de botones no es esta edición.
