"""Tests de la geometría de rotación de la vista."""

from __future__ import annotations

import unittest

from jfbpdf.pdf_view import _rotate_rect

RECT = (10.0, 20.0, 30.0, 40.0)
WIDTH = 200.0
HEIGHT = 100.0


class RotateRectTest(unittest.TestCase):
    def test_identity(self) -> None:
        self.assertEqual(_rotate_rect(RECT, WIDTH, HEIGHT, 0), RECT)
        self.assertEqual(_rotate_rect(RECT, WIDTH, HEIGHT, 360), RECT)

    def test_90_grados(self) -> None:
        # (h - y1, x0, h - y0, x1)
        self.assertEqual(_rotate_rect(RECT, WIDTH, HEIGHT, 90), (60.0, 10.0, 80.0, 30.0))

    def test_180_grados(self) -> None:
        self.assertEqual(
            _rotate_rect(RECT, WIDTH, HEIGHT, 180), (170.0, 60.0, 190.0, 80.0)
        )

    def test_270_grados(self) -> None:
        # (y0, w - x1, y1, w - x0)
        self.assertEqual(
            _rotate_rect(RECT, WIDTH, HEIGHT, 270), (20.0, 170.0, 40.0, 190.0)
        )

    def test_rotaciones_consecutivas(self) -> None:
        rect = RECT
        for _ in range(4):
            rect = _rotate_rect(rect, WIDTH, HEIGHT, 90)
        self.assertEqual(rect, RECT)


if __name__ == "__main__":
    unittest.main()
