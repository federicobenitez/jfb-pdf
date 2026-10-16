"""Utilidades compartidas por la suite de tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest

# Los tests corren sin pantalla (CI, servidores): plataforma offscreen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jfbpdf.compat import fitz  # noqa: E402


def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def process_until(condition, timeout: float = 15.0) -> bool:
    """Procesa eventos hasta que ``condition()`` sea verdadera o venza el plazo."""
    app = qt_app()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return condition()


class QtTestCase(unittest.TestCase):
    """TestCase que garantiza una QApplication única."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app = qt_app()


def make_test_pdf(path: str, pages: int = 5, text: str = "texto buscable") -> None:
    """Genera un PDF A4 de prueba con texto buscable en cada página."""
    module = fitz()
    doc = module.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 96), f"JFB Pdf — página {i + 1}", fontsize=20)
        page.insert_text((72, 140), text, fontsize=12)
    doc.save(path)
    doc.close()


def make_encrypted_pdf(path: str, password: str = "clave") -> None:
    """Genera un PDF protegido con contraseña de usuario."""
    module = fitz()
    doc = module.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 96), "contenido secreto", fontsize=20)
    doc.save(
        path,
        encryption=module.PDF_ENCRYPT_AES_256,
        user_pw=password,
        owner_pw="owner",
    )
    doc.close()


class TempDirTestCase(QtTestCase):
    """TestCase con un directorio temporal y XDG_CONFIG_HOME aislado."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="jfbpdf-test-")
        self.tmp = self._tmp.name
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp

    def tearDown(self) -> None:
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self._tmp.cleanup()
        super().tearDown()

    def tmp_path(self, name: str) -> str:
        return os.path.join(self.tmp, name)
