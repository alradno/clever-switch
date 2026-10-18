# CleverSwitch Flow local

Prototipo para coordinar un **MX Keys original y un MX Master 3** entre Windows
(canal 1, pantalla izquierda) y Mac (canal 2, pantalla derecha).

Versión candidata conjunta: **0.5.24 experimental**, protocolo de control **2**.
Los paquetes de Mac y Windows incorporan las mismas fuentes de aplicación;
las correcciones compartidas se prueban en ambos sentidos.
Ambos equipos deben ejecutar protocolo 2 para utilizar el traslado coordinado.
Actualizar la app conserva
el enlace y los emparejamientos Bluetooth/USB existentes.

## Estado y coordinación

La candidata 0.5.0 introdujo entrada continua por LAN, desactivada por defecto. Los
periféricos permanecen conectados a un equipo y los eventos cruzan por una conexión
TLS persistente. Cada equipo conserva sus aplicaciones y su distribución de teclado.
El menú «Ratón y teclado por red (experimental)» debe activarse expresamente en ambos.
Este modo suspende el traslado Bluetooth por bordes y el seguimiento HID; conserva
la vinculación, las reglas de firewall y los botones físicos Easy-Switch.

La 0.5.24 evita reproducir posiciones antiguas después de una pausa aunque
entre ellas haya mensajes de mantenimiento del enlace. Conserva las fronteras
de teclas, botones, rueda y foco. Calcula la espera siguiente con el reloj actual,
sin añadir de nuevo el tiempo ya consumido al procesar la cola.
También corrige el reloj de los clics en Mac: un evento Quartz recién creado
puede tener fecha cero, por lo que ahora se usa el reloj nativo de actividad.
Windows conserva su conversión con el contador de milisegundos del sistema.

Añade medidas agregadas de captura, cola, recepción, entrega y permisos para
localizar pausas. Las mediciones ceden ante la entrada: si su bloqueo está
ocupado, se omite la muestra. No guardan teclas, posiciones ni eventos individuales,
y no añaden sondeos, hilos ni esperas. El movimiento pendiente se sustituye por
el más reciente cuando es seguro; los clics y modificadores conservan su orden.

Pasan 2.094 pruebas en Mac, Ruff y la compilación ARM64 con firma, fuentes y
vista previa comprobadas. La validación nativa de Windows está pendiente.
Se reproducen 24 fallos con la fuente anterior y se conservan 11 controles.
Las APIs nativas están simuladas y TLS/UDP usan loopback real; estas pruebas
no demuestran fluidez física ni la causa del vídeo.
Se acumulan las mejoras sin instalar esta candidata. El 18/10/2026 se comprobó
0.5.23 instalada en Mac y disponible en Windows mediante el enlace autenticado.

Véase la [investigación de pausas](docs/validation/input-0.5.24-cursor-stutter.json).

La 0.5.23 conserva el enlace al recuperar una ráfaga de entrada pendiente.
Amplía la cola acotada para cubrir un ratón de 1.000 informes por segundo
dentro del plazo de actividad existente. El trabajo se procesa por lotes que
ceden turno a la red y continúan sin esperar al siguiente temporizador.
Conserva el recorrido del cursor, la posición de clics y rueda, el orden de
las teclas y la prioridad de la entrada que ya llegó a las aplicaciones locales.
Mac y Windows comparten la corrección; no añade sondeos ni alarga los plazos.

Pasan 2.019 pruebas por plataforma, Ruff y ambas compilaciones. Las 34 pruebas
nuevas reproducen 26 fallos con la fuente anterior y conservan ocho controles.
Incluyen ambos sentidos, TLS e híbrido, entrada a 125/1.000 informes por segundo,
trayectorias recortadas, acciones intercaladas y exceso de cola con liberación.
Las APIs nativas están simuladas y TLS/UDP usan loopback real. La pausa impuesta
sigue existiendo; la fluidez física y la causa de los microcortes domésticos
siguen pendientes de la prueba conjunta. Candidata preparada sin instalar.

Véase la [validación de ráfagas](docs/validation/input-0.5.23-callback-bursts.json).

La 0.5.22 conserva el tiempo de las pulsaciones del ratón al enviarlas entre
Mac y Windows. Una pausa de red o del receptor ya no comprime dos clics
separados ni deshace un doble clic en los casos simulados. Incluye los clics
que esperan la aceptación del cruce y las liberaciones al reconectar el ratón.
La extensión se negocia con el compañero; una versión anterior conserva su
formato. Aprovecha la confirmación existente y no añade otra espera al cruce,
sondeos periódicos ni trabajo de reloj por cada movimiento.

