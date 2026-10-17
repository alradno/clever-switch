# Actualizar sin volver a vincular

Las dos apps conservan identidad, compañero, bordes, router y caché en las carpetas
`.config` del usuario. El instalador no las incluye ni las sustituye. Actualizar no
requiere emparejar Bluetooth otra vez ni intercambiar nuevos archivos de enlace.
Mac y Windows deben compilar la misma edición desde el mismo commit.

## Windows

`build_tray.py` genera cuatro archivos en `dist`: `CleverSwitchTray.exe`,
`Install-CleverSwitch.ps1`, `Configure-CleverSwitchFirewall.ps1` y
`cleverswitch-package.json`. Distribuirlos juntos. El manifiesto detecta archivos
incompletos o alterados; los hashes no sustituyen una firma de editor.

Con CleverSwitch cerrado, ejecutar `Install-CleverSwitch.ps1` desde PowerShell
normal, sin administrador. Instala en `%LOCALAPPDATA%\Programs\CleverSwitchFlow`,
crea un acceso en Inicio y abre la app con el usuario habitual. Verifica el paquete
antes de sustituirlo y restaura los archivos anteriores si una copia falla.

Conserva la elección de arranque automático: si estaba desactivado, sigue así; si
HKCU Run apuntaba a una copia anterior de CleverSwitch, actualiza esa referencia.
Las decisiones de StartupApproved/Administrador de tareas se conservan. Un comando
de arranque personalizado se informa y se deja intacto, en vez de sobreescribirlo.

El firewall usa TCP 47654 y UDP 47655, sólo para el ejecutable instalado y la
subred IPv4 doméstica guardada, tanto en dirección local como remota. Incluye
perfiles Privado y Público porque Windows puede clasificar la Wi-Fi de casa como
Pública. No cambia esa clasificación, no usa `Any`/`LocalSubnet` como direcciones y
no activa acceso desde Internet. TLS mutuo y los avisos autenticados siguen siendo
obligatorios: una regla de firewall no sustituye la identidad del compañero.

Cambiar la IP del Mac o de Windows dentro de esa subred no cambia las reglas ni
requiere UAC. La carpeta estable evita que una actualización invalide el permiso
por ruta. El instalador sólo solicita UAC para reparar reglas ausentes o distintas;
el proceso elevado sólo configura el firewall, nunca inicia la app ni lee identidades.
Si aún no hay enlace, la primera vinculación configura las reglas para la red elegida.

El menú muestra el estado de la instalación y ofrece «Revisar firewall de Windows».
La consulta de inicio es de lectura y se hace una vez, sin temporizador. Cancelar
UAC, una política de administración o una regla de bloqueo quedan como pendientes;
no se desactiva Defender ni se borran bloqueos para aparentar éxito. Las antiguas
reglas manuales que apuntan a otras carpetas no se reutilizan ni se eliminan.
«Reglas presentes» no demuestra conectividad: sigue haciendo falta la confirmación TLS.

Si cambia el router/subred, seleccionar «Red local» y «Actualizar la red del enlace»
en cada equipo. En Windows, ese cambio explícito actualiza las reglas con UAC si
es necesario; conserva las claves. No amplía silenciosamente el acceso a otras redes.

## Mac

Conservar `/Applications/CleverSwitchTray.app` y el bundle ID `local.cleverswitch.tray`.
La configuración y el LaunchAgent permanecen fuera del paquete y no deben reemplazarse.

La firma ad-hoc cambia entre compilaciones: macOS puede volver a pedir Accesibilidad,
Bluetooth o Red local aunque la configuración sea idéntica. Para mantener una identidad
de firma estable, el compilador admite `CLEVERSWITCH_CODESIGN_IDENTITY` con el nombre
exacto o SHA-1 de una identidad de firma válida **ya existente** en el llavero.
Si la identidad solicitada falta, la compilación falla; no vuelve silenciosamente a
firma ad-hoc. Se utiliza la misma identidad para dependencias, ejecutable y paquete.

En este Mac se comprobaron cero identidades válidas el 17 de octubre de 2026. La
candidata usa ad-hoc: no se promete una actualización sin renovar permisos del sistema.
No se han creado certificados, modificado TCC, retirado cuarentena ni notarizado la app.

## Validación pendiente en Windows

Las pruebas de PowerShell en Mac validan copia/restauración reales y simulan
NetSecurity. Antes de considerar final esta candidata, comprobar en Windows:

- Actualización desde la carpeta antigua con Inicio activado y desactivado.
- Misma identidad/configuración antes y después, y arranque con el usuario habitual.
- Primera elevación UAC y segunda instalación sin otra solicitud con reglas correctas.
- Cambios DHCP en cada equipo con perfil Público y Privado; reconexión TLS automática.
- Cancelación de UAC y política/regla de bloqueo: diagnóstico pendiente, sin falsear éxito.
- Traslados físicos en ambos sentidos con la misma edición en Mac y Windows.

No hace falta pedir al usuario instalaciones intermedias para desarrollar estas mejoras.
