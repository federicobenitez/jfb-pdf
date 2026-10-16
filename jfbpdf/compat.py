"""Utilidades de compatibilidad con PyMuPDF.

PyMuPDF se importa de forma perezosa y centralizada aquí: el paquete moderno
se llama ``pymupdf`` y las versiones antiguas usan el alias histórico
``fitz``. Todo el proyecto obtiene el módulo a través de :func:`fitz`.
"""

from __future__ import annotations

from typing import Any

_module: Any = None


def fitz() -> Any:
    """Devuelve el módulo PyMuPDF, importándolo la primera vez."""
    global _module
    if _module is None:
        try:  # PyMuPDF moderno
            import pymupdf as m
        except ImportError:  # alias histórico
            import fitz as m  # type: ignore[no-redef]
        _module = m
    return _module


__all__ = ["fitz"]