Pasan 1.985 pruebas por plataforma, Ruff y ambas compilaciones. Hay 69 casos
nuevos y ocho fallos de interpretación de clics reproducidos antes de corregirlos.
Las APIs nativas están simuladas y TLS/UDP usan loopback real; el modelo de
Windows comprueba el tiempo entregado a su gestor de ventanas. La aceptación
física de fluidez, doble clic, Easy-Switch y consumo sigue pendiente.

Véase la [validación de clics](docs/validation/input-0.5.22-click-timing.json).

La validación adicional de 0.5.21 añade 40 escenarios por plataforma de
entrada sostenida a 125 y 1.000 informes por segundo, con receptores lentos
y pausas de 40 ms. Verifica 19.800 movimientos, 720 acciones ordenadas y
60 reconexiones de teclado por plataforma, además de las liberaciones y el
siguiente cruce. Pasan 1.916 pruebas en Mac y Windows. No aparece otro fallo
de aplicación: las fuentes y los paquetes de la candidata siguen iguales.
Las APIs de entrada son simuladas y TLS/UDP usan loopback real; la fluidez y
recuperación físicas siguen pendientes de la prueba conjunta.
Véase la [validación de flujo sostenido](docs/validation/input-0.5.21-sustained.json).

La 0.5.21 recupera la entrada si el sistema deja de informar de pantallas
durante un cambio de configuración. Mac y Windows detienen la comprobación de
entrada en segundo plano aunque falle la restauración del cursor, conservan
el cierre pendiente y lo completan cuando vuelve una pantalla válida.
La suspensión mantiene la espera de disponibilidad existente y el siguiente
cruce vuelve a registrar sus capturadores. No añade búsquedas periódicas.

La 0.5.20 evita un cruce involuntario después de recolocar el cursor en Mac.
Si una aplicación lo mueve sin generar un evento de movimiento, el siguiente
clic o rueda conserva esa posición como origen del próximo desplazamiento.
Así, un movimiento vertical junto al borde no se interpreta como uno horizontal
desde la posición anterior. Windows ya conservaba esa referencia y se comprueba
con los mismos casos. Los eventos propios marcados no alteran la referencia
física. No añade esperas, sondeos, hilos ni consultas nativas.

La 0.5.19 conserva el control local si un cruce quedó en cola y después
llegó una tecla, clic, rueda o movimiento a la aplicación local antes de empezar
la captura. Al procesar esa entrada cancela el cruce que acaba de iniciarse,
sin reenviarla al otro equipo. Conserva la posición local, las liberaciones y
el enlace para el siguiente cruce. Un cursor quieto no lo cancela y los eventos
de un periodo local anterior no afectan a un cruce nuevo. La corrección es común
a Mac y Windows; no añade esperas, sondeos, hilos ni consultas nativas, y conserva
la ruta de los callbacks que se procesan al momento.

La validación ampliada de 0.5.19 añade 24 casos por plataforma: 64 reconexiones
repetidas y ocho cortes de TLS en un solo sentido. Comprueba que se sueltan
teclas y arrastres, se recupera una liberación rechazada temporalmente y funciona
el siguiente cruce. Los trabajadores y observadores de entrada terminan al cerrar
el enlace; sus avisos tardíos no borran pulsaciones de la conexión nueva.
También se comprueba por UDP real que un paquete cifrado de un enlace anterior
se rechaza durante un foco nuevo. Las APIs de entrada siguen simuladas.
Esta ampliación no detectó otro fallo ni cambia el código o los paquetes 0.5.19;
la fluidez y el consumo físicos siguen pendientes.
Véase la [validación de reconexiones](docs/validation/input-0.5.19-reconnect.json).

La 0.5.18 evita dejar una tecla o un arrastre pulsados al iniciar un cruce.
Si la pulsación llegó a la aplicación local antes de empezar la captura,
su repetición y su liberación siguen llegando allí aunque la captura ya haya
empezado. El cruce se cancela y puede repetirse después, sobre el mismo enlace.
Conserva el destino de las nuevas pulsaciones capturadas. La corrección es común
a Mac y Windows y no añade esperas, sondeos, hilos ni consultas nativas.

Se añaden 24 escenarios de red variable por plataforma, con confirmaciones UDP
retrasadas, pérdida del 10 % y retenciones de 40 ms. Conservan posición y orden
de las acciones, sin retrocesos; las pausas impuestas por la red siguen presentes.
Esta ampliación no cambia los paquetes de la app ni prueba la fluidez física.
Véase la [validación de red variable](docs/validation/input-0.5.18-datagram-jitter.json).

