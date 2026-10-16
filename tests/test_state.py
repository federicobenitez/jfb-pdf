"""Tests de la persistencia del estado."""

from __future__ import annotations

import json
import os

from jfbpdf.state import AppState, config_dir

from .support import TempDirTestCase, make_test_pdf


class AppStateTest(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pdf = self.tmp_path("doc.pdf")
        make_test_pdf(self.pdf, pages=2)

    def test_roundtrip(self) -> None:
        state = AppState()
        state.set_last_page(self.pdf, 2)
        state.save_now()
        loaded = AppState()
        self.assertEqual(loaded.last_page(self.pdf), 2)

    def test_poda_de_archivos_inexistentes(self) -> None:
        state = AppState()
        state.last_pages["/no/existe.pdf"] = 4
        state.last_pages[self.pdf] = 1
        state.save_now()
        loaded = AppState()
        self.assertIsNone(loaded.last_page("/no/existe.pdf"))
        self.assertEqual(loaded.last_page(self.pdf), 1)

    def test_recientes_sin_duplicados_y_con_limite(self) -> None:
        state = AppState()
        for i in range(15):
            state.add_recent(f"/tmp/doc-{i}.pdf")
        state.add_recent("/tmp/doc-5.pdf")
        self.assertEqual(state.recent[0], "/tmp/doc-5.pdf")
        self.assertEqual(len(state.recent), 10)
        self.assertEqual(len(set(state.recent)), 10)

    def test_migracion_del_formato_antiguo(self) -> None:
        os.makedirs(config_dir(), exist_ok=True)
        legacy = os.path.join(config_dir(), "last_pages.json")
        with open(legacy, "w", encoding="utf-8") as fh:
            json.dump({self.pdf: 2}, fh)
        loaded = AppState()
        self.assertEqual(loaded.last_page(self.pdf), 2)

    def test_geometria_y_preferencias(self) -> None:
        state = AppState()
        state.zoom_mode = "page"
        state.invert = True
        state.dark_theme = True
        state.save_now()
        loaded = AppState()
        self.assertEqual(loaded.zoom_mode, "page")
        self.assertTrue(loaded.invert)
        self.assertTrue(loaded.dark_theme)

    def test_geometria_roundtrip_con_un_solo_base64(self) -> None:
        from PySide6.QtCore import QByteArray

        cruda = b"\x01\xd9\xd0\xcb\x00\x03\x00\x00"
        state = AppState()
        state.save_geometry(QByteArray(cruda))
        state.save_now()
        loaded = AppState()
        self.assertEqual(loaded.geometry, cruda)

    def test_geometria_vacia_no_pisa_la_guardada(self) -> None:
        from PySide6.QtCore import QByteArray

        state = AppState()
        state.save_geometry(QByteArray(b"\x01\x02\x03\x04"))
        state.save_geometry(QByteArray())  # p. ej. ventana ya oculta
        state.save_now()
        loaded = AppState()
        self.assertEqual(loaded.geometry, b"\x01\x02\x03\x04")


if __name__ == "__main__":
    import unittest

    unittest.main()
