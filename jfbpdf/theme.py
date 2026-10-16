"""Tema visual de la aplicación (claro/oscuro)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

_ORIGINAL_STYLE_ATTR = "_jfbpdf_original_style"


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(35, 35, 35))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(110, 110, 110))
    return palette


def apply_theme(app: QApplication, dark: bool) -> None:
    """Aplica el tema oscuro (Fusion) o restaura el estilo original."""
    if not hasattr(app, _ORIGINAL_STYLE_ATTR):
        style = app.style()
        setattr(app, _ORIGINAL_STYLE_ATTR, style.objectName() if style else "")
    if dark:
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            app.setStyle(fusion)
        app.setPalette(_dark_palette())
    else:
        name = getattr(app, _ORIGINAL_STYLE_ATTR, "")
        style = QStyleFactory.create(name) if name else None
        if style is not None:
            app.setStyle(style)
        app.setPalette(app.style().standardPalette())
