# Continuación en Windows — integración 0.4.1

Usar este repositorio como fuente común. Antes de trabajar, revisar `git status` y
hacer `git pull --ff-only`; conservar cualquier modificación local antes de integrar.
No usar los ZIP anteriores como base de nuevos cambios.

La edición está en `src/cleverswitch/flow/version.py`: 0.4.1, protocolo 2. Conserva
la coordinación 0.4.0 de Windows y añade:

- Rearme del borde sólo con una muestra segura al menos 80 px dentro de pantalla.
  La colocación automática a 24 px no lo rearma; se conserva la pausa de llegada.
- Registro local rotativo de fases en `~/.config/cleverswitch-tray/flow.log`,
  máximo 256 KiB más una copia; sin teclas, secretos ni historial del cursor.
- `Reconectar ahora` borra el error persistente anterior.
- `flow/identity.py` versionado: la regla `identity.*` lo había excluido por error.
- Suite portable: mocks de APIs Windows y TLS real en dos puertos de loopback.
  No cambian el protocolo, los certificados ni la validación del código de producción.

En Mac se ejecutaron 626 pruebas sin exclusiones, cobertura 90,66 %, Ruff, el
script nativo de compilación y una vista previa del binario con salida 0. Se
comprobó la versión y protección del borde en el código empaquetado. La app
0.4.1 está instalada en Aplicaciones; no se concedieron permisos automáticamente.
Bluetooth y Accesibilidad necesitan renovación por el cambio de firma ad-hoc.
No hay nueva aceptación física ni validación Windows de esta integración.

En Windows: compilar y ejecutar la suite con las instrucciones del README. Cerrar
la copia anterior, conservar una copia recuperable del ejecutable y sustituirlo
en la ruta estable que ya permiten las reglas del firewall. No regenerar el enlace
ni copiar al repositorio sus claves, certificados, caché o diagnósticos privados.
Confirmar 0.4.1 en ambos menús y probar ambos sentidos, entrando hacia el centro
tras cada llegada antes de volver al borde. Publicar aquí cualquier corrección y
su evidencia; no presentar simulaciones como prueba con dispositivos reales.
