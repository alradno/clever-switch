# Movimiento cifrado integrado en la entrada continua

La 0.5.5 incorporó el transporte a `Service`, `Channel` y `Discovery`.
Los módulos de producción son `input_motion.py`, `input_motion_link.py` e
`input_hybrid.py`. Los archivos equivalentes de `experiments/` los reutilizan;
ya no mantienen una segunda implementación. No se ha instalado la candidata.
La validación utiliza entrada sintética y sockets reales en loopback.

## Recuperación finita de UDP en 0.5.11

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

Pasan 1.359 pruebas por plataforma, Ruff y ambas compilaciones. Se reprodujeron
diez fallos antes de corregirlos; hay 30 casos nuevos y 333 pruebas focalizadas.
El banco recorre 36 escenarios y conserva las 144 acciones, con duplicados,
desorden, alteraciones, pérdidas y pausas. Tras una pausa inicial de 150 ms,
una retención posterior de TLS causa huecos de 81–89 ms antes y 1,3–9,7 ms
después en Mac. Son medidas de loopback con entrada sintética; la pausa inicial
permanece y no se mide la latencia física. La aceptación física sigue pendiente.

La comparación local usa la misma instrumentación contra 0.5.10 y 0.5.11,
a 125/1.000 Hz y en ambos sentidos. El receptor se pausa 150 ms a partir
de 0,2 s; después se retiene TLS durante 80 ms desde 0,6 s. Los intervalos
comparados pertenecen a la ventana 0,5–0,9 s, separada de la pausa inicial.
El atasco posterior no contiene una acción que deba frenar el movimiento.
Las pérdidas totales de 150 ms y de sus confirmaciones iniciales conservan
TLS: sin evidencia nueva no hay recuperación automática de UDP. El banco
comprueba también que no queden plazos de movimiento activos al acabar.

Reproducir con `PYTHONPATH=src python benchmarks/input_path_recovery.py`.
La comparación anterior usa `--expected-path-after-pause reliable` contra
los módulos de producción de 0.5.10. Las cifras no son una garantía sobre
Wi-Fi, pantallas, batería ni periféricos físicos.

Evidencia: [validación 0.5.11](validation/input-0.5.11-path-recovery.json).

## Recuperación del ratón por eventos en 0.5.10

La 0.5.10 recupera también las desconexiones del ratón. Cuando el sistema
anuncia su llegada o retirada, libera los botones cuyo evento de soltar pudo
perderse y conserva el teclado, el control remoto y el enlace. Windows renueva
sólo el capturador del dispositivo afectado; Mac observa teclado y ratón por
separado. Los avisos tardíos de una sesión cerrada se descartan. No añade
búsquedas periódicas ni hilos. Un cambio de ratón en el receptor no interrumpe
un arrastre enviado desde el otro equipo.

Pasan 1.329 pruebas por plataforma, Ruff y ambas compilaciones. Hay 40 casos
nuevos: recuperación de los cinco botones, conservación del teclado y del control,
avisos en cola al terminar un cruce, aislamiento del receptor y vida de los
observadores. Las 233 pruebas focalizadas usan APIs de entrada simuladas y
TLS/UDP reales en loopback. También se comprueba el registro y cierre nativos
de los observadores pasivos, sin abrir HID ni leer informes. La aceptación
física sigue pendiente.

Los 28 casos de recuperación con sockets cubren ambos sentidos, TLS e híbrido.
Se retira el ratón con una tecla y uno de sus cinco botones pulsados: el botón
se libera, la tecla permanece y después vuelven a funcionar clics y movimiento.
También se comprueba que no quede inhibido el cruce local y que el receptor
conserve un arrastre remoto. Dos casos adicionales verifican el aviso en cola
sin foco o tras retirarlo. No se registró una ejecución funcional previa a
esta corrección; estos resultados corresponden a la candidata corregida.

Evidencia: [validación 0.5.10](validation/input-0.5.10-mouse-recovery.json).

## Uso local simultáneo a una llegada en 0.5.9

La 0.5.9 conserva la prioridad del uso local cuando coincide con una llegada
remota. Una tecla, clic, rueda o movimiento que quedó en cola durante la
aceptación cancela esa llegada al procesarse. El cursor quieto no la cancela.
Las teclas remotas pendientes no se reproducen después de recuperar el control;
el enlace permanece abierto y permite el siguiente cruce. Se aplica en ambos
sentidos, sin añadir esperas ni consultas nativas al recorrido de entrada.

