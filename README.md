# CleverSwitch Flow local

Prototipo para coordinar un **MX Keys original y un MX Master 3** entre Windows
(canal 1, pantalla izquierda) y Mac (canal 2, pantalla derecha).

Versión de esta edición: **0.4.2**, protocolo **2**. Ambos equipos deben ejecutar
protocolo 2 para utilizar el traslado coordinado; para la protección del borde,
actualiza ambos a 0.4.2. Actualizar la app conserva
el enlace y los emparejamientos Bluetooth/USB existentes.

## Estado y coordinación

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

- [Validación nativa de Mac 0.4.2](docs/validation/macos-0.4.2.json): 646 pruebas,
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

El script se ha ejecutado en macOS 26.6.2 ARM64 y el binario compilado ha superado
una vista previa de arranque/cierre, con firma ad-hoc válida y sin cambiar los
datos de vinculación ni el inicio de sesión. La firma nueva requiere renovar
los permisos guardados. La prueba con periféricos, reposo y permisos queda pendiente.

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
contengan. La app intercambia coordinación por LAN cifrada; no transmite letras,
portapapeles ni movimientos continuos del cursor. Los periféricos se conectan a
cada equipo por Bluetooth o receptor Logitech.

## Procedencia

Modificación independiente de [MikalaiBarysevich/CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch),
base v1.5.7, commit `de0a4f4f4b996a63bbdf2e368c93eb3843eb2732`.
Licencia [GPL-3.0-or-later](LICENSE.txt). Se conservan los avisos de autoría del
código y la [documentación original](docs/UPSTREAM-README.md), que describe otra
edición. Se omiten sus workflows de publicación AUR/Homebrew y etiquetado: este
repositorio no está configurado para publicar en los repositorios del autor.
