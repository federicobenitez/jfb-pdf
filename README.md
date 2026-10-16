# JFB Pdf

Lector de PDF ligero para Debian, escrito en Python con **PySide6** (Qt6) y
**PyMuPDF**. Prioriza un **desplazamiento suave** mediante renderizado
perezoso en segundo plano y una caché con presupuesto de memoria.

## Características

- Abrir PDF desde el menú, arrastrar y soltar, por línea de comandos o desde
  el menú de **recientes**.
- Scroll continuo y fluido: sólo se renderizan las páginas visibles.
- **Búsqueda de texto** (`Ctrl+F`) con resaltados, contador de coincidencias
  y navegación con `F3` / `Shift+F3`.
- **Panel lateral** (`F9`) con el **índice** del documento y **miniaturas**.
- **Auto-scroll**: pulsa el botón central del ratón y mueve el cursor
  arriba/abajo del punto de anclaje para desplazarte de forma continua
  (clic o `Esc` para salir).
- Zoom (acercar/alejar/ajustar ancho/ajustar página) y `Ctrl + rueda`
  anclado en el punto bajo el cursor.
- **Rotación** de la vista (`Ctrl+R` / `Ctrl+Shift+R`) e **inversión de
  colores** para lectura nocturna (`Ctrl+I`).
- **Modo presentación** (`F5`), **pantalla completa** (`F11`) e **impresión**
  (`Ctrl+P`, requiere `python3-pyside6.qtprintsupport`).
- Render nítido en pantallas **HiDPI** y restauración de la última página
  leída, geometría de ventana y preferencias.
- Tema claro/oscuro de la interfaz.
- Diálogo "Acerca De" con la descripción, el autor y su email.

## Dependencias

- `python3`
- `python3-pyside6.qtcore`, `python3-pyside6.qtgui`, `python3-pyside6.qtwidgets`
- `python3-pymupdf` (arrastra `python3-mupdf` y `libmupdf25.1`)
- Opcional: `python3-pyside6.qtprintsupport` (impresión)

Todas están disponibles en los repositorios de Debian 13 (trixie).

## Instalación

```bash
sudo apt install ./jfbpdf_1.1.1_all.deb
# o bien:
sudo dpkg -i jfbpdf_1.1.1_all.deb
```

Luego se puede lanzar desde el menú de aplicaciones (categoría *Gráficos*),
desde la terminal con `jfbpdf`, o asociado a archivos PDF:

```bash
jfbpdf documento.pdf
```

## Construcción del paquete .deb

```bash
./build.sh
```

Genera `jfbpdf_<versión>_all.deb` usando `dpkg-deb` (no requiere `pip`).
La versión vive únicamente en `jfbpdf/version.py`; el script la sustituye en
las plantillas de `packaging/` (`control.in`, página de manual).

## Ejecutar desde el código fuente

```bash
python3 -m jfbpdf archivo.pdf
python3 -m jfbpdf --version
python3 -m jfbpdf --self-test   # verificación interna sin interfaz
```

## Tests

La suite usa `unittest` (sin dependencias extra) y corre sin pantalla:

```bash
python3 -m unittest discover -s tests -t . -v
```

## Atajos de teclado

| Tecla                       | Acción                                  |
|-----------------------------|-----------------------------------------|
| `Ctrl+O`                    | Abrir documento                         |
| `Ctrl+W`                    | Cerrar documento                        |
| `Ctrl+F`                    | Buscar texto                            |
| `F3` / `Shift+F3`           | Coincidencia siguiente / anterior       |
| `Ctrl+P`                    | Imprimir                                |
| `Ctrl+Q`                    | Salir                                   |
| `Ctrl++` / `Ctrl+-`         | Acercar / alejar                        |
| `Ctrl+rueda`                | Zoom anclado en el cursor               |
| `Ctrl+R` / `Ctrl+Shift+R`   | Girar a la derecha / izquierda          |
| `Ctrl+I`                    | Invertir colores de las páginas         |
| `Ctrl+G`                    | Ir a la página…                         |
| `Espacio` / `Shift+Espacio` | Avanzar / retroceder una pantalla       |
| `PgDown` / `PgUp`           | Desplazarse por pantalla                |
| `Inicio` / `Fin`            | Primera / última página                 |
| `F5`                        | Modo presentación                       |
| `F9`                        | Mostrar/ocultar panel lateral           |
| `F11`                       | Pantalla completa                       |

## Licencia

MIT — ver [LICENSE](LICENSE).

Autor: **Federico Benitez** <benitez.federico@gmail.com>