La 0.5.17 conserva Bloq Mayús en los cruces rápidos. El nuevo cruce usa el
último estado enviado aunque macOS aún no lo haya aplicado. Evita recuperar
un estado antiguo y alterar las mayúsculas o minúsculas al escribir.
Si falla la creación, preparación o publicación nativa del evento, conserva
el estado anterior. Un cambio físico de Bloq Mayús sigue teniendo prioridad.
Se comprueban cruces iniciados en ambos equipos, con TLS y transporte híbrido.
No añade esperas, sondeos, hilos ni consultas al recorrido del ratón.

La 0.5.16 conserva el destino de la rueda al mover y desplazar enseguida.
En Mac, cada evento de rueda usa la posición ordenada del movimiento anterior,
aunque WindowServer todavía no lo haya aplicado. Evita desplazar la zona donde
estaba antes el cursor. Conserva ambas direcciones de rueda, sus fracciones y
las coordenadas negativas o fraccionarias. Windows mantiene el orden de sus
eventos nativos; ambos sentidos se comprueban con TLS y transporte híbrido.
No añade esperas, consultas del cursor, sondeos, hilos ni cambios de protocolo.

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

La 0.5.14 evita que un estado antiguo de compañero no disponible cierre
una conexión de entrada que ya se ha autenticado y está preparada. Al recuperar
la disponibilidad, conserva ese canal y sus observadores en Mac y Windows.
La sesión y los permisos locales siguen comprobándose; un fallo local libera
la entrada. Sin un canal preparado, el estado no disponible sigue impidiendo
iniciar la captura o reintentar la conexión. No añade sondeos, hilos ni cambios
de protocolo y conserva la espera pasiva cuando el compañero está ausente.

La 0.5.13 conserva la posición del ratón local cuando recupera el control
durante un arrastre remoto. Si había un movimiento remoto en curso, primero
suelta sus botones y modificadores y después restaura la posición física.
Si alguna liberación falla, conserva su registro para recuperarla y evita
recolocar el cursor mientras quede pendiente. Se aplica en Mac y Windows.
También corrige el historial de clics del Mac: un clic local intermedio rompe
la secuencia remota anterior, y un doble o triple clic local conserva el contador
que entrega macOS. No añade sondeos, hilos ni consultas de pantalla por movimiento.

La 0.5.12 corrige la precisión del cursor en Windows. Cada posición apunta
al centro del rango que representa su píxel: algunas coordenadas anteriores
caían en el píxel vecino. Al recuperar control local, el primer desplazamiento
parte de la posición física redondeada y evita un pequeño salto. El punto donde
se mantiene el cursor mientras controla el otro equipo también usa un píxel
exacto en pantallas de dimensiones impares. No añade consultas de pantalla por
movimiento. Mac conserva sus coordenadas fraccionarias y ambos sistemas
comparten la misma edición y las pruebas de entrada en ambos sentidos.

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

La integración corrige además el arranque durante una negociación pendiente y
conserva las acciones ya encoladas al volver al equipo de origen. Mantiene la
recuperación del teclado tras Easy-Switch, los avisos de cambios de pantalla,
la liberación de teclas y botones y la reconexión finita por eventos.

Pasan 1.876 pruebas por plataforma, Ruff y ambas compilaciones. Se añaden
40 casos con los adaptadores de producción y APIs de sistema simuladas:
16 reproducen fallos de cierre y recuperación; 24 verifican cambios de tamaño,
origen y número de pantallas. Pasan 220 pruebas focalizadas. Ambos sentidos
se comprueban con TLS y transporte híbrido reales en loopback. La fluidez,
Easy-Switch, pantallas y consumo físicos siguen pendientes de aceptación conjunta. Mac verifica
firma y vista previa; Windows comprueba el ejecutable empaquetado. Evidencia: [validación 0.5.21](docs/validation/input-0.5.21-display-recovery.json).
[Diseño y límites](docs/MOTION-LAB.md); [uso de entrada por red](docs/INPUT-LAN.txt).
La candidata queda preparada para una única prueba conjunta al volver el usuario.
No se instalan versiones intermedias: Aplicaciones conserva 0.5.1; el último
Windows doméstico observado tenía 0.5.0. Quedan pendientes la aceptación física
de fluidez, batería, recuperación del teclado y cambios de monitor en ambos sentidos.

