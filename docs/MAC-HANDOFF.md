# Traspaso para Codex en el Mac

## Objetivo y configuración

Continuar este mismo prototipo y publicar los cambios en `alradno/clever-switch`.
Equipo de destino: MacBook M4 Pro, macOS indicado por el usuario como 26.6.2
(confirmar versión real). Windows delante/a la izquierda, Mac a la derecha; teclado
MX Keys original y ratón MX Master 3, canales Windows=1 y Mac=2. Misma LAN doméstica.

El usuario consiguió traslados físicos con la versión anterior, pero informó de
traslados de un solo dispositivo, devoluciones automáticas a los pocos segundos y
fallos intermitentes. No hay aún validación física de 0.4.0 en los dos equipos.

## Estado disponible

- Candidata conjunta actual: **0.5.24 experimental**, protocolo de control 2 y entrada 1.
  Selecciona movimiento reciente a través de heartbeats, corrige esperas calculadas
  con tiempo antiguo y fecha los clics desde el reloj nativo de Mac. Añade medidas
  agregadas que ceden ante la entrada. Pasan 2.094 pruebas en Mac, Ruff y la
  compilación ARM64; Windows pendiente. No instalar aún.
  Evidencia: `validation/input-0.5.24-cursor-stutter.json`. La investigación no
  demuestra todavía qué causó las pausas del vídeo doméstico. El 18/10/2026 se
  comprobó 0.5.23 instalada en Mac y disponible por TLS en Windows.
- Candidata anterior: **0.5.23 experimental**, protocolo de control 2 y entrada 1.
  Recupera ráfagas de callbacks sin llenar prematuramente la cola y cerrar el enlace.
  La cola sigue acotada; cada lote cede turno a la red y continúa si queda trabajo.
  Conserva trayectorias, posiciones de acciones, prioridad local y liberaciones.
  Pasan 2.019 pruebas por plataforma, Ruff y ambas compilaciones; 34 casos nuevos,
  26 fallos reproducidos con la fuente anterior y ocho controles conservados.
  APIs nativas simuladas y TLS/UDP reales en loopback. Paquetes sin instalar;
  aceptación física pendiente. Véase `validation/input-0.5.23-callback-bursts.json`.
- Candidata anterior: **0.5.22 experimental**, protocolo de control 2 y entrada 1.
  Conserva los tiempos de clic mediante una extensión negociada, en ambos sentidos.
  Los clics en cola y las liberaciones tras reconectar el ratón conservan su orden.
  No añade otro intercambio al cruce ni sincronización periódica de relojes.
  Pasan 1.985 pruebas por plataforma, Ruff y ambas compilaciones; 69 casos nuevos
  y ocho fallos reproducidos antes de corregirlos. APIs nativas simuladas y TLS/UDP
  reales en loopback. Paquetes preparados sin instalar; aceptación física pendiente.
  Véase `validation/input-0.5.22-click-timing.json`. Para probar la mejora de clics
  deben estar actualizados los dos extremos; una versión antigua mantiene el enlace.
- Candidata anterior: **0.5.21 experimental**, protocolo de control 2 y entrada 1.
  Mac y Windows conservan y completan el cierre si desaparecen temporalmente
  las pantallas, deteniendo la comprobación de entrada en segundo plano.
  La disponibilidad o la preparación del enlace permiten recuperar la entrada.
  Pasan 1.876 pruebas por plataforma, Ruff y ambas compilaciones; 40 casos nuevos,
  16 fallos anteriores reproducidos y 220 pruebas focalizadas en ambos sentidos.
  APIs de sistema simuladas y TLS/UDP reales en loopback; aceptación física pendiente.
  Véanse `validation/input-0.5.21-display-recovery.json` y [MOTION-LAB.md](MOTION-LAB.md).
  No instalar candidatas intermedias ni pedir pruebas al usuario ausente. En esa revisión, Mac conservaba
  0.5.1 y el último Windows doméstico observado tenía 0.5.0. Quedan pendientes fluidez,
  Easy-Switch, pantallas, reposo/DHCP y consumo con hardware real en ambos sentidos.
