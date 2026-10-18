# CleverSwitch Flow local

Prototipo para coordinar un **MX Keys original y un MX Master 3** entre Windows
(canal 1, pantalla izquierda) y Mac (canal 2, pantalla derecha).

Versión candidata conjunta: **0.5.4 experimental**, protocolo de control **2**.
Mac y Windows se compilan
desde el mismo commit; las correcciones compartidas se prueban en ambos sentidos.
Ambos equipos deben ejecutar protocolo 2 para utilizar el traslado coordinado.
Actualizar la app conserva
el enlace y los emparejamientos Bluetooth/USB existentes.

## Estado y coordinación

La candidata 0.5.0 añade entrada continua por LAN, desactivada por defecto. Los
periféricos permanecen conectados a un equipo y los eventos cruzan por una conexión
TLS persistente. Cada equipo conserva sus aplicaciones y su distribución de teclado.
El menú «Ratón y teclado por red (experimental)» debe activarse expresamente en ambos.
Este modo suspende el traslado Bluetooth por bordes y el seguimiento HID; conserva
la vinculación, las reglas de firewall y los botones físicos Easy-Switch.

La 0.5.4 mantiene actualizable el movimiento pendiente hasta que el socket puede
escribir. Así evita enviar primero una posición antigua al terminar una espera
de red. Los clics, teclas y cambios de foco conservan su orden y posición; un
bloque ya presentado a TLS no se modifica durante sus reintentos o escrituras
parciales. No se añade un temporizador de suavizado ni una espera para agrupar.

En el banco local, con esperas artificiales de 8, 24 y 80 ms antes de poder
escribir, el primer movimiento pasa de una muestra de unos 10–99 ms de antigüedad
a la más reciente, con unos 0,1 ms. Es frescura de la cola en esa simulación,
no latencia física de Wi-Fi ni una garantía de fluidez. Reproducir con
benchmarks/input_freshness.py.

El banco adicional `benchmarks/input_network.py` retrasa tráfico cifrado ya
aceptado por el transporte. Sus 24 casos locales conservan el orden y las
posiciones de las acciones en ambos sentidos, pero una retención de 80 ms sigue
causando pausas de 81–88 ms en la entrega. No mide la red doméstica ni reproduce
pérdida de paquetes. La coalescencia no puede eliminar una pausa de transporte.
[Medidas y límites](docs/validation/input-0.5.4-network-delay.json).

El [laboratorio de movimiento](docs/MOTION-LAB.md) estudia una vía cifrada que
tolera pérdidas sin detener los movimientos posteriores y conserva las posiciones
de las acciones. Ya prueba claves por conexión mediante TLS real, una cola UDP
limitada y compatibilidad con el servicio fiable actual. Es un prototipo
independiente; todavía no está incluido en la candidata 0.5.4 ni en las apps
instaladas. Queda conectar las piezas al circuito completo de entrada.

Se conserva la recuperación de pantallas de 0.5.3, que responde a los avisos
nativos de cambio de pantalla en ambos equipos.
Cancela la entrada que usa coordenadas antiguas, libera teclas y botones y vuelve
a negociar la geometría. Si se retira un monitor, restaura el cursor dentro de una
pantalla que siga conectada. También rechaza una conexión si las pantallas cambian
entre el anuncio y el arranque del capturador. Los observadores viven sólo mientras
hay una sesión conectada; no añaden temporizadores ni trabajadores de búsqueda.
Un cambio local de pantalla permite una nueva ventana finita de reconexión;
los fallos de red siguen agotando sus intentos y pasan a espera pasiva.

Se conservan las mejoras anteriores: recuperación del teclado al cambiar de canal,
consultas de permisos fuera del hilo de entrada, ráfagas TLS sin espera añadida y
protección del destino si mantiene teclas o botones físicos pulsados. El diagnóstico
muestra tiempos agregados de ida y vuelta sin guardar entrada. Las medidas de
[eficiencia 0.5.2](docs/validation/input-0.5.2-efficiency.json) describen trabajo
simulado evitado, no autonomía ni latencia física.

Pasan 1.010 pruebas por plataforma, Ruff y ambas compilaciones. Mac verifica
firma y vista previa; Windows comprueba el ejecutable empaquetado.
Evidencia: [0.5.4](docs/validation/input-0.5.4-freshness.json).
Los paquetes quedan preparados para una única prueba conjunta al volver el usuario.
No se instalan candidatas intermedias: Aplicaciones conserva 0.5.1; la última
comprobación del Windows doméstico corresponde a 0.5.0. Quedan pendientes los
permisos de la firma final y la aceptación física de fluidez, batería, recuperación
del teclado y cambios de monitor en ambos sentidos.

El menú «Comprobar permisos de entrada…» revisa los requisitos sin capturar nada.
Detalles de uso: [entrada por red](docs/INPUT-LAN.txt).

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
entrada por red transmite posiciones, clics y códigos de teclas por TLS al equipo
vinculado, sin guardar su contenido ni transmitir pantalla, portapapeles o archivos.
Los periféricos permanecen conectados por Bluetooth o receptor al equipo de origen.

## Procedencia

Modificación independiente de [MikalaiBarysevich/CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch),
base v1.5.7, commit `de0a4f4f4b996a63bbdf2e368c93eb3843eb2732`.
Licencia [GPL-3.0-or-later](LICENSE.txt). Se conservan los avisos de autoría del
código y la [documentación original](docs/UPSTREAM-README.md), que describe otra
edición. Se omiten sus workflows de publicación AUR/Homebrew y etiquetado: este
repositorio no está configurado para publicar en los repositorios del autor.
