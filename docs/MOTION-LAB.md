# Movimiento cifrado integrado en la entrada continua

La 0.5.5 incorporó el transporte a `Service`, `Channel` y `Discovery`.
Los módulos de producción son `input_motion.py`, `input_motion_link.py` e
`input_hybrid.py`. Los archivos equivalentes de `experiments/` los reutilizan;
ya no mantienen una segunda implementación. No se ha instalado la candidata.
La validación utiliza entrada sintética y sockets reales en loopback.

## Tiempo de clic conservado en 0.5.22

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

La reproducción usa un umbral de doble clic de 200 ms y una pausa de 180 ms
al inyectar el primer o segundo clic. Dos pulsaciones separadas unos 285 ms
se interpretaban como doble clic; dos separadas unos 85 ms se interpretaban
como clics simples. Los ocho casos (ambos sentidos, TLS e híbrido y las dos
pausas) fallaban con la fuente anterior y pasan con la corrección.

El callback fecha los botones antes de diferirlos. El destino añade su tiempo
monotónico a la confirmación ACCEPT existente; el origen resta el momento en
que la recibe para convertir los botones al reloj del destino. La demora del
regreso hace conservadora esa correspondencia y no altera el intervalo entre
clics del mismo foco. Se renueva en cada cruce, sin sincronización periódica.
Los botones que esperan ACCEPT conservan su fecha de captura.

`click-timing-v1` se anuncia y acepta expresamente. Sólo entonces se usan las
tramas de entrada 15 (ACCEPT/botón con tiempo) y 16 (botón con posición y tiempo).
El tiempo es un entero de microsegundos monotónicos. El protocolo de control
sigue siendo 2 y el de entrada 1; el movimiento UDP conserva su formato. Las
versiones anteriores siguen comunicándose con sus tramas originales, aunque
la mejora temporal requiere ambos extremos actualizados.

Mac calcula el contador de clics desde esas fechas y asigna al evento Quartz
su edad respecto de un evento recién creado. Windows convierte esa edad al
contador de milisegundos del sistema y conserva el desbordamiento de DWORD,
evitando el cero que pediría fechar el evento al entregarlo. Ninguno fecha
un evento nativo en el futuro. Movimiento y rueda no consultan estos relojes.
Las liberaciones por cambio de ratón se fechan en el mismo orden del callback.
Un tiempo de botón ausente o decreciente retira el foco y libera su entrada.

Las pruebas nuevas cubren fragmentación, formatos inválidos, negociación y
compatibilidad, relojes con orígenes distintos, espera de ACCEPT, callbacks
en cola, reconexión de ratón, desbordamiento y liberaciones. Se conserva el
enlace y el siguiente cruce. Windows usa un modelo independiente del gestor
de ventanas que lee `MOUSEINPUT.time`; no se han enviado clics a aplicaciones
reales. Las pruebas anteriores de movimiento, orden y recuperación siguen pasando.

La correspondencia de relojes no es una sincronización exacta. Las pausas de
red siguen existiendo. Queda por validar con hardware el doble clic cerca del
umbral, la combinación de clics locales y remotos, la deriva en controles muy
largos y la fluidez. No se atribuyen los microcortes domésticos a esta causa.
La prueba conjunta debe comprobar clic simple, doble clic, selección y arrastre
en ambos sentidos, inmediatamente tras cruzar y después de Easy-Switch.

