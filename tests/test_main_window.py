"""Tests de la ventana principal."""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from jfbpdf.errors import InvalidPasswordError, PasswordRequiredError
from jfbpdf.main_window import MainWindow, read_pdf_info, read_pdf_toc
from jfbpdf.state import AppState

from .support import TempDirTestCase, make_encrypted_pdf, make_test_pdf


class ReadPdfInfoTest(TempDirTestCase):
    def test_documento_normal_y_protegido(self) -> None:
        path = self.tmp_path("doc.pdf")
        make_test_pdf(path, pages=3)
        count, sizes = read_pdf_info(path)
        self.assertEqual(count, 3)
        self.assertEqual(len(sizes), 3)

        encrypted = self.tmp_path("enc.pdf")
        make_encrypted_pdf(encrypted, password="clave")
        with self.assertRaises(PasswordRequiredError):
            read_pdf_info(encrypted)
        with self.assertRaises(InvalidPasswordError):
            read_pdf_info(encrypted, "mala")
        count, _sizes = read_pdf_info(encrypted, "clave")
        self.assertEqual(count, 1)

    def test_indice_del_documento(self) -> None:
        path = self.tmp_path("toc.pdf")
        make_test_pdf(path, pages=2)
        from jfbpdf.compat import fitz

        doc = fitz().open(path)
        doc.set_toc([[1, "Capítulo 1", 1], [1, "Capítulo 2", 2]])
        doc.saveIncr()
        doc.close()
        toc = read_pdf_toc(path)
        self.assertEqual([entry[1] for entry in toc], ["Capítulo 1", "Capítulo 2"])


class MainWindowTest(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.tmp_path("doc.pdf")
        make_test_pdf(self.path, pages=5)
        self.window = MainWindow()
        self.window.open_file(self.path)
        self.window.show()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        super().tearDown()

    def _key(self, key: Qt.Key) -> None:
        QApplication.sendEvent(
            self.window, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        )
        self.app.processEvents()

    def test_apertura(self) -> None:
        self.assertEqual(self.window._view.page_count(), 5)
        self.assertIn("doc.pdf", self.window.windowTitle())
        self.assertTrue(self.window._state.recent)
        self.assertEqual(self.window._state.recent[0], self.path)

    def test_home_y_end(self) -> None:
        self.window._view.goto_page(3)
        self.app.processEvents()
        self._key(Qt.Key.Key_Home)
        self.assertEqual(self.window._view.current_page(), 1)
        self._key(Qt.Key.Key_End)
        self.assertEqual(self.window._view.current_page(), 5)
        self.assertEqual(self.window._status_page.text(), "Página 5 de 5")

    def test_home_deshabilitado_al_editar_pagina(self) -> None:
        self.window._page_edit.setFocus()
        self.app.processEvents()
        self.assertFalse(self.window.act_first.isEnabled())
        self.assertFalse(self.window.act_last.isEnabled())
        self.window._view.setFocus()
        self.app.processEvents()
        self.assertTrue(self.window.act_first.isEnabled())
        self.assertTrue(self.window.act_last.isEnabled())

    def test_guardado_al_cerrar(self) -> None:
        self.window._view.goto_page(4)
        self.app.processEvents()
        self.window.close()
        loaded = AppState()
        self.assertEqual(loaded.last_page(self.path), 4)

    def test_cerrar_documento(self) -> None:
        self.window.close_document()
        self.assertFalse(self.window._view.has_document())
        self.assertEqual(self.window.windowTitle(), "JFB Pdf")


if __name__ == "__main__":
    import unittest

    unittest.main()