Pasan 1.289 pruebas por plataforma, Ruff y ambas compilaciones. Los 20 casos
nuevos simulan la carrera durante la aceptación: 16 reproducían pérdida de
prioridad local y cuatro comprueban que un cursor quieto no interrumpe el cruce.
También verifican que no se reproduzcan teclas remotas pendientes y que el
siguiente cruce funcione sobre el mismo enlace. Las 78 pruebas focalizadas usan
APIs simuladas y TLS/UDP reales en loopback. La aceptación física sigue pendiente.

La prueba detiene la aceptación después de comprobar que no había entrada
local pulsada. Con el bloqueo todavía ocupado, ejecuta un callback físico
local, que queda en cola sin bloquear el hilo nativo. Después permite terminar
la aceptación y comprueba la cancelación, la ausencia de teclas remotas
reproducidas y un nuevo cruce con escritura. Se prueban los dos adaptadores
y los transportes TLS e híbrido. No se activan capturadores reales.

Evidencia: [validación 0.5.9](validation/input-0.5.9-takeover.json).

## Control local después de una llegada en 0.5.8

La 0.5.8 evita que el destino reclame al otro equipo con un pequeño movimiento
junto al borde después de recibir control remoto. Cada nuevo foco descarta la
habilitación anterior del borde; un movimiento local nuevo hacia el interior
lo vuelve a habilitar. Se aplica en ambos sentidos y también tras cancelar con
un teclado o ratón local. Si el movimiento físico ya entra en el interior,
permite cruzar en el siguiente movimiento, sin otro paso previo. En Mac, el primer
movimiento local usa la última posición recibida, igual que Windows, sin calcular
un salto desde una posición antigua.

Pasan 1.269 pruebas por plataforma, Ruff y ambas compilaciones. Los 16 casos nuevos
cubren llegada, regreso, cancelación local y el primer desplazamiento en ambos
adaptadores, con y sin notificación del evento generado. También comprueban la
inversión inmediata tras recuperar control moviendo físicamente hacia el interior.
Catorce fallaban antes de sus respectivas correcciones.
Las 157 pruebas focalizadas usan APIs simuladas y TLS/UDP reales en loopback;
conservan además los 64 ciclos de ida y vuelta rápida. La aceptación física sigue pendiente.

La regresión empieza con el borde del destino habilitado por uso local previo.
Recibe el control remoto y lo termina mediante regreso normal o pulsación local.
Un movimiento de una unidad hasta el borde debe quedarse local; entrar hacia el
interior y cruzar después debe funcionar. Se prueba con los dos orígenes y con
TLS solo o TLS/UDP. Otra prueba comprueba el primer delta tras la última posición
inyectada, tanto si llega su notificación marcada como si no llega.

Evidencia: [validación 0.5.8](validation/input-0.5.8-arrival.json).

## Continuidad del cruce en 0.5.7

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

Pasan 1.253 pruebas por plataforma, Ruff y ambas compilaciones. Las 201 pruebas
centradas en estos cambios incluyen 64 ciclos de ida y vuelta rápida, con las
APIs de ambos sistemas simuladas y TLS/UDP reales en loopback. Se reprodujeron
21 casos de fallo antes de corregirlos. La prueba física de fluidez y estabilidad
sigue pendiente; estos resultados no miden la latencia del ratón por Wi-Fi.

Ejemplo reproducido: remoto de 501 unidades de altura, origen de 1.001. Un
movimiento de 300 horizontal y 150 vertical, iniciado a 100 del borde y altura
200, cruza en y=250. El regreso mapea esa mitad de pantalla a y=500 y conserva
las 200 unidades horizontales y 100 verticales restantes: llega a y=600. Antes
se calculaba y=700 desde el extremo final y se perdía el desplazamiento horizontal.

La regresión concurrente retiene una restauración mientras se ejecuta la comprobación
de salud: la conexión debe seguir viva. El control complementario provoca un fallo
real de restauración y exige cerrar. La comprobación usa un bloqueo sin espera.

Evidencia: [validación 0.5.7](validation/input-0.5.7-crossing.json).

## Recepción de ráfagas en 0.5.6

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

