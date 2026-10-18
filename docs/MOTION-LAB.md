# Movimiento independiente de la cola fiable

Este experimento avanza hacia una entrada continua más resistente a pérdidas.
Todavía no está integrado ni instalado: la candidata de la app sigue siendo
0.5.4. Los archivos de `experiments/` no forman parte de su ejecución ni de sus
paquetes. No se capturan periféricos ni se inyecta entrada del sistema.

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
python -m pytest experiments -o addopts='' --cov=experiments.motion_lane --cov=experiments.motion_link --cov-fail-under=95
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
mensajes y conserva la autenticación criptográfica. Esto permite reutilizar el
puerto UDP existente cuando se conecte el componente a la aplicación.

Las pruebas usan TLS mutuo y UDP reales en loopback, en ambos sentidos. Incluyen
reconexión, fallo del certificado, respuesta incompleta, cierre, limpieza de
claves y mezcla de descubrimiento/movimiento en un socket. El servicio de entrada
actual, sin modificar, conserva el modo fiable con un extremo de laboratorio
que no selecciona la capacidad. La selección se realiza mediante un adaptador
de prueba; todavía no está conectada al anuncio de capacidades de la aplicación.

La suite ampliada pasa 138 pruebas locales, con 97,90 % de cobertura conjunta
(100 % del módulo nuevo). Las 138 pruebas pasan también en Windows y macOS,
con 97,96 % de cobertura y sin exclusiones. Los artefactos descargados coinciden
con sus hashes publicados y los 160 casos del modelo mantienen idénticos resultados.
[CI de la negociación](https://github.com/alradno/clever-switch/actions/runs/39085575993);
[evidencia y límites](validation/motion-link-2026-10-18.json).

## Trabajo necesario para incorporarlo a la app

Falta conectar estos componentes al `Service` y `Discovery` de producción y al
bucle que multiplexa TLS, UDP y plazos en un único dueño del estado. También
falta codificar las acciones con sus barreras en el flujo real y enlazar su vida
con el foco, la cancelación, el reposo y la reconexión de la aplicación. El banco
actual prueba esas piezas por separado; aún no recorre ese circuito completo.

La integración debe conservar el puerto, las reglas de firewall y la espera
pasiva y probarse con sockets reales bajo fallos, además del modelo. Los tiempos
del modelo anterior siguen siendo simulados; la prueba de negociación no los
convierte en mediciones físicas. Después corresponderá compilar una candidata
conjunta y probarla con los periféricos reales al volver el usuario. La candidata
disponible sigue siendo 0.5.4 y no se ha instalado una app nueva.