- La validación adicional de 0.5.21 añade 40 escenarios por plataforma de
  entrada sostenida a 125 y 1.000 informes por segundo, con receptores lentos
  y pausas de 40 ms. Verifica 19.800 movimientos, 720 acciones ordenadas y
  60 reconexiones de teclado por plataforma, además de las liberaciones y el
  siguiente cruce. Pasan 1.916 pruebas en Mac y Windows. No aparece otro fallo
  de aplicación: las fuentes y los paquetes de la candidata siguen iguales.
  Las APIs de entrada son simuladas y TLS/UDP usan loopback real; la fluidez y
  recuperación físicas siguen pendientes de la prueba conjunta.
  Véase `validation/input-0.5.21-sustained.json`.
- Validación adicional 0.5.19: 24 casos nuevos por plataforma, 64 reconexiones
  repetidas y ocho cortes unidireccionales. Conservan liberaciones, nuevos cruces
  y pulsaciones tras avisos tardíos; los trabajadores de entrada terminan al cerrar.
  Cuatro casos rechazan un datagrama antiguo recibido por UDP durante un foco nuevo.
  APIs nativas simuladas y TLS/UDP reales en loopback. Código y paquetes sin cambios;
  no se ha probado la fluidez o el consumo físicos. Véase `validation/input-0.5.19-reconnect.json`.
- Validación adicional 0.5.18: 24 escenarios por plataforma con retrasos UDP
  variables, reordenamiento, pérdidas y retención de 40 ms, desde la negociación.
  Se verifican 48 acciones por ejecución, posición final, liberaciones y ausencia
  de retrocesos. Código y paquetes sin cambios; la pausa de red sigue siendo
  perceptible. Véase `validation/input-0.5.18-datagram-jitter.json`.
- Rendimiento 0.4.6: borde de 80 ms y cursor colocado al confirmar el ratón,
  sin esperar al teclado ni repetir la colocación tras perder un ACK. Seguimiento
  del teclado en un único hilo separado, con descarte tras reconexión; descubrimiento
  acelerado sólo durante una preparación autenticada y lecturas HID que ceden a
  órdenes pendientes. Bluetooth despierta al conectarse HID. Se mantienen todos
  los límites, pruebas nuevas de presencia, rearme a 80 px y pausa de 3 s.
  Medidas comparables y límites en `validation/macos-0.4.6.json`; repetir con
  `PYTHONPATH=src .venv/bin/python benchmarks/handoff.py --cycles 20`.
- La 0.4.5 incorpora instalación estable y firewall por subred en Windows, conserva
  datos/Inicio y admite seleccionar una identidad de firma existente en Mac. No hay
  ninguna válida en el llavero de este Mac: la firma continúa siendo ad-hoc y no se
  garantiza conservar permisos TCC al actualizar. No crear certificados, relajar
  requisitos de firma ni modificar TCC. Véanse `UPDATES.md` y `validation/macos-0.4.5.json`.
- La revisión 0.4.4 añade avisos ordenados de llegada/partida, respuesta directa
  para recuperar DHCP, ausencia de difusión periódica en conexión sana y descarte
  de callbacks de conexiones cerradas. Windows observa cambios de dirección IP;
  Mac observa cambios del servicio DHCP. Pasan 690 pruebas, cobertura 91,98 %.
  Véase `validation/macos-0.4.4.json`; las APIs Windows están simuladas en Mac.
- La revisión anterior se documenta en `validation/macos-0.4.3.json`: 665 pruebas,
  91,36 % de cobertura y 16 casos observados fallando antes de corregirlos. Se
  comprueban ambos sentidos, cancelación durante preparación, reconexión con el
  mismo listener, plazos TLS totales y cierre seguro del lector HID de Windows.
  Mac y Windows deben incorporar el mismo commit. Consolidar las mejoras antes
  de pedir instalaciones al usuario en Windows; aún no hay aceptación física conjunta.