Pasan 1.205 pruebas por plataforma, Ruff y ambas compilaciones. Hay 14 casos
nuevos: dos regresiones de servicio fallaban antes de la corrección y doce
casos adicionales cubren autenticación, orden y cancelación. La cola sigue
limitada a 64; se preservan el orden de sondas y recibos y el mayor serial
dentro de cada frontera. El diagnóstico añade `received_moves_coalesced`
dentro del transporte de movimiento, sin conservar datos de entrada.

El primer CI de Windows detectó una carrera de la fixture compartida: esperaba
que existiera el canal, pero no que ambos trabajadores estuvieran listos.
Se reprodujo reteniendo cada trabajador y se corrigió la espera del banco.
Las aserciones de disponibilidad y de ausencia de sondeo se conservan.

Evidencia de 0.5.6: [validación 0.5.6](validation/input-0.5.6-bursts.json).

## Qué se ha construido

El movimiento usa mensajes independientes cifrados con ChaCha20-Poly1305. Las
claves son efímeras, distintas por sentido y derivadas con HKDF; el identificador
de sesión y el contador también quedan autenticados. Un mensaje duplicado,
alterado, reflejado al emisor o perteneciente a otra conexión se descarta.
Los reintentos usan un contador nuevo y no repiten el nonce del cifrado.

Las teclas, botones, rueda y estado de bloqueos permanecen en una vía fiable y
ordenada. Cada acción incluye la posición exacta que tenía el cursor al emitirla.
Un movimiento posterior lleva el número de las acciones que deben precederlo:
el receptor espera esas acciones antes de aplicarlo y sólo retiene una muestra
pendiente. Así un clic no acaba desplazado porque su movimiento lo adelantó.

Una comprobación cifrada de ida y vuelta debe confirmar primero que la vía rápida
funciona. Hace como máximo tres intentos y termina en 100 ms. Mientras no existe
esa confirmación, el transporte seleccionado es el fiable; la comprobación se
plantea al establecer el enlace, antes de capturar el foco. No se repite en bucle.

Durante el movimiento, la confirmación detiene el trabajo pendiente. Si se pierde
la última muestra, se reenvía a los 8, 24 y 56 ms. Si faltan confirmaciones durante
16 ms, se envía también la posición más reciente por la vía fiable, con su barrera
de acciones. Esas copias terminan al recuperarse las confirmaciones o al pasar
definitivamente a la vía fiable, a los 100 ms. La entrada normal no espera esos
plazos ni añade agrupación. Al quedar inactivo no hay consultas periódicas.

## Qué demuestra el modelo

Se comparan ambos transportes bajo las mismas ventanas de fallo, a 125 y 1.000 Hz,
en ambos sentidos y con/sin acciones: 160 casos. El modelo supone 2 ms de trayecto
y 80 ms de recuperación de una transmisión fiable perdida. Coalesce también
los movimientos del transporte fiable para no exagerar su desventaja.

| Caso a 125 Hz | Vía ordenada | Vía híbrida |
| --- | ---: | ---: |
| Pausa máxima con una transmisión perdida | 88 ms | 16 ms |
| Pausa máxima durante un corte completo de 80 ms | 88 ms | 88 ms |
| Entrega de la última posición si se pierde su transmisión | 82 ms | 10 ms |
| Primera entrega con UDP bloqueado desde el inicio | 2 ms | 2 ms, usando la vía fiable |
| Pausa máxima si UDP deja de funcionar tras confirmarse | 8 ms | 24 ms hasta la recuperación |

Con 1.000 Hz, una pérdida aislada pasa de una pausa de 81 a 2 ms en este modelo.
Las 320 acciones de los casos que las incluyen conservan orden, posición y
liberación. La posición final siempre llega y no queda trabajo pendiente.

Los tiempos son **tiempo simulado**, no mediciones de Wi-Fi, TCP real, pantalla,
Bluetooth o autonomía. No describen la latencia doméstica ni garantizan fluidez
física. Si toda la red deja de entregar datos, ninguna de las dos vías puede
reconstruir movimientos que aún no ha recibido.

