"""Punto de entrada de la aplicación JFB Pdf."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import List, Optional

from .version import APP_NAME, VERSION


def _fitz():
    try:  # PyMuPDF moderno
        import pymupdf as m
    except ImportError:  # alias histórico
        import fitz as m  # type: ignore
    return m


def _generate_test_pdf(path: str, pages: int = 5) -> None:
    fitz = _fitz()
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text(
            (72, 96),
            f"JFB Pdf — página de prueba {i + 1}",
            fontsize=20,
        )
    doc.save(path)
    doc.close()


def _self_test() -> int:
    """Verificación sin interfaz del flujo completo (render + lectura de PDF)."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from .main_window import read_pdf_info
    from .renderer import _page_to_image

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        _generate_test_pdf(path, 5)

        count, sizes = read_pdf_info(path)
        assert count == 5 and len(sizes) == 5, "lectura de páginas incorrecta"

        doc = _fitz().open(path)
        try:
            for i in range(count):
                image = _page_to_image(doc.load_page(i), 1.5)
                assert image is not None and not image.isNull(), f"página {i} vacía"
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

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)

    from .main_window import MainWindow

    window = MainWindow()
    window.show()
    if args.file:
        window.open_file(args.file)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
