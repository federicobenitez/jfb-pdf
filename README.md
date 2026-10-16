# JFB Pdf

Lector de PDF ligero para Debian, escrito en Python con **PySide6** (Qt6) y
**PyMuPDF**. Prioriza un **desplazamiento suave** mediante renderizado
perezoso en segundo plano y una caché con presupuesto de memoria.

## Características

- Abrir PDF desde el menú, arrastrar y soltar, o por línea de comandos.
- Scroll continuo y fluido: sólo se renderizan las páginas visibles.
- **Auto-scroll**: pulsa el botón central del ratón y mueve el cursor arriba/abajo
  del punto de anclaje para desplazarte de forma continua (clic o `Esc` para salir).
- Zoom (acercar/alejar/ajustar ancho/ajustar página) y `Ctrl + rueda`.
- Navegación por páginas, "Ir a la página…" y atajos de teclado.
- Diálogo "Acerca De" con la descripción, el autor y su email.

## Dependencias

- `python3`
- `python3-pyside6.qtcore`, `python3-pyside6.qtgui`, `python3-pyside6.qtwidgets`
- `python3-pymupdf` (arrastra `python3-mupdf` y `libmupdf25.1`)

Todas están disponibles en los repositorios de Debian 13 (trixie).

## Instalación

```bash
sudo apt install ./jfbpdf_1.0.0_all.deb
# o bien:
sudo dpkg -i jfbpdf_1.0.0_all.deb
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

Genera `jfbpdf_1.0.0_all.deb` usando `dpkg-deb` (no requiere `pip`).

## Ejecutar desde el código fuente

```bash
python3 -m jfbpdf archivo.pdf
python3 -m jfbpdf --version
python3 -m jfbpdf --self-test   # verificación interna sin interfaz
```

## Atajos de teclado

| Tecla            | Acción                  |
|------------------|-------------------------|
| `Ctrl+O`         | Abrir documento         |
| `Ctrl+Q`         | Salir                   |
| `Ctrl++` / `Ctrl+-` | Acercar / alejar     |
| `Ctrl+G`         | Ir a la página…         |
| `Espacio` / `Shift+Espacio` | Avanzar / retroceder una pantalla |
| `PgDown` / `PgUp` | Desplazarse por pantalla |
| `Inicio` / `Fin` | Primera / última página |
| `F11`            | Pantalla completa       |

## Licencia

MIT — ver [LICENSE](LICENSE).

Autor: **Federico Benitez** <benitez.federico@gmail.com>