- La 0.4.3 anterior se compiló, instaló y arrancó. El cierre nativo tras 130 s
  sin intervención terminó en 0,33 s. La 0.4.2 anterior no respondió a SIGTERM
  tras una espera prolongada: se conservó la muestra de hilos y se corrigió el
  despertar del bucle nativo con MachSignals. Windows y el CLI no cambian esa ruta.
- La revisión anterior está en `validation/macos-0.4.2.json`; conserva la base
  integrada y corrige envío Bluetooth duplicado, recuperación manual, rearme tras
  reconexión, botones pulsados al llegar y errores de UDP. Las cifras de 0.4.1
  que siguen son la validación histórica de la integración inicial.
- Código, compilador, dependencias fijadas, pruebas e instrucciones en este repositorio.
- Windows x64 compilado y examinado con Defender, sin detecciones.
- 615 pruebas y cobertura 91,47 % en Windows; Ruff correcto.
- 12 traslados alternos con TLS real local y dispositivos simulados. Se pierden 48
  ACK deliberadamente, uno por fase de traslado en cada prueba.
- Tres arranques/cierres de la vista previa compilada, código de salida 0.
- Conservación de hashes de claves/enlace/caché durante la preparación en Windows.
- Integración del Mac: 626 pruebas sin exclusiones, cobertura 90,66 %, Ruff correcto.
- Script Mac ejecutado en macOS 26.6.2 ARM64; compilación, firma y vista previa correctas.
- App instalada y arrancada en Aplicaciones; identidad, enlace e Inicio conservados.
- Los permisos guardados no aceptan la firma nueva; renovación manual pendiente.
- Reposo y estabilidad física con ambos equipos actualizados siguen sin aceptación.

Los resultados concretos y hashes del paquete preparado en Windows están en
`validation/windows-0.4.0.json`. Los binarios y el estado privado local no se versionan.

La evidencia del Mac está en `validation/macos-0.4.1.json`; la continuación en
Windows, en [WINDOWS-HANDOFF.md](WINDOWS-HANDOFF.md). La integración conserva
las siete correcciones de coordinación de Windows que se detallan debajo. Añade
el rearme del borde a 80 px, registro rotativo y borrado de error al reconectar.
El módulo fuente `flow/identity.py`, omitido por `identity.*` en `.gitignore`,
se recupera desde los fuentes locales del Mac y queda versionado.

La 0.4.4 está compilada, instalada y arrancada en Aplicaciones. Tras 130 segundos
sin intervención, el cierre nativo terminó en 0.32 s; el ejecutable
coincide con los cuatro módulos de producción probados. La vinculación, caché
y configuración de Inicio se conservaron. La aceptación física sigue pendiente.

## Fallos encontrados y correcciones incluidas

1. El nombre HID++ real era `MX Keys Wireless Keyboard` y `Wireless Mouse MX Master 3`.
   El filtro abreviado rechazaba ambos. Se admiten esas variantes exactas y las cortas.
2. Una excepción de RPC cancelaba inmediatamente la espera de llegada. Ahora se
   reintenta dentro del plazo existente, sin repetir las órdenes de cambio de canal.
3. Un ping fallido en `commit` devolvía ambos dispositivos enseguida. Ahora queda
   pendiente para obtener pruebas nuevas; la recuperación sigue teniendo plazo finito.
4. El traslado de salida y la recepción no compartían bloqueo. Ahora usan el mismo
   gate durante toda la transacción. Repetir `prepare` con el mismo ID es idempotente.
5. Se reescribían canales ante eventos nativos o un fallo de seguimiento de teclado.
   Se han quitado esas órdenes redundantes. Los botones físicos siguen en firmware.
6. El seguimiento del teclado necesita dos observaciones separadas y una pausa de
   3 s tras terminar. Un solo ping perdido no inicia el seguimiento.
7. Un heartbeat vencido durante una transferencia podía producir una espera de cero
   segundos. El trabajo en curso espera 200 ms y conserva los plazos de recuperación.

El protocolo nuevo rechaza traslados del antiguo. Actualizar ambos equipos antes de
medir estabilidad. No cambiar la vinculación para actualizar el código.