El menú «Comprobar permisos de entrada…» revisa los requisitos sin capturar nada.

### Historial del modo Bluetooth

La 0.4.6 reduce la permanencia en el borde de 400 a 80 ms y coloca el cursor en
cuanto se confirma la llegada del ratón, antes de esperar al teclado. No vuelve
a colocarlo al confirmar la pareja ni al repetir una respuesta perdida. Conserva
las pruebas HID++ nuevas, el rearme a 80 px y la pausa de 3 s contra rebotes.
La comprobación del teclado en segundo plano deja de bloquear el cursor; las
órdenes HID tienen prioridad sobre nuevas lecturas en espera. El reconocimiento
se acelera sólo durante un traslado autorizado y vuelve al ritmo normal al acabar.
Bluetooth del Mac despierta su suscripción al conectar HID, sin esperar otro sondeo.

Con TLS real local y radios simuladas que tardan 200 ms en reconectar, la mediana
para colocar el cursor baja de unos 685 a 223 ms, y la pareja completa de 687 a
453 ms, sin contar el borde. Son 20 cruces por sentido y versión; no son medidas
de Bluetooth físico. Se puede repetir con `benchmarks/handoff.py`.
La reconexión Bluetooth y la pausa contra rebotes impiden prometer una experiencia
idéntica a una pantalla por cable. No se reenvían pulsaciones ni movimiento por red.
El diagnóstico conserva tiempos acumulados de la última transferencia, sin entradas.
Pasan 738 pruebas (92,12 %) y Ruff. La app 0.4.6 está instalada y arrancada
en Aplicaciones; la firma, vista previa y fuentes incluidos están comprobados.
Tras 130 s inactiva cierra en 0,33 s; vínculo, caché e Inicio se conservan.
La firma ad-hoc puede exigir renovar permisos. Windows no respondió a la prueba
de enlace: la compilación nativa Windows y aceptación física conjunta siguen pendientes.
Véase [evidencia de rendimiento](docs/validation/macos-0.4.6.json).

La 0.4.5 añade instalación y actualización de Windows en una ruta fija por usuario.
Conserva la vinculación, los datos de dispositivos y la preferencia de Inicio. Las
reglas de firewall usan la subred doméstica guardada, con TCP 47654 y UDP 47655,
para admitir cambios DHCP de ambos equipos incluso si Windows marca la red Pública.
No abre otras subredes ni elimina reglas de bloqueo. El menú comprueba la instalación
una vez al arrancar y permite revisarla; no añade sondeos periódicos.

El compilador de Mac admite seleccionar una identidad de firma existente y falla
si no está disponible. Este Mac no tiene una identidad válida: sigue usando firma
ad-hoc, que puede exigir renovar permisos aunque los datos se conserven.
Véase [actualizaciones y firewall](docs/UPDATES.md) y
[evidencia de 0.4.5](docs/validation/macos-0.4.5.json). La aceptación física y la
compilación nativa Windows de esta candidata siguen pendientes.
La validación histórica de 0.4.5 registró 723 pruebas (92,16 %) y Ruff correctos.
Tras 130 s de espera, la app instalada cerró en 0,33 s. El ejecutable contiene los
módulos probados y conserva identidad, enlace, caché e Inicio.

La revisión 0.4.4 refuerza la reconexión automática por eventos en ambos equipos.
Al arrancar, despertar o volver a casa, el equipo anuncia que está disponible y
el otro responde una vez, permitiendo recuperar también direcciones DHCP distintas.
La conexión se confirma por TLS antes de reanudar el trabajo. Al pausar, dormir o
cerrar se envía un aviso breve cuando la red y el sistema lo permiten; el compañero
pasa a espera pasiva. Un apagado brusco sigue teniendo intentos limitados.

Durante la ausencia no hay búsquedas periódicas, consultas al cursor ni sondeos
HID/BLE. Una conexión sana ya no emite anuncios periódicos de descubrimiento.
Los avisos duplicados o antiguos y los callbacks de conexiones cerradas se descartan.
Windows observa también cambios de dirección IP; Mac observa cambios del servicio
DHCP. El diagnóstico indica si hay búsqueda activa o espera y lectores suspendidos.

