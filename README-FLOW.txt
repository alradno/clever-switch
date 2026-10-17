CLEVERSWITCH FLOW — candidata experimental 0.5.3, Windows 1 y Mac 2

Para MX Keys original + MX Master 3. Windows delante/a la izquierda, Mac a la derecha.
Basado en CleverSwitch v1.5.7 (de0a4f4f4b996a63bbdf2e368c93eb3843eb2732), GPL-3.0-or-later.
Modificación independiente, sin mantenimiento ni certificación del autor original.

QUÉ HACE
Entrada continua por LAN con los periféricos conectados físicamente a un equipo.
No transmite el escritorio. Conserva el enlace, protocolo de control 2, protocolo
de entrada 1 y los puertos. El menú Ratón y teclado por red (experimental) requiere
activación explícita en ambos. Easy-Switch permanece en el propio dispositivo.

REVISIÓN 0.5.3
La 0.5.3 reacciona a los avisos nativos de cambio de pantalla en ambos equipos.
Cancela la entrada que usa coordenadas antiguas, libera teclas y botones y vuelve
a negociar la geometría. Si se retira un monitor, restaura el cursor dentro de una
pantalla que siga conectada. También rechaza una conexión si las pantallas cambian
entre el anuncio y el arranque del capturador. Los observadores viven sólo mientras
hay una sesión conectada; no añaden temporizadores ni trabajadores de búsqueda.
Un cambio local de pantalla permite una nueva ventana finita de reconexión;
los fallos de red siguen agotando sus intentos y pasan a espera pasiva.

Pasan 1.006 pruebas por plataforma, Ruff y ambas compilaciones. Mac verifica
firma y vista previa; Windows comprueba el ejecutable empaquetado.
Se conservan las mejoras 0.5.1/0.5.2 de teclado, permisos, transporte y diagnóstico.
La candidata se prepara para probar ambos equipos juntos al volver el usuario.
No se instala ahora: Mac conserva 0.5.1 y el último estado comprobado del Windows
doméstico es 0.5.0. La aceptación física sigue pendiente, incluidos cambios de
pantalla, fluidez y recuperación del teclado. Véase docs/INPUT-LAN.txt y
la evidencia docs/validation/input-0.5.3-displays.json.

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
