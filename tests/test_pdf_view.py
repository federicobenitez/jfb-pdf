"""Tests de la vista de páginas (offscreen)."""

from __future__ import annotations

import os
import unittest

import jfbpdf.pdf_view as pdf_view
from jfbpdf.pdf_view import PdfView

from .support import QtTestCase, make_test_pdf, process_until

PAGE = (595.0, 842.0)
SIZES = [PAGE, PAGE, PAGE, PAGE, PAGE]


class PdfViewTest(QtTestCase):
    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="jfbpdf-view-")
        self.path = os.path.join(self._tmp.name, "doc.pdf")
        make_test_pdf(self.path, pages=5)
        self.view = PdfView()
        self.view.resize(800, 900)
        self.view.show()

    def tearDown(self) -> None:
        self.view.close_document()
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()
        self._tmp.cleanup()
        super().tearDown()

    def _open(self) -> None:
        self.view.open_document(self.path, SIZES)
        self.assertTrue(
            process_until(lambda: self.view._pages[0].has_pixmap()),
            "la primera página no se renderizó",
        )

    def test_apertura_y_zoom_ajustado(self) -> None:
        self._open()
        self.assertTrue(self.view.has_document())
        self.assertEqual(self.view.page_count(), 5)
        self.assertEqual(self.view.zoom_mode(), "width")
        self.assertGreater(self.view._zoom, 0)

    def test_ultima_pagina_informa_pagina_correcta(self) -> None:
        self._open()
        self.view.last_page()
        self.app.processEvents()
        self.assertEqual(self.view.current_page(), 5)

    def test_goto_page_y_restauracion(self) -> None:
        self.view.open_document(self.path, SIZES, start_page=4)
        self.app.processEvents()
        self.assertTrue(
            process_until(lambda: self.view.current_page() == 4),
            f"página restaurada: {self.view.current_page()}",
        )

    def test_rotacion(self) -> None:
        self._open()
        self.view.rotate(90)
        self.app.processEvents()
        self.assertEqual(self.view.rotation(), 90)
        self.assertEqual(self.view._rotated_size(*PAGE), (842.0, 595.0))
        self.assertEqual(self.view.rotation(), 90)  # detección de cambios

    def test_inversion(self) -> None:
        self._open()
        self.assertFalse(self.view.is_inverted())
        self.view.set_invert(True)
        self.assertTrue(self.view.is_inverted())

    def test_cache_no_descarta_la_pagina_visible(self) -> None:
        old_budget = pdf_view.CACHE_BUDGET_BYTES
        pdf_view.CACHE_BUDGET_BYTES = 1
        try:
            self._open()
            self.app.processEvents()
            self.assertEqual(self.view._cache_bytes, 0)
            self.assertTrue(self.view._pages[0].has_pixmap())
        finally:
            pdf_view.CACHE_BUDGET_BYTES = old_budget

    def test_busqueda(self) -> None:
        self._open()
        self.view.start_search("texto buscable")
        self.assertTrue(process_until(lambda: not self.view.is_searching()))
        self.assertEqual(self.view._hit_count, 5)
        self.view.search_next()
        self.app.processEvents()
        self.view.search_prev()
        self.app.processEvents()
        self.view.clear_search()
        self.assertEqual(self.view._hit_count, 0)


if __name__ == "__main__":
    unittest.main()