Pasan 690 pruebas en Mac, con 91,98 % de cobertura. Incluyen UDP y TLS reales en
loopback, salida/regreso a casa, cambio de IP, aviso de reposo, apagado brusco y
espera simulada durante un día sin trabajo activo. Las APIs Windows y periféricos
están simulados. [Evidencia y límites de 0.4.4](docs/validation/macos-0.4.4.json).
Ambas apps deben incorporar esta candidata para disponer de todos los avisos.
La app debe estar abierta: para reconectar después de encender el ordenador,
activa una vez «Iniciar al entrar en la sesión» en ambos equipos. No hay servicio
previo al inicio de sesión ni se despierta un equipo apagado.

La 0.4.4 está compilada, instalada y arrancada en Aplicaciones. Tras 130 segundos
sin intervención, el cierre nativo terminó en 0.32 s; el ejecutable
coincide con los cuatro módulos de producción probados. La vinculación, caché
y configuración de Inicio se conservaron. La aceptación física sigue pendiente.

La revisión anterior 0.4.3 cancela el traslado si se pausa, cierra o bloquea la sesión,
o se empieza a arrastrar, mientras se espera la preparación del compañero. Una
reconexión que conserva los listeners también exige volver hacia el interior de
la pantalla y descarta observaciones antiguas del teclado. Los mensajes TLS
tienen un plazo total, incluso si llegan fragmentados. El cierre conserva el
recurso HID en su hilo lector mientras termina una lectura nativa, y descarta
escrituras pendientes tras cerrar. El menú tolera la sustitución del enlace.
En Mac, las señales de cierre despiertan el bucle nativo: la prueba de una app
que llevaba horas en espera detectó un bloqueo que la prueba corta de 0.4.2
no había cubierto. Se conserva el manejo habitual de señales en Windows y el CLI.

Pasan 665 pruebas en Mac, incluidas las rutas Windows simuladas y los dos sentidos
del traslado, con 91,36 % de cobertura. Se reprodujeron 16 casos de fallo antes de
corregirlos. [Evidencia y límites de 0.4.3](docs/validation/macos-0.4.3.json).
En la validación de 0.4.3, compilada e instalada en Aplicaciones; tras 130 s sin intervención, el cierre
por SIGTERM terminó en 0,33 s. Se comprobó el contenido del ejecutable instalado
contra los fuentes probados. Identidad, enlace, caché e Inicio se conservaron.
La aceptación física conjunta y la compilación nativa Windows siguen pendientes;
no se considera una versión final para uso diario hasta completarlas.

La revisión 0.4.2 corrige fallos reproducidos en la recuperación: evita repetir
órdenes de canal tras perder una respuesta Bluetooth, protege el borde también
al reiniciar y al regresar manualmente, y espera a soltar botones/modificadores
durante la llegada antes de colocar el cursor. La recuperación manual comprueba
la llegada incluso si se pierde su respuesta y permite hasta 8 segundos para
Bluetooth. Un fallo de UDP ya no impide intentar el enlace TLS configurado.
El cierre por señal despierta también los componentes en espera pasiva.
La evidencia y las limitaciones están en [validación 0.4.2](docs/validation/macos-0.4.2.json).

La versión anterior consiguió traslados físicos en ambos sentidos, pero el usuario
observó dispositivos separados, devoluciones inesperadas y fallos intermitentes.
La base 0.4.0 corrige rutas concretas de coordinación y recuperación. La integración
0.4.1 añade las correcciones del Mac: el borde se rearma sólo después de entrar
al menos 80 px en pantalla, diagnóstico rotativo local y limpieza del error al
reconectar. Se restaura también el módulo de vinculación que `.gitignore` había
excluido del primer commit. **Su
estabilidad física con ambos equipos actualizados sigue pendiente de validar.**

En Windows: 615 pruebas, cobertura 91,47 %, Ruff correcto, 12 traslados simulados
alternos con TLS real y 48 respuestas perdidas deliberadamente, tres arranques y
cierres de la vista previa compilada, Defender sin detecciones. Los dispositivos
de esas pruebas están simulados. La app ARM64 no se ha compilado desde Windows.

- [Validación histórica de Mac 0.4.2](docs/validation/macos-0.4.2.json): 646 pruebas,
  cobertura 91,20 %, compilación, firma, vista previa y cierre nativo correctos.
  Instalada y arrancada; permisos y aceptación física pendientes.
- [Validación histórica de Mac 0.4.1](docs/validation/macos-0.4.1.json): 626 pruebas
  sin exclusiones, cobertura 90,66 %, compilación ARM64 y vista previa correctas.
  Instalada en Aplicaciones; permisos de la nueva firma y prueba física pendientes.