Referencias de API: [MOUSEINPUT.time](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput),
[efecto sobre el doble clic](https://devblogs.microsoft.com/oldnewthing/20121101-00/?p=6193),
[CGEventTimestamp](https://developer.apple.com/documentation/coregraphics/cgeventtimestamp)
y [contador de clics de Quartz](https://developer.apple.com/documentation/coregraphics/cgeventfield/mouseeventclickstate).
Evidencia: [validación 0.5.22](validation/input-0.5.22-click-timing.json).

## Flujo sostenido y teclado reconectado en 0.5.21

La validación adicional de 0.5.21 añade 40 escenarios por plataforma de
entrada sostenida a 125 y 1.000 informes por segundo, con receptores lentos
y pausas de 40 ms. Verifica 19.800 movimientos, 720 acciones ordenadas y
60 reconexiones de teclado por plataforma, además de las liberaciones y el
siguiente cruce. Pasan 1.916 pruebas en Mac y Windows. No aparece otro fallo
de aplicación: las fuentes y los paquetes de la candidata siguen iguales.
Las APIs de entrada son simuladas y TLS/UDP usan loopback real; la fluidez y
recuperación físicas siguen pendientes de la prueba conjunta.

Cada caso genera movimiento durante 0,6 segundos y mezcla escritura,
arrastre y rueda desde los callbacks de los adaptadores de producción.
Veinte casos desconectan el teclado tres veces con una tecla pulsada,
sin enviar su liberación física, mientras el ratón continúa un arrastre.
La notificación debe liberar sólo el teclado y permitir escribir de nuevo.
Al terminar, se mantienen Shift y un botón pulsados para comprobar que
el regreso los libera y que el enlace admite otro cruce y otra pulsación.

La recepción tiene costes artificiales de 0, 2 o 5 ms por evento; otros
ocho casos añaden una pausa de 40 ms. Todas las acciones conservan orden
y posición, el cursor alcanza el destino final sin retroceder y el enlace
permanece disponible. La agrupación de movimientos ya existente evita
reproducir cada posición atrasada. Las pausas impuestas siguen presentes.

Las observaciones temporales del Mac se toman después del coste simulado
y de que el adaptador termine su llamada a la API falsa. Miden ese recorrido,
sin incluir aplicaciones, pantalla, Wi-Fi o periféricos reales. Son resultados
de diagnóstico; los criterios de aceptación comprueban orden, posición,
liberaciones y continuidad, sin imponer un umbral de latencia al planificador
del equipo de pruebas. No se atribuyen los microcortes domésticos al router.

La ampliación conserva los paquetes ya preparados desde
`e6a76245c472451ccb74132cdc44da2fd4e43a38`. Windows recompila en CI y verifica
el ejecutable sin capturar entrada; la compilación del Mac no se repite porque
sus fuentes no cambian. La aceptación física continúa pendiente, en ambos sentidos.

Reproducir los 40 escenarios y sus observaciones agregadas:
`python -m pytest tests/cleverswitch/test_input_sustained.py --no-cov -q -s`.

Evidencia: [flujo sostenido 0.5.21](validation/input-0.5.21-sustained.json).

## Recuperación tras cambios de pantalla en 0.5.21

La 0.5.21 recupera la entrada si el sistema deja de informar de pantallas
durante un cambio de configuración. Mac y Windows detienen la comprobación de
entrada en segundo plano aunque falle la restauración del cursor, conservan
el cierre pendiente y lo completan cuando vuelve una pantalla válida.
La suspensión mantiene la espera de disponibilidad existente y el siguiente
cruce vuelve a registrar sus capturadores. No añade búsquedas periódicas.

Pasan 1.876 pruebas por plataforma, Ruff y ambas compilaciones. Se añaden
40 casos con los adaptadores de producción y APIs de sistema simuladas:
16 reproducen fallos de cierre y recuperación; 24 verifican cambios de tamaño,
origen y número de pantallas. Pasan 220 pruebas focalizadas. Ambos sentidos
se comprueban con TLS y transporte híbrido reales en loopback. La fluidez,
Easy-Switch, pantallas y consumo físicos siguen pendientes de aceptación conjunta.

El caso fallido retira temporalmente todas las pantallas de la API simulada
con Ctrl y un botón pulsados en el destino. El cierre libera esa entrada y
muestra el cursor, pero no puede restaurar su posición sin una pantalla válida.
Antes, esa excepción dejaba vivo el trabajador de comprobación de disponibilidad;
el servicio olvidaba el cierre y la geometría seguía invalidada aunque volvieran
las pantallas. Ahora conserva el cierre como pendiente, notifica al runtime
y lo termina antes de preparar otro enlace. Una excepción de suspensión mantiene
la espera existente; no introduce un sondeo ni una nueva ventana infinita.

Los 40 casos recorren los lectores de geometría y adaptadores de producción,
con APIs de Windows/Quartz y observadores de pantalla simulados. Veinticuatro
comprueban redimensionado, coordenadas negativas y un monitor adicional,
el cambio en cualquiera de los extremos, liberaciones, avisos tardíos y otro
cruce con movimiento y teclado. Dieciséis comprueban ausencia temporal de
pantallas, cierre del trabajador y recuperación por disponibilidad o negociación.
Los mismos 16 fallan con los fuentes de `e6f3991900cf387e12fef06cacefa2eee601102c`; los otros 24 ya pasan.

La vista previa de Mac y la comprobación del ejecutable de Windows no capturan
entrada. Windows pasa también los bancos existentes de red, ráfagas,
recuperación, UDP variable y precisión. El reinicio del CFRunLoop falso se hace
explícitamente en el banco: no prueba un reinicio nativo de Quartz. La prueba
física conjunta debe incluir conectar y retirar monitores durante el control,
recuperar teclado y ratón y comprobar reposo, fluidez y consumo.

Evidencia: [validación 0.5.21](validation/input-0.5.21-display-recovery.json).

## Posición local después de clics y rueda en 0.5.20

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

La reproducción parte de un movimiento local en el interior y de un clic
o rueda con el cursor ya recolocado en el borde, sin un evento de movimiento
intermedio. El siguiente informe sólo cambia la altura. El adaptador Mac usaba
todavía la posición interior para calcular su delta horizontal y empezaba un
cruce no solicitado. Ahora toma la posición del clic o rueda antes de procesar
ese informe. La captura activa sigue usando su ancla y los eventos marcados
se descartan antes de actualizar las observaciones físicas.

Los 24 casos integrados cubren clic primario, botón adicional y ambas ruedas,
los dos orígenes, TLS/híbrido y el procesamiento inmediato o en cola. Exigen
que ambos equipos sigan locales, que no queden botones pulsados y que el
siguiente cruce y la escritura funcionen sobre el mismo enlace. Seis controles
comprueban que los eventos marcados no reemplacen la posición física.

Con los fuentes de `ac50f8f6673ebb9d318fdbc6889ce3bacd09e14d`, 12 de estos 30 casos fallan: los de origen Mac.
Los 12 del origen Windows y los seis controles de eventos marcados ya pasan.
La corrección cambia sólo el adaptador Mac y la edición compartida; los dos
paquetes incluyen la misma fuente y las mismas regresiones. No se infiere que
ésta sea la causa de los microcortes observados en la red doméstica.

Windows vuelve a pasar sus bancos de red, ráfagas, recuperación, UDP variable
y precisión. La candidata no se instala. La aceptación física debe comprobar
también una recolocación del cursor seguida de clic/rueda y movimiento vertical
junto al borde, en ambos equipos.

Evidencia: [validación 0.5.20](validation/input-0.5.20-pointer-baseline.json).

## Reconexiones y paquetes tardíos en 0.5.19

La validación ampliada de 0.5.19 añade 24 casos por plataforma: 64 reconexiones
repetidas y ocho cortes de TLS en un solo sentido. Comprueba que se sueltan
teclas y arrastres, se recupera una liberación rechazada temporalmente y funciona
el siguiente cruce. Los trabajadores y observadores de entrada terminan al cerrar
el enlace; sus avisos tardíos no borran pulsaciones de la conexión nueva.
También se comprueba por UDP real que un paquete cifrado de un enlace anterior
se rechaza durante un foco nuevo. Las APIs de entrada siguen simuladas.
Esta ampliación no detectó otro fallo ni cambia el código o los paquetes 0.5.19;
la fluidez y el consumo físicos siguen pendientes.

La suite ampliada pasa 1.806 pruebas en Mac y Windows. Los 16 casos de
reconexión alternan el origen físico durante cuatro ciclos cada uno, con TLS
o transporte híbrido y cierre desde cualquiera de los extremos. Mantienen
tres teclas y dos botones pulsados al cerrar, sin enviar sus liberaciones
desde el origen. En 32 de los 64 ciclos, la API simulada rechaza una liberación
de Shift: se conserva para un intento al preparar la conexión siguiente.

Se comprueba el cierre de los trabajadores nativos y de comprobación de entrada,
los observadores y las colas. La conexión nueva reutiliza servicios y adaptadores,
negocia TLS y UDP de nuevo y permite escribir y hacer clic desde el otro origen.
Los callbacks tardíos de los observadores anteriores no alteran esa entrada.
El simulador de CFRunLoop reinicia su señal de parada entre ejecuciones; esto
no prueba el reinicio del bucle nativo real de macOS.

Otros ocho casos retienen una dirección del relé TCP hasta que vence el plazo
del canal; la otra dirección y UDP permanecen disponibles. Tras liberar la
entrada se abre un enlace nuevo. En los cuatro casos híbridos se reenvía un
datagrama cifrado del enlace retirado: se observa su llegada real al listener
y su rechazo, conservando el cursor, el foco y las nuevas pulsaciones.

Los bancos de red, ráfagas, recuperación, UDP variable y precisión también
pasan en Windows. Las fuentes operativas y ambos paquetes de la candidata
0.5.19 conservan sus bytes. No se mide latencia doméstica, fluidez visual o
batería; la aceptación física conjunta sigue pendiente.

Evidencia: [reconexiones 0.5.19](validation/input-0.5.19-reconnect.json).

## Entrada local posterior a un borde en cola en 0.5.19

La 0.5.19 conserva el control local si un cruce quedó en cola y después
llegó una tecla, clic, rueda o movimiento a la aplicación local antes de empezar
la captura. Al procesar esa entrada cancela el cruce que acaba de iniciarse,
sin reenviarla al otro equipo. Conserva la posición local, las liberaciones y
el enlace para el siguiente cruce. Un cursor quieto no lo cancela y los eventos
de un periodo local anterior no afectan a un cruce nuevo. La corrección es común
a Mac y Windows; no añade esperas, sondeos, hilos ni consultas nativas, y conserva
la ruta de los callbacks que se procesan al momento.

Pasan 1.782 pruebas por plataforma, Ruff y ambas compilaciones. Se añaden
22 casos: 16 reproducen el fallo anterior; cuatro conservan el cruce con el cursor
quieto y dos descartan movimiento de un periodo local ya terminado. Pasan 257
pruebas focalizadas. Se comprueban ambos orígenes y TLS/transporte híbrido.
Las APIs nativas y la entrega a aplicaciones están simuladas; TLS/UDP usan
loopback real. La aceptación física de fluidez, recuperación y consumo
sigue pendiente.

La secuencia reproducida mantiene ocupado el bloqueo de sesión, entrega
localmente los movimientos hacia el interior y el borde, y después otra acción.
Al liberar el bloqueo, el borde en cola inicia un foco saliente. La acción
siguiente todavía pertenece al periodo local y no tiene el identificador de
ese foco nuevo: antes se descartaba y dejaba la captura remota activa. Ahora
se reconoce su periodo local y conserva la prioridad de lo ya entregado.

Los 20 casos de cruce recorren tecla, botón, rueda, movimiento y cursor quieto,
en ambos orígenes y con TLS o híbrido. Dos controles adicionales comprueban
que un movimiento de un periodo terminado no cancele un foco saliente posterior.
La comparación carga sólo `input_service.py` del commit
`00f79dfcae77c843c7acce819d7c3736d4532983`, con las mismas pruebas finales:
16 fallan y seis ya pasan. El canal conserva su disponibilidad y el siguiente
cruce funciona; las pulsaciones y la posición local permanecen coherentes.

Windows vuelve a ejecutar los bancos existentes de red, ráfagas, recuperación,
UDP variable y precisión. Ambos paquetes usan las mismas fuentes de aplicación
y siguen sin instalarse. Esta prueba no atribuye los microcortes al router ni
mide una mejora de latencia física.

Evidencia: [validación 0.5.19](validation/input-0.5.19-queued-edge.json).

## Red variable y pérdidas en 0.5.18

Se amplía la validación sin cambiar el código de la app ni los paquetes 0.5.18.
Pasan 24 escenarios por plataforma, con ambos orígenes, 125/1.000 movimientos
por segundo y con/sin clic y tecla. Se retrasan simultáneamente el movimiento
UDP y sus confirmaciones, desde las primeras sondas de negociación. TLS mantiene
también un retardo ordenado de 2–18 ms. Se comprueba que cada perfil provoca
realmente los retrasos, reordenamientos o pérdidas previstos.

En cada ejecución se verifican 48 acciones con su orden y posición exactos,
cero retrocesos del cursor, las liberaciones y la posición final. Se conserva
el canal y la vía UDP, y la cola queda vacía al terminar. Cuatro controles en
Mac comprueban la ruta habitual con TLS o híbrido, sin la nueva perturbación.
La suite de Windows vuelve a pasar sus 1.760 pruebas, Ruff, compilación y
comprobación del ejecutable sin capturar entrada. Las fuentes y pruebas
unitarias de la app coinciden con las ya verificadas en ambos sistemas.

Pausa máxima observada entre entregas de movimiento; máximos de los ocho
escenarios de cada perfil, sin descontar el coste del planificador:

| Perfil artificial | Ejecución en Mac | Ejecución en Windows |
| --- | ---: | ---: |
| Retardo variable de 2–18 ms | 19.8 ms | 27.3 ms |
| Retardo variable y pérdida de uno de cada diez datagramas | 21.4 ms | 27.8 ms |
| Retención durante una ventana de 40 ms | 41.4 ms | 38.5 ms |

Son medidas de sockets reales en loopback con entrada sintética y un
planificador de prueba. No miden la Wi-Fi doméstica ni permiten comparar
el rendimiento físico de los dos equipos. Las retenciones de red siguen
siendo visibles como pausas; no se demuestra una experiencia de cable.
La aceptación conjunta de fluidez, Easy-Switch, pantallas y consumo sigue pendiente.

Reproducir: `PYTHONPATH=src .venv/bin/python benchmarks/input_datagram_jitter.py`.
El nuevo paso queda incluido en la validación automática de Windows.
Evidencia: [red variable 0.5.18](validation/input-0.5.18-datagram-jitter.json).

## Pulsaciones locales durante la captura en 0.5.18

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

Los 32 casos de cruce pasan la pulsación a las aplicaciones locales, inician
la captura y repiten/sueltan antes de que el trabajador procese la cola.
La versión anterior ocultaba esas liberaciones. La nueva conserva el estado
de las pulsaciones que realmente pasaron por el callback local; los avisos de
dispositivo lo retiran en orden nativo. Ese seguimiento no envuelve movimiento.
Se comprueba que la aplicación local no conserva teclas o botones pulsados,
que el compañero no recibe la pulsación y que funciona el siguiente cruce.

La comparación carga sólo el servicio compartido de `450c5d3e5411d33ca4b49b43e0282b50a4b379f8`
con las mismas regresiones finales. Los 32 casos fallan con esa fuente anterior.
La validación de Windows detectó también defectos del banco de pruebas. La
recuperación enviaba movimiento directamente a la sesión, saltándose la cola
de callbacks de las liberaciones; ahora conserva la misma ruta para todos.
El generador de rueda calculaba algunos deltas desde una posición de sesión
aún pendiente: forzar la cola reproduce ocho fallos con ese generador. Ahora
usa su último informe físico y añade 16 casos con cola forzada. Se mantienen
las posiciones, los eventos y los plazos exigidos. La comprobación de privacidad
requiere una representación opaca, sin buscar cifras que pueden aparecer por
casualidad en la dirección de memoria. El informe conserva los fallos anteriores
y sus límites de atribución.

No se activó captura ni inyección reales. Ambos paquetes usan la misma fuente.

Evidencia: [validación 0.5.18](validation/input-0.5.18-press-ownership.json).

## Bloq Mayús durante cruces rápidos en 0.5.17

La 0.5.17 conserva Bloq Mayús en los cruces rápidos. El nuevo cruce usa el
último estado enviado aunque macOS aún no lo haya aplicado. Evita recuperar
un estado antiguo y alterar las mayúsculas o minúsculas al escribir.
Si falla la creación, preparación o publicación nativa del evento, conserva
el estado anterior. Un cambio físico de Bloq Mayús sigue teniendo prioridad.
Se comprueban cruces iniciados en ambos equipos, con TLS y transporte híbrido.
No añade esperas, sondeos, hilos ni consultas al recorrido del ratón.

Pasan 1.705 pruebas por plataforma, Ruff y ambas compilaciones. Los 26 casos
nuevos reproducen el fallo anterior en la comparación local; también pasan
256 pruebas focalizadas. Se comprueban Bloq Mayús activado y desactivado,
cruces rápidos con eventos nativos pendientes y fallos de preparación del evento.
Las APIs de entrada y su aplicación tardía están simuladas; TLS/UDP usan loopback
real. La aceptación física de fluidez, recuperación y consumo sigue pendiente.

Ocho casos recorren los adaptadores de producción y la conexión real de
loopback: parten de cualquiera de los equipos, cambian Bloq Mayús y cruzan
desde el Mac antes de aplicar sus eventos nativos pendientes. La primera tecla
en Windows conserva el estado esperado y el canal sigue disponible. Dos casos
más sincronizan estados alternos sin esperar a WindowServer.

Otros 16 casos rechazan la creación, preparación o publicación nativa del
evento, con Bloq Mayús inicialmente activado o desactivado y con o sin un
cambio anterior en cola. Conservan el último estado válido. La suite existente
comprueba además la prioridad de cambios físicos y las repeticiones de tecla.

La comparación sustituye únicamente el adaptador Mac por su fuente del commit
`09b041ee2486e61ecead70a581163c3a791c0572`, sin cambiar los casos ni las demás
rutas. Los 26 casos fallan antes. No se activó captura o inyección reales y no
se mide latencia física. Ambos paquetes usan la misma fuente y edición.

Evidencia: [validación 0.5.17](validation/input-0.5.17-caps-order.json).

## Continuidad de teclado y ratón durante reconexiones en 0.5.16

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

La nueva prueba usa ocho semillas deterministas, dos orígenes, dos transportes
y ejecución inmediata o con el bloqueo de sesión ocupado. Cada caso ejecuta
seis ráfagas de 16 operaciones. Las notificaciones recorren los observadores
y adaptadores de producción con APIs falsas; Windows procesa su mensaje de
reconexión y renueva el hook antes del siguiente evento de la prueba.

Tras cada ráfaga compara el estado físico observado por el adaptador, el
registro del emisor y las teclas/botones del receptor. Compara además cada
acción con su posición y orden esperados. Termina soltando los dispositivos,
regresando al control local y escribiendo después de un cruce nuevo sobre el
mismo canal. También comprueba que el caso diferido realmente dejó eventos
en cola y que la pausa controlada no venció su plazo.

Los 64 casos pasan ya con el código operativo publicado en
`4b802ea19fbda557fc7a67008aa8d134b14268a1`. La evidencia amplía la cobertura de
recuperación; no demuestra el comportamiento físico de Easy-Switch ni mide
fluidez de Wi-Fi, pantalla o consumo. Los paquetes se reconstruyen desde el
commit de la validación ampliada y siguen sin instalarse.

Evidencia: [validación ampliada 0.5.16](validation/input-0.5.16-scroll-position.json).

## Destino de la rueda con movimiento nativo pendiente en 0.5.16

La 0.5.16 conserva el destino de la rueda al mover y desplazar enseguida.
En Mac, cada evento de rueda usa la posición ordenada del movimiento anterior,
aunque WindowServer todavía no lo haya aplicado. Evita desplazar la zona donde
estaba antes el cursor. Conserva ambas direcciones de rueda, sus fracciones y
las coordenadas negativas o fraccionarias. Windows mantiene el orden de sus
eventos nativos; ambos sentidos se comprueban con TLS y transporte híbrido.
No añade esperas, consultas del cursor, sondeos, hilos ni cambios de protocolo.

La validación inicial registró 1.615 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
22 casos: 12 reproducían el fallo anterior y 10 controles ya pasaban. También
pasan 186 pruebas focalizadas. Se comprueba cada destino de rueda en eventos
nativos simulados, con movimientos intercalados, ambas direcciones y ráfagas
de 24 desplazamientos. TLS/UDP son reales en loopback; las APIs de entrada y
la aplicación tardía del movimiento por el sistema están simuladas.
La aceptación física de fluidez, recuperación y consumo sigue pendiente.

El transporte ya conservaba la posición exacta de cada acción. Al convertirla
en una rueda de Quartz, faltaba trasladar esa posición al evento nativo. Crear
y publicar un movimiento no implica que WindowServer ya haya actualizado el
cursor: la rueda podía heredar una ubicación anterior. `CGEventSetLocation`
asigna ahora la posición ya conocida antes de publicar la rueda. No consulta
el cursor ni espera a que el sistema termine de aplicar el movimiento.

La prueba conserva el cursor del sistema simulado en una posición anterior
mientras recoge los eventos nativos. Comprueba la ubicación de cada rueda,
además de sus deltas. Recorre ambos orígenes, TLS e híbrido, ambos ejes y lotes
de uno o 24 desplazamientos intercalados con movimiento. Windows decodifica
independientemente las coordenadas absolutas de los registros `SendInput`.
Otros controles cubren coordenadas negativas/fraccionarias, ausencia de una
posición ordenada y fallo al crear el evento. La referencia es 0.5.15
(`0c19c5ac35356614bdc10c17d82c0960bc1cbd26`): 12 fallan y 10 ya pasan.

Los bancos existentes de red, ráfagas, recuperación UDP y precisión vuelven
a pasar en Windows. No se captura ni inyecta entrada real. La prueba física
debe mover entre paneles desplazables y usar inmediatamente ambas ruedas,
en los dos sentidos y con monitores secundarios.

Evidencia: [validación 0.5.16](validation/input-0.5.16-scroll-position.json).

## Entrada local durante la preparación de un cruce en 0.5.15

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

Pasan 1.593 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
48 casos y pasan 200 pruebas focalizadas. Se reprodujeron 24 fallos previos:
16 al preparar la captura y ocho al completar una liberación fallida.
Se comprueban teclas, clics, rueda y movimiento durante la preparación del cruce,
antes y después de la captura, en ambos sentidos y con TLS o transporte híbrido.
Se conserva el destino de cada entrada, la posición local, el canal y el siguiente
cruce. Las APIs de entrada están simuladas y TLS/UDP usan loopback.
La aceptación física de fluidez, recuperación y consumo sigue pendiente.

Un cruce puede ejecutarse en el trabajador de red cuando sus movimientos quedaron
en cola durante una sección de sesión ocupada. El foco ya tenía identificador
antes de terminar la captura nativa. Una entrada que el callback permitió entregar
localmente durante esa ventana se encolaba con el mismo identificador y después
se reenviaba al destino. La cola conserva ahora también si el callback la capturó.
La cancelación sólo se aplica al foco vigente y a actividad real; los avisos
de dispositivos y los callbacks de sesiones retiradas conservan sus límites.

La prueba retiene la preparación de captura antes o después de activar el estado
nativo. Usa los adaptadores de producción con APIs simuladas, los dos orígenes y
TLS/UDP reales en loopback. Comprueba el resultado del callback, el destino de las
acciones, la posición al cancelar, las liberaciones y otro cruce sobre el mismo
canal. Los 24 controles que ya pasaban siguen conservando la entrada capturada
y el cruce cuando el cursor está quieto.

La referencia de captura es 0.5.14 (`acd6825dbdbababcd388d1499ffb606e37171c9e`).
El primer CI de 0.5.15 (`71175351453f8442e0eaf8db6204ee595a3ceef1`) pasó los
40 casos nuevos y detectó un fallo en una prueba anterior de liberación. El
trabajador TLS podía consumir el aviso de cancelación mientras su callback
retenía el bloqueo y aparcar la limpieza al pasar al intervalo de reposo.
Ocho pruebas adicionales reproducen esa secuencia en ambos adaptadores, con
tecla, clic, rueda y movimiento. Conservan el fallo en el registro de liberaciones
hasta resolverlo y exigen que el cierre termine sin esperar al intervalo inactivo.
El transporte híbrido conserva las pruebas de limpieza de la suite completa;
su procesamiento del foco no admite la misma pausa forzada antes de enviar RESET.
La corrección sólo despierta después del desbloqueo cuando queda limpieza pendiente.

Los bancos de red, ráfagas, recuperación UDP y precisión vuelven a pasar en Windows.
No hay captura ni inyección reales. La aceptación con periféricos sigue pendiente.

Evidencia: [validación 0.5.15](validation/input-0.5.15-capture-race.json).

## Disponibilidad actual después de recuperar el enlace en 0.5.14

La 0.5.14 evita que un estado antiguo de compañero no disponible cierre
una conexión de entrada que ya se ha autenticado y está preparada. Al recuperar
la disponibilidad, conserva ese canal y sus observadores en Mac y Windows.
La sesión y los permisos locales siguen comprobándose; un fallo local libera
la entrada. Sin un canal preparado, el estado no disponible sigue impidiendo
iniciar la captura o reintentar la conexión. No añade sondeos, hilos ni cambios
de protocolo y conserva la espera pasiva cuando el compañero está ausente.

Pasan 1.545 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
26 casos y pasan 110 pruebas focalizadas. De los 26 casos, 16 fallaban con 0.5.13.
Se comprueba el estado antiguo en cualquiera de los extremos, con el control
local o remoto, en ambos sentidos y con TLS o transporte híbrido. Se conservan
las teclas, el arrastre, el canal y los observadores; la indisponibilidad local
sigue liberando ambos dispositivos. Las APIs de entrada están simuladas y TLS/UDP
usan loopback. La aceptación física de fluidez, recuperación y consumo sigue pendiente.

El estado del compañero recibido por el canal de control es una instantánea.
Puede describir una sesión o configuración anterior mientras el canal de entrada
ya ha completado su negociación autenticada. El runtime consultaba esa instantánea
antes de reconocer el canal actual y lo suspendía aunque pudiera usarlo.

Las pruebas recorren el runtime y los adaptadores de producción con APIs nativas
simuladas y TLS/UDP reales en loopback. Fuerzan el estado antiguo en cada extremo,
antes o durante un foco remoto, comprueban que Shift y un arrastre permanecen
activos, envían más teclas y movimiento y vuelven al foco local sobre el mismo canal.
También comprueban que una indisponibilidad local actual libera teclas, botones,
captura y visibilidad, y que sin canal preparado no comienza una conexión nueva.

La comparación utiliza 0.5.13 (`5f7af7c2d8f923e467d3fc9a1ad4d6339bdace7e`).
Los bancos de red, ráfagas, recuperación UDP y precisión de píxeles vuelven a pasar
en Windows. No se captura ni inyecta entrada real y no se alteran pantallas,
permisos, protecciones ni ajustes de red. Reposo, DHCP y fluidez física siguen
pendientes de la prueba conjunta con los periféricos y la red doméstica.

Evidencia: [validación 0.5.14](validation/input-0.5.14-readiness.json).

## Posición local y secuencias de clics en 0.5.13

La 0.5.13 conserva la posición del ratón local cuando recupera el control
durante un arrastre remoto. Si había un movimiento remoto en curso, primero
suelta sus botones y modificadores y después restaura la posición física.
Si alguna liberación falla, conserva su registro para recuperarla y evita
recolocar el cursor mientras quede pendiente. Se aplica en Mac y Windows.
También corrige el historial de clics del Mac: un clic local intermedio rompe
la secuencia remota anterior, y un doble o triple clic local conserva el contador
que entrega macOS. No añade sondeos, hilos ni consultas de pantalla por movimiento.

Pasan 1.519 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
70 casos y pasan 293 pruebas focalizadas. De 56 casos comparados con 0.5.12,
44 fallaban antes y 12 ya pasaban. Otros 12 casos comprueban fallos de preparación,
restauración y liberación. Se prueba movimiento, clic y rueda locales durante
un arrastre remoto, incluso con un movimiento remoto en curso, en ambos sentidos
y con TLS o transporte híbrido. También se verifica que teclas y movimiento
usan la misma cola al recuperar el teclado. Las APIs de entrada están simuladas y TLS/UDP
usan loopback. La aceptación física de fluidez, recuperación y consumo sigue pendiente.

La comparación usa `test_input_click_sequence.py` contra los módulos de producción
de 0.5.12 (`a42ed74dd2966a36b74eb83e18a21843997ff434`). Las nuevas pruebas cubren
los cinco botones, posiciones distintas, contadores nativos, eventos propios o
reenviados y caducidad de la secuencia con un reloj monotónico común.

La prueba de concurrencia retiene deliberadamente un movimiento remoto mientras
llega el evento físico. Comprueba que se libera el botón y Shift antes de restaurar
la posición local y que el enlace permanece disponible. Los fallos simulados
comprueban la limpieza y la conservación del registro de una liberación rechazada;
el siguiente intento de limpieza la resuelve sin recolocar antes el cursor.

Una primera ejecución de Windows detectó que la prueba del teclado mezclaba
callbacks con llamadas directas a Session. El movimiento podía adelantarse a
las teclas pendientes sólo por esa mezcla de rutas. Se reprodujo en ambos sentidos;
la prueba corregida usa callbacks para los dos dispositivos y exige el orden
tecla abajo, tecla arriba y movimiento final, también con eventos en cola.
La corrección de esta prueba no modifica código de producción.

El CI vuelve a comprobar los bancos anteriores: 52 escenarios de red y 104 acciones,
54 ráfagas, 36 escenarios de recuperación con 144 acciones y 53.000 posiciones sin
errores en el modelo de píxeles de Windows. Ninguna prueba captura o inyecta entrada
real. Los paquetes quedan preparados para la aceptación conjunta con periféricos.

Evidencia: [validación 0.5.13](validation/input-0.5.13-local-pointer.json).

## Precisión y entrada combinada en 0.5.12

La 0.5.12 corrige la precisión del cursor en Windows. Cada posición apunta
al centro del rango que representa su píxel: algunas coordenadas anteriores
caían en el píxel vecino. Al recuperar control local, el primer desplazamiento
parte de la posición física redondeada y evita un pequeño salto. El punto donde
se mantiene el cursor mientras controla el otro equipo también usa un píxel
exacto en pantallas de dimensiones impares. No añade consultas de pantalla por
movimiento. Mac conserva sus coordenadas fraccionarias y ambos sistemas
comparten la misma edición y las pruebas de entrada en ambos sentidos.

Pasan 1.449 pruebas por plataforma, Ruff y ambas compilaciones. Se añadieron
90 casos y pasan 231 pruebas focalizadas. De las 26 regresiones de precisión,
22 fallaban antes y cuatro ya protegían el comportamiento del Mac. El banco
recorre 53.000 posiciones de ocho geometrías: pasa de 453 coordenadas incorrectas
a ninguna en el modelo de rangos de píxel de Windows. Otros 64 casos mezclan
19.200 operaciones de movimiento, teclas, botones y rueda con ambos sentidos,
TLS e híbrido, y un receptor inmediato o con coste simulado de 1 ms.
Las APIs de entrada están simuladas y TLS/UDP usan loopback. La aceptación
física de fluidez, recuperación y consumo sigue pendiente.

La comparación llama a `WindowsInput.inject` con `SendInput` simulado y decodifica
sus argumentos con un modelo independiente de rangos de píxel. Sigue la
[explicación del código oficial de Microsoft WPF](https://github.com/dotnet/wpf/blob/65d15adece1df9a73e3d1abc11f8bbf99e9fc7a9/src/Microsoft.DotNet.Wpf/src/UIAutomation/UIAutomationClientSideProviders/MS/Internal/AutomationProxies/Input.cs#L87-L104).
Se verificó el blob `f31c00d9a77563b6096666b9f89f7f852fe208c9` de esa revisión.
Los barridos horizontal y vertical cubren ocho geometrías entre 1280×720 y
7680×4320, incluidos orígenes negativos. No se movió un cursor nativo: la prueba
demuestra la conversión y su estado interno dentro de ese modelo.

Reproducir con `PYTHONPATH=src python benchmarks/input_pointer_precision.py`.
La comparación de 0.5.11 utiliza el mismo banco con `--expect-errors` contra sus
módulos de producción. La suite combina ocho semillas, dos sentidos, dos
transportes y dos costes del receptor; comprueba orden, posición de cada acción,
liberación de teclas y botones y conservación del enlace. Las 19.200 operaciones
no cuentan la preparación ni las liberaciones finales.

Evidencia: [validación 0.5.12](validation/input-0.5.12-pointer-precision.json).

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
