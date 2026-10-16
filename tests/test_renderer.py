"""Tests del motor de renderizado."""

from __future__ import annotations

import os

from jfbpdf.compat import fitz
from jfbpdf.renderer import MAX_RENDER_PIXELS, PageRenderer, RenderKey, _page_to_image

from .support import QtTestCase, make_encrypted_pdf, make_test_pdf, process_until


class PageToImageTest(QtTestCase):
    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="jfbpdf-render-")
        self.path = os.path.join(self._tmp.name, "doc.pdf")
        make_test_pdf(self.path, pages=2)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        super().tearDown()

    def test_render_basico(self) -> None:
        doc = fitz().open(self.path)
        try:
            image = _page_to_image(doc.load_page(0), RenderKey(0, 1.0))
            self.assertIsNotNone(image)
            self.assertEqual((image.width(), image.height()), (595, 842))
        finally:
            doc.close()

    def test_render_hidpi(self) -> None:
        doc = fitz().open(self.path)
        try:
            image = _page_to_image(doc.load_page(0), RenderKey(0, 1.0, dpr=2.0))
            self.assertEqual((image.width(), image.height()), (1190, 1684))
            self.assertEqual(image.devicePixelRatio(), 2.0)
        finally:
            doc.close()

    def test_rotacion_intercambia_dimensiones(self) -> None:
        doc = fitz().open(self.path)
        try:
            image = _page_to_image(doc.load_page(0), RenderKey(0, 1.0, rotation=90))
            self.assertEqual((image.width(), image.height()), (842, 595))
        finally:
            doc.close()

    def test_inversion_de_colores(self) -> None:
        doc = fitz().open(self.path)
        try:
            page = doc.load_page(0)
            normal = _page_to_image(page, RenderKey(0, 1.0))
            inverted = _page_to_image(page, RenderKey(0, 1.0, invert=True))
            self.assertNotEqual(normal.pixelColor(5, 5), inverted.pixelColor(5, 5))
        finally:
            doc.close()

    def test_limite_de_pixeles(self) -> None:
        doc = fitz().open(self.path)
        try:
            image = _page_to_image(doc.load_page(0), RenderKey(0, 8.0))
            self.assertIsNotNone(image)
            self.assertLessEqual(image.width() * image.height(), MAX_RENDER_PIXELS * 1.05)
        finally:
            doc.close()


class RendererWorkerTest(QtTestCase):
    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="jfbpdf-render-")
        self.path = os.path.join(self._tmp.name, "doc.pdf")
        make_test_pdf(self.path, pages=3)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        super().tearDown()

    def test_renderiza_pagina(self) -> None:
        renderer = PageRenderer(self.path)
        ready = []
        renderer.pageReady.connect(lambda key, image: ready.append((key, image)))
        renderer.start()
        renderer.request(RenderKey(1, 1.0))
        self.assertTrue(process_until(lambda: len(ready) == 1))
        key, image = ready[0]
        self.assertEqual(key.index, 1)
        self.assertFalse(image.isNull())
        renderer.shutdown()

    def test_error_con_contrasena_incorrecta(self) -> None:
        encrypted = os.path.join(self._tmp.name, "enc.pdf")
        make_encrypted_pdf(encrypted, password="clave")
        renderer = PageRenderer(encrypted, password="mala")
        errors = []
        renderer.error.connect(errors.append)
        renderer.start()
        self.assertTrue(process_until(lambda: bool(errors)))
        renderer.shutdown()

    def test_busqueda_reporta_coincidencias(self) -> None:
        renderer = PageRenderer(self.path)
        hits = []
        finished = []
        renderer.searchPageHits.connect(
            lambda gen, page, rects: hits.append((page, rects))
        )
        renderer.searchFinished.connect(finished.append)
        renderer.start()
        renderer.start_search("texto buscable", generation=1)
        self.assertTrue(process_until(lambda: bool(finished)))
        self.assertEqual(finished[0], 1)
        self.assertEqual([page for page, _rects in hits], [0, 1, 2])
        self.assertTrue(all(rects for _page, rects in hits))
        renderer.shutdown()

    def test_cierre_con_pedidos_pendientes(self) -> None:
        renderer = PageRenderer(self.path)
        renderer.start()
        for i in range(3):
            renderer.request(RenderKey(i, 1.0))
        renderer.shutdown()  # no debe colgarse ni emitir a receptores muertos


if __name__ == "__main__":
    import unittest

    unittest.main()
