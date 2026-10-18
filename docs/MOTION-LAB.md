# Movimiento cifrado integrado en la entrada continua

La 0.5.5 incorporó el transporte a `Service`, `Channel` y `Discovery`.
Los módulos de producción son `input_motion.py`, `input_motion_link.py` e
`input_hybrid.py`. Los archivos equivalentes de `experiments/` los reutilizan;
ya no mantienen una segunda implementación. No se ha instalado la candidata.
La validación utiliza entrada sintética y sockets reales en loopback.

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

Evidencia actual: [validación 0.5.6](validation/input-0.5.6-bursts.json).

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
