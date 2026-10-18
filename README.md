# CleverSwitch Flow local

Prototipo para coordinar un **MX Keys original y un MX Master 3** entre Windows
(canal 1, pantalla izquierda) y Mac (canal 2, pantalla derecha).

Versión candidata conjunta: **0.5.8 experimental**, protocolo de control **2**.
Mac y Windows se compilan
desde el mismo commit; las correcciones compartidas se prueban en ambos sentidos.
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

Pasan 1.269 pruebas por plataforma, Ruff y ambas compilaciones. Los 16 casos nuevos
cubren llegada, regreso, cancelación local y el primer desplazamiento en ambos
adaptadores, con y sin notificación del evento generado. También comprueban la
inversión inmediata tras recuperar control moviendo físicamente hacia el interior.
Catorce fallaban antes de sus respectivas correcciones.
Las 157 pruebas focalizadas usan APIs simuladas y TLS/UDP reales en loopback;
conservan además los 64 ciclos de ida y vuelta rápida. La aceptación física sigue pendiente. Mac verifica
firma y vista previa; Windows comprueba el ejecutable empaquetado. Evidencia: [validación 0.5.8](docs/validation/input-0.5.8-arrival.json).
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
