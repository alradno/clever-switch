# HIDAPI incluido para Windows

`hidapi.dll` es el binario oficial x64 sin modificar de HIDAPI 0.15.0, extraído de
`x64/hidapi.dll` del paquete de Windows del proyecto libusb/hidapi.

- Proyecto: https://github.com/libusb/hidapi
- Versión: https://github.com/libusb/hidapi/releases/tag/hidapi-0.15.0
- Código fuente: https://github.com/libusb/hidapi/tree/hidapi-0.15.0
- Licencia BSD del componente: [HIDAPI-LICENSE-bsd.txt](HIDAPI-LICENSE-bsd.txt),
  reproducida del tag oficial sin modificaciones.
- SHA-256 del DLL:
  `d4c05ba2138cb5259a7f796464b5eaa63b9f8f67bb5cb003f96989244ee01583`.

`build_tray.py` comprueba ese hash antes de empaquetarlo. En pruebas desde un
checkout de Windows, cópialo a `.venv/Scripts/hidapi.dll` para que el cargador
actual de la aplicación lo encuentre. No requiere editar PATH ni instalarlo
en una carpeta del sistema.

En Mac no se utiliza este DLL: se enlaza HIDAPI ARM64 de Homebrew al compilar.
Las demás dependencias se instalan con los archivos de requisitos fijados y
conservan sus propias licencias.