## Compilar y probar en el Mac

1. Clonar este repositorio o integrar `main` conservando cualquier trabajo local.
2. Revisar `git status`, `README.md`, `AGENTS.md` y la versión actual.
3. Tener Python ARM64 3.12+ e HIDAPI. Ejecutar `bash Compilar-Mac.command` en la raíz.
4. Comprobar que `dist/CleverSwitchTray.app` se ha generado y registrar diagnósticos.
5. Cerrar la app anterior antes de sustituirla en su misma ruta estable. Mantener
   las carpetas `.config` existentes y cualquier vinculación ya completada.
6. Revisar los permisos reales de Accesibilidad, Bluetooth, Red local y acceso a HID
   que solicite macOS. Una compilación con firma ad-hoc puede necesitar reautorización.
7. Sólo en un entorno autorizado para entrada nativa, abrir la candidata y
   confirmar que el menú muestra la misma edición en ambos equipos.
8. Usar `Ver diagnóstico…` para comprobar el estado de modelos, enlace y último fallo.

No afirmar que compilar o pasar pruebas demuestra estabilidad física. No eludir
protecciones del sistema para facilitar la prueba.

## Validación física pendiente

Documentar la conexión real de cada periférico (Bluetooth/USB), versión/commit,
permisos, secuencia temporal y resultados sin incluir claves o archivos de enlace.

- Con los dos periféricos en Windows, traslado por borde derecho al Mac; comprobar
  llegada de ambos y cursor situado dentro del borde izquierdo del Mac.
- Regreso por borde izquierdo del Mac, con ambos confirmados en Windows.
- Repetir ambos sentidos, incluyendo llegadas lentas y el límite de pausa de 3 s.
- Botones físicos 1/2 del teclado: comprobar que el ratón sigue; el canal 3 queda manual.
- Inactividad/sueño del ratón, reposo/despertar del Mac y cambios de red.
- Corte del enlace durante un traslado, recuperación y ausencia de ping-pong.
- Mac fuera de casa: búsqueda finita y espera pasiva, sin sondeos continuos.
- Volver a casa con DHCP distinto, despertar cada equipo, reiniciar Windows y
  reabrir la app: quien vuelve anuncia y ambos restablecen el enlace sin pulsar
  Reconectar ahora. Repetir con el compañero ya en espera durante horas.
- Comparar apagado normal y brusco; el primero puede avisar, el segundo debe
  agotar su presupuesto y aparcar. Verificar también pausa/reanudación.
- Inicio de sesión habilitado una vez en cada equipo para cubrir el reinicio.
  El proceso no despierta un ordenador apagado ni actúa antes de iniciar sesión.
- Cerrar la app y actualizarla: configuración, vínculo y botones físicos conservados.

Si falla un traslado, conservar el texto de `Ver diagnóstico…` y el contenido
sanitizado de `~/.config/cleverswitch-flow/last-transfer.json`. No publicar las
carpetas privadas completas. Registrar el fallo antes de modificar tiempos a ciegas.

## Archivos principales

- `flow/coordinator.py`: gate, leases, fases, ACK, recuperación y motivos de fallo.
- `flow/hardware.py`: presencia HID++ con nonce y órdenes de canal.
- `flow/runtime.py`: red/energía, cursor, seguimiento de teclado y diagnóstico.
- `flow/environment.py`: avisos nativos de Windows y SystemConfiguration/NSWorkspace.
- `flow/cursor.py`: sesión/Accesibilidad, borde, esquinas y colocación de cursor.
- `gateway/hid_gateway_ble.py`: ruta CoreBluetooth del Mac.
- `tray/app.py`, `tray/startup.py`: menú y arranque por usuario.
- `tests/cleverswitch/test_flow.py`: pérdidas de ACK, exclusión y estrés TLS.
- `tests/cleverswitch/test_energy.py`: reconexión finita y trabajo en espera.

Las rutas anteriores son relativas a `src/cleverswitch/`, salvo las pruebas.
No hay servicio de control/depuración remoto: el estado del Mac debe observarse allí.