La primera fase del laboratorio pasó 72 pruebas, con 96,58 % de cobertura local.
Incluye UDP real en loopback, pérdida/reordenación, alteración de cada byte,
sesión equivocada, claves retiradas, nonce agotado, barreras, foco cancelado,
reintentos finitos y liberación mediante la máquina de sesión existente.
Cuatro regresiones fallaban antes de añadir las copias fiables durante el
plazo de detección de una caída de UDP. La misma suite y los 160 casos pasan en Windows y macOS en [CI](https://github.com/alradno/clever-switch/actions/runs/39084215584). Los resultados del modelo coinciden con la ejecución local.

Evidencia: [validación del laboratorio](validation/motion-lab-2026-10-18.json).

Reproducir desde la raíz con las dependencias de desarrollo:

```sh
python -m pytest experiments -o addopts='' --cov=cleverswitch.flow.input_motion --cov=cleverswitch.flow.input_motion_link --cov-fail-under=95
PYTHONPATH=src python experiments/benchmark_motion.py
```

## Conexión del laboratorio al TLS real

La segunda fase, `experiments/motion_link.py`, intercambia el material efímero
por una conexión TLS real de `Link`, con el certificado del compañero comprobado.
La negociación sólo empieza cuando los dos extremos han seleccionado la capacidad.
Cada conexión tiene un desafío nuevo y claves nuevas; una respuesta de otra
conexión, una identidad distinta o un cierre atrasado no puede reactivar la anterior.
Un fallo o el cierre de TLS retira las claves. No se escriben en disco ni se
modifican los archivos de vinculación.

También se prueba un receptor compartido para los avisos de disponibilidad y el
movimiento. Rechaza mensajes de otra dirección, puerto, subred o sesión. La cola
conserva como máximo 64 datagramas y despierta al consumidor al pasar de cola vacía
a pendiente; el hilo receptor no descifra ni aplica entrada. El dueño del canal recoge los
mensajes y conserva la autenticación criptográfica. La integración reutiliza el
puerto UDP 47655 y el listener existente de la aplicación.

Las pruebas usan TLS mutuo y UDP reales en loopback, en ambos sentidos. Incluyen
reconexión, fallo del certificado, respuesta incompleta, cierre, limpieza de
claves y mezcla de descubrimiento/movimiento en un socket. El servicio de entrada
actual, sin modificar, conserva el modo fiable con un extremo de laboratorio
que no selecciona la capacidad. En esa fase la selección se realizaba mediante un adaptador
de prueba; en 0.5.5 la anuncia y negocia el servicio de producción.

La suite ampliada pasa 138 pruebas locales, con 97,90 % de cobertura conjunta
(100 % del módulo nuevo). Las 138 pruebas pasan también en Windows y macOS,
con 97,96 % de cobertura y sin exclusiones. Los artefactos descargados coinciden
con sus hashes publicados y los 160 casos del modelo mantienen idénticos resultados.
[CI de la negociación](https://github.com/alradno/clever-switch/actions/runs/39085575993);
[evidencia y límites](validation/motion-link-2026-10-18.json).

## Integración 0.5.5 y validación pendiente

El dueño de TLS consume también la cola cifrada de UDP y aplica las acciones
a través de la máquina de foco existente. El protocolo de entrada sigue en 1:
la extensión se selecciona sólo si ambos equipos anuncian su capacidad. Un
compañero anterior conserva el formato fiable original. El mensaje ampliado
lleva acción, secuencia y posición; el decodificador antiguo lo rechaza.

Antes de ACCEPT, los movimientos siguen ENTER por TLS. El servicio no captura
mientras negocia las claves. La salida normal conserva las acciones encoladas;
RESET y cancelación retiran lo atrasado. El cierre del listener UDP pasa a TLS
sin dejar teclas pulsadas. Una reconexión retira claves, cola y datagramas viejos.
No se añade un hilo, puerto ni sondeo periódico al transporte de movimiento.

Las 43 pruebas de `test_input_hybrid.py` recorren el servicio real en ambos
sentidos, junto a las regresiones del laboratorio dentro de la suite completa.
Pasan 1.191 pruebas por plataforma: cobertura 92,56 % en Mac y 92,79 % en Windows. Dos regresiones de salida
normal y dos de arranque fallaban antes de sus correcciones; la evidencia distingue
estos fallos de los ajustes del propio banco de pruebas.

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

Evidencia de código, compilaciones y CI: [validación 0.5.5](validation/input-0.5.5-hybrid.json).
Queda probar las dos apps con periféricos reales, Wi-Fi, cambios físicos de canal,
reposo, DHCP y consumo al volver el usuario. Los resultados del modelo anterior
siguen siendo simulados. Un corte completo de red y una acción fiable retenida
siguen imponiendo una espera: no se promete latencia cero.