- [Continuar en Windows](docs/WINDOWS-HANDOFF.md): actualizar desde este mismo repositorio.
- [Traspaso para Codex en el Mac](docs/MAC-HANDOFF.md): contexto, compilación,
  comprobaciones físicas y límites de la evidencia.
- [Instrucciones de trabajo](AGENTS.md): conservación de datos y límites del diseño.
- [Guía completa de esta edición](README-FLOW.txt): vinculación, energía y recuperación.
- [Resultado de verificación de Windows](docs/validation/windows-0.4.0.json).

Antes de trabajar en el otro equipo, ejecuta `git pull --ff-only`. Si hay cambios
locales, revísalos y consérvalos antes de integrar. Publica las correcciones del Mac
en este mismo repositorio para poder incorporarlas después en Windows.

## Mac M4 Pro

Requiere Python ARM64 nativo 3.12 o superior e HIDAPI de Homebrew. Desde la raíz:

```bash
brew install hidapi
bash Compilar-Mac.command
```

El script crea `.venv`, instala las dependencias fijadas y genera
`dist/CleverSwitchTray.app`. No instala ni inicia la app, no reemplaza la existente
y no activa el inicio de sesión. Cierra la app anterior, sustituye la de
`/Applications` por la compilada y ábrela. Revisa Accesibilidad, Bluetooth, Red local
y los permisos de acceso a HID que solicite macOS. No se eluden las protecciones
del sistema. La firma es ad-hoc; no hay notarización ni Developer ID.

La validación histórica de 0.4.3 se hizo en macOS 26.6.2 ARM64 y ha superado la vista previa y el
cierre nativo tras 130 s de espera, con firma ad-hoc válida y sin cambiar los datos
de vinculación ni el inicio de sesión. La firma nueva requiere renovar los permisos
guardados. La prueba con periféricos, reposo y permisos queda pendiente.

## Windows x64

Desde PowerShell con Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-lock.txt
Copy-Item .\hidapi.dll .\.venv\Scripts\hidapi.dll
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe build_tray.py
```

Resultado: `dist/CleverSwitchTray.exe`. El DLL incluido es el artefacto oficial
x64 revisado de HIDAPI 0.15.0; [procedencia y licencia](docs/HIDAPI.md).
La compilación incluye también ambos scripts de instalación/firewall y su manifiesto
de hashes. Distribuir esos cuatro archivos juntos. Con la app cerrada, desde
PowerShell **sin administrador**, ejecutar:

```powershell
.\dist\Install-CleverSwitch.ps1
```

Instala en `%LOCALAPPDATA%\Programs\CleverSwitchFlow` y abre la app como el usuario
habitual. Sólo el asistente del firewall pide elevación cuando faltan reglas o cambia
la subred elegida. Una actualización con reglas correctas no la vuelve a pedir.
Inicio desactivado se conserva desactivado; si estaba activado desde una ruta anterior,
se actualiza esa referencia. No copiar la configuración desde el paquete descargado.

Vista previa sin red ni HID:

```powershell
.\dist\CleverSwitchTray.exe --preview --preview-seconds 5
```

El punto de entrada de esta edición es `tray_main.py`. El CLI y los instaladores
heredados de `scripts/` son referencias del upstream, no el procedimiento de esta
edición. Las instrucciones activas son las de este README y `README-FLOW.txt`.

## Datos privados

Las identidades y la configuración de cada equipo permanecen fuera del repositorio:

- `~/.config/cleverswitch-flow/`: identidad, clave privada, enlace, recuperación y
  diagnóstico de la última transferencia.
- `~/.config/cleverswitch-tray/`: caché de dispositivos y bloqueo de instancia.

No subir esas carpetas, archivos de vinculación, secretos ni paquetes que los
contengan. El modo físico intercambia coordinación por LAN cifrada. El modo de
entrada por red transmite acciones por TLS y movimiento por UDP cifrado al equipo
vinculado cuando ambos admiten la vía rápida, sin guardar su contenido ni transmitir
pantalla, portapapeles o archivos.
Los periféricos permanecen conectados por Bluetooth o receptor al equipo de origen.

## Procedencia

Modificación independiente de [MikalaiBarysevich/CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch),
base v1.5.7, commit `de0a4f4f4b996a63bbdf2e368c93eb3843eb2732`.
Licencia [GPL-3.0-or-later](LICENSE.txt). Se conservan los avisos de autoría del
código y la [documentación original](docs/UPSTREAM-README.md), que describe otra
edición. Se omiten sus workflows de publicación AUR/Homebrew y etiquetado: este
repositorio no está configurado para publicar en los repositorios del autor.
