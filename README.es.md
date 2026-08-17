# RenPySlim

> Herramienta todo en uno para adelgazar recursos y empaquetar juegos de Ren'Py

**🌐 Idioma:** [简体中文](README.md) | [English](README.en.md) | [Русский](README.ru.md) | **Español** | [Português (BR)](README.pt.md) | [Türkçe](README.tr.md) | [Deutsch](README.de.md) | [Français](README.fr.md)

**Licencia: [AGPL-3.0](LICENSE)** · Avisos de terceros en [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

---

## Qué es esto

RenPySlim ayuda a desarrolladores de juegos Ren'Py a hacer sus juegos **más pequeños, más ordenados y listos para publicar** — en un solo flujo:

- **Analizar** — busca recursos inflados y genera un informe de tamaño/problemas/recomendaciones
- **Comprimir** — adelgaza imágenes, audio, vídeo y fuentes; las referencias en los guiones se reescriben automáticamente; el perfil predeterminado prioriza la calidad (q95, casi sin pérdidas) y el procesamiento paralelo aprovecha todos los núcleos
- **Empaquetar** — genera paquetes de PC / Mac / Android mediante el SDK oficial
- **Adelgazar distribución** — adelgaza con seguridad un juego ya empaquetado (carpeta o zip/7z/rar), entra y sale listo
- **Adelgazar APK** — también para Android: imágenes → WebP, audio → OGG (reasignación en tiempo de ejecución, sin tocar referencias), re-firmado automático
- **Descompilación para conversión** (experimental) — para distribuciones sin código fuente, el unrpyc integrado recupera los guiones y permite convertir incluso los recursos archivados, que luego se reempaquetan en sus RPA

Además, chequeo de salud del proyecto: detección de recursos sin uso, limpieza de
basura antes de empaquetar, detección de duplicados, informe de glifos faltantes —
y el lint oficial se ejecuta automáticamente tras cada optimización.

**Seguro por defecto**: todas las operaciones trabajan primero sobre una copia, los
originales no se tocan; «si no se hizo más pequeño, no se reemplaza»; los recursos
sin referencias encontradas nunca se renombran; cada ejecución produce un informe de
análisis y una lista de cambios.

## Inicio rápido

**Usuarios normales**: descarga `RenPySlim.exe` desde
[Releases](https://github.com/AxelBeary/renpyslim/releases), doble clic para
ejecutar — el navegador abre la interfaz automáticamente.

**Desarrolladores**:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python main.py            # iniciar la interfaz gráfica
```

## Interfaz gráfica (recomendada)

Diseño con barra lateral, compatible con **中文 / English / Русский / Español / Português (BR) / Türkçe / Deutsch / Français** y
**tema claro/oscuro** (cambio en la esquina superior derecha; sin elección manual
sigue el idioma del navegador y la apariencia del sistema, y tu elección se
recuerda). Cuatro entradas: **Optimizar y empaquetar / Adelgazar distribución / Adelgazar APK / Adelgazar fuentes**.

### Flujo guiado en cuatro pasos

1. Escribe la ruta (o pulsa «Examinar archivos/Examinar carpetas»), pulsa «Analizar» → revisa el informe
2. Marca las optimizaciones deseadas y elige un nivel de compresión
3. Pulsa «Ejecutar» → observa el progreso y el registro en tiempo real
4. Obtén el resultado optimizado / los paquetes oficiales

### Operaciones cómodas

- **Arrastra un zip / 7z / rar / APK / carpeta directamente sobre el icono de la herramienta** — la ruta se rellena sola y se abre la función correspondiente
- Si la herramienta ya está abierta, soltar un archivo nuevo abre una pestaña nueva en lugar de lanzar otra instancia
- Las rutas usadas recientemente quedan en «Recientes», listas con un clic

### Las cuatro entradas

- **Optimizar y empaquetar**: apunta a la carpeta del proyecto; tras optimizar, el SDK oficial genera los paquetes (PC/Mac/Android); opcionalmente «empaquetar recursos en un archivo RPA» (canal oficial)
- **Adelgazar distribución**: apunta a la carpeta de la distribución o suelta un zip / 7z / rar (extracción automática, adelgazado y re-empaquetado automático; compatible con archivos protegidos con contraseña); los archivos RPA se desmontan, optimizan y reconstruyen automáticamente; si el archivo contiene un APK pasa automáticamente al adelgazado seguro de APK; la opción experimental «descompilar guiones» permite la conversión de formatos también en distribuciones sin código fuente
- **Adelgazar APK**: elige un archivo .apk, tres pasos (nivel / modo de máximo adelgazado / firma — por defecto se crea una clave nueva), y obtienes un paquete adelgazado listo para instalar
- **Adelgazar fuentes** (autónomo): no se necesita el proyecto del juego — elige una fuente + fuentes de texto; las colecciones ttc/otc se separan por peso; los originales nunca se sobrescriben; se incluye la lista de caracteres usados

### Garantías durante la ejecución

- Puedes pulsar «Detener» en cualquier momento (el trabajo completado se conserva); los fallos escriben automáticamente un volcado
- La interfaz avisa cuando hay una versión nueva (comparando con GitHub Releases)
- Si falta FFmpeg / 7-Zip, la interfaz muestra los pasos concretos de instalación (comando winget o enlace de descarga)
- Para salir: clic derecho en el icono de la bandeja → Salir, o el botón «Salir» de la barra lateral (cerrar la pestaña del navegador no detiene la herramienta)

## Modo sin interfaz (para guiones/automatización, salida JSON)

```
python cli.py env                                  # chequeo del entorno
python cli.py analyze <ruta> --mode project        # analizar
python cli.py optimize <ruta> --preset balanced    # optimizar
python cli.py full <proyecto> --platforms pc,mac   # optimizar + empaquetar
python cli.py slimfont <fuente> <textos...>        # adelgazado de fuentes autónomo
python cli.py slimapk <apk> --remap --gen-key      # adelgazar APK (WebP/OGG + re-firmado)
```

## Requisitos

| Dependencia | Para qué | Nota |
|---|---|---|
| Ren'Py SDK | Empaquetado, compilación de guiones de reasignación APK | Suele detectarse solo; si no, indícalo en «Ajustes» |
| FFmpeg | Optimización de audio/vídeo | En el PATH o en la carpeta bin junto al programa |
| Java/JDK | Empaquetado Android, re-firmado de APK | El primer empaquetado Android requiere completar la configuración de Android en el lanzador de Ren'Py |

El servicio de la interfaz escucha por defecto en 127.0.0.1:52786 (puerto poco
común); si está ocupado usa automáticamente un puerto libre asignado por el sistema.
Puedes fijar otro puerto con la variable `RENPYTOOLS_PORT`.

## Mecanismos de seguridad

| Mecanismo | Descripción |
|---|---|
| Copia de trabajo | Por defecto todo se hace sobre una copia — los originales no se tocan ni un byte |
| Copia de seguridad obligatoria | Al marcar «modificar los archivos originales», primero se crea un respaldo completo (incluidas las partidas guardadas) |
| Si no se hizo más pequeño, no se reemplaza | Cada optimizador escribe un archivo temporal y solo reemplaza si el tamaño realmente bajó |
| Control por referencias | Los recursos sin referencias literales en los guiones se comprimen en su sitio y nunca se renombran |
| Protección de carpetas del motor | En los modos distribución/APK, renpy/, lib/, assets/x-renpy/ nunca se tocan |
| Marcar, no borrar | Los archivos posiblemente sin referencias solo aparecen en el informe por defecto; con la opción activada van a cuarentena |
| La limpieza solo borra lo regenerable | Cachés/registros/bytecode; en modo de modificación directa se omite automáticamente para proteger las partidas |
| Las imágenes no se declaran muertas | Ren'Py carga imágenes por nombre de archivo — sin referencia ≠ sin uso |
| Protección contra entradas maliciosas | Deserialización de índices por lista blanca; saneado de rutas de entradas (defensa contra zip-slip) |
| Solo local | El servicio escucha únicamente en 127.0.0.1 y verifica el origen de las peticiones — inaccesible desde fuera |
| Lint automático tras optimizar | La comprobación estática oficial está integrada en el flujo y se archiva como validation.txt |
| Lista de cambios | Cada ejecución escribe changelog.json con cada modificación |

## Límites de seguridad

- El servicio **escucha solo en 127.0.0.1** (dirección «solo este equipo»): otros
  dispositivos de la red local o de internet simplemente no pueden conectar. No hace
  falta configurar el cortafuegos y no se recomienda exponerlo de ninguna forma;
- La herramienta no ofrece ni planea ofrecer una opción de «abrir acceso de red». Si
  modificas el código fuente, **no se recomienda en absoluto** cambiar la dirección a
  0.0.0.0 o una pública — la interfaz no tiene inicio de sesión, y exponerla entrega
  la capacidad de leer y escribir archivos locales a cualquiera que pueda acceder;
- La herramienta no hace peticiones salientes, con una única excepción: «comprobar
  actualizaciones» (compara con GitHub Releases; si falla se omite en silencio y no
  afecta a ninguna función).

## Pruebas

```
.venv\Scripts\python -m pytest tests -q
```

Cubren lectura/escritura de archivos RPA (ambas generaciones del formato y bloqueo
de archivos maliciosos), seguridad de la reescritura de referencias, que los
optimizadores de fuentes/imágenes no dañen originales, análisis de rpyc, adelgazado
de APK (protección del motor / eliminación de firma / conversión de rutas x- /
generación de claves), cancelación y volcados de fallos, valores por defecto
seguros, regresiones de correcciones, protección local del backend e integridad de
los diccionarios de localización de los ocho idiomas — 114 pruebas.

## Desarrollo

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
python main.py            # iniciar la interfaz gráfica
build_exe.bat             # recompilar el exe
```

**Mantenedores/agentes, leer primero:**

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): plano de arquitectura, líneas rojas de seguridad, guía de extensión
- [docs/BACKLOG.md](docs/BACKLOG.md): archivo de requisitos y pendientes (las ideas nuevas van aquí primero)
- [docs/STATUS.md](docs/STATUS.md): estado de traspaso y resultados de pruebas reales

## Localización

| Idioma | Interfaz | Documentación | Estado |
|---|---|---|---|
| 简体中文 | ✅ por defecto | ✅ documento principal | Disponible |
| English | ✅ | [README.en.md](README.en.md) | Disponible |
| Русский | ✅ | [README.ru.md](README.ru.md) | Disponible |
| Español | ✅ | ✅ este documento | Disponible |
| Português (BR) | ✅ | [README.pt.md](README.pt.md) | Disponible |
| Türkçe | ✅ | [README.tr.md](README.tr.md) | Disponible |
| Deutsch | ✅ | [README.de.md](README.de.md) | Disponible |
| Français | ✅ | [README.fr.md](README.fr.md) | Disponible |

¿Quieres añadir un idioma nuevo? Consulta la «Guía de traducción» en
[CONTRIBUTING.md](CONTRIBUTING.md) — añade un diccionario a la interfaz y un
README.<código-de-idioma>.md a la documentación.

## Licencia y cumplimiento

- El proyecto se publica bajo **AGPL-3.0**: puedes usarlo, modificarlo y
  distribuirlo libremente, pero las versiones modificadas (también al servirlo por
  red) deben publicarse bajo la misma licencia. Adelgazar tu propio juego no tiene
  restricciones; la obligación de abrir el código solo surge al distribuir versiones
  modificadas.
- Avisos completos de dependencias de terceros e implementaciones de referencia de
  formatos: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
  (cumplimiento LGPL de pystray, créditos del formato Ren'Py, límites de programas externos)
- Para contribuir, lee primero [CONTRIBUTING.md](CONTRIBUTING.md); las
  vulnerabilidades van por el canal privado de [SECURITY.md](SECURITY.md).
- Ren'Py es una marca registrada/proyecto de Tom Rothamel y otros; este proyecto no
  está afiliado — es una herramienta independiente de terceros para la comunidad de
  Ren'Py.
