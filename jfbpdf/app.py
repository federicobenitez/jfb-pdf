"""Punto de entrada de la aplicación JFB Pdf."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
from typing import List, Optional

from .version import APP_ID, APP_NAME, VERSION

logger = logging.getLogger("jfbpdf")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _application_icon():
    """Icono de la aplicación: del tema del escritorio o del SVG empaquetado."""
    from PySide6.QtGui import QIcon

    icon = QIcon.fromTheme(APP_ID)
    if not icon.isNull():
        return icon
    svg = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "packaging",
        "jfbpdf.svg",
    )
    if os.path.exists(svg):
        icon = QIcon(svg)
    return icon


def _generate_test_pdf(path: str, pages: int = 5) -> None:
    from .compat import fitz

    doc = fitz().open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text(
            (72, 96),
            f"JFB Pdf — página de prueba {i + 1}",
            fontsize=20,
        )
        page.insert_text((72, 140), "texto buscable", fontsize=12)
    doc.save(path)
    doc.close()


def _self_test() -> int:
    """Verificación sin interfaz del flujo completo (render + lectura de PDF)."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # El self-test no debe tocar el estado real del usuario (geometría,
    # recientes, última página): usa su propio directorio de configuración.
    config_tmp = tempfile.mkdtemp(prefix="jfbpdf-self-test-")
    os.environ["XDG_CONFIG_HOME"] = config_tmp

    from .main_window import read_pdf_info, read_pdf_toc
    from .renderer import RenderKey, _page_to_image

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        _generate_test_pdf(path, 5)

        count, sizes = read_pdf_info(path)
        assert count == 5 and len(sizes) == 5, "lectura de páginas incorrecta"
        assert read_pdf_toc(path) == [], "el PDF generado no debería tener índice"

        from .compat import fitz

        doc = fitz().open(path)
        try:
            for i in range(count):
                key = RenderKey(i, 1.5, rotation=0, invert=False, dpr=1.0)
                image = _page_to_image(doc.load_page(i), key)
                assert image is not None and not image.isNull(), f"página {i} vacía"
                # Rotación: las dimensiones deben intercambiarse en 90/270.
                rotated = _page_to_image(
                    doc.load_page(i), RenderKey(i, 1.0, 90, False, 1.0)
                )
                assert rotated is not None and not rotated.isNull(), "rotación vacía"
                assert (
                    rotated.width() == 842 and rotated.height() == 595
                ), "rotación de 90° incorrecta"
        finally:
            doc.close()

        # Smoke test del stack completo con la GUI (offscreen o con display).
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from .main_window import MainWindow

        window = MainWindow()
        window.open_file(path)
        app.processEvents()
        window._view.set_zoom(1.2)
        app.processEvents()
        assert window._view.page_count() == 5, "la vista no cargó el documento"
        window.close_document()
        window.close()
        print("SELF-TEST OK")
        return 0
    finally:
        shutil.rmtree(config_tmp, ignore_errors=True)
        try:
            os.unlink(path)
        except OSError:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jfbpdf",
        description=f"{APP_NAME}: lector de PDF ligero para Debian.",
    )
    parser.add_argument("file", nargs="?", help="archivo PDF a abrir")
    parser.add_argument("--version", action="store_true", help="muestra la versión")
    parser.add_argument(
        "--self-test", action="store_true", help="ejecuta una verificación interna"
    )
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {VERSION}")
        return 0

    if args.self_test:
        return _self_test()

    _configure_logging()

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(_application_icon())

    from .main_window import MainWindow

    window = MainWindow()
    window.show()
    if args.file:
        window.open_file(args.file)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
