"""Diálogo "Acerca De"."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from .version import APP_NAME, AUTHOR_EMAIL, AUTHOR_NAME, DESCRIPTION, VERSION


class AboutDialog(QDialog):
    """Muestra la descripción de la aplicación junto al autor y su email."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Acerca de {APP_NAME}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(APP_NAME)
        title_font = QFont(title.font())
        title_font.setPointSizeF(title_font.pointSizeF() * 1.6)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        version = QLabel(f"Versión {VERSION}")
        layout.addWidget(version)

        description = QLabel(DESCRIPTION)
        description.setWordWrap(True)
        description.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(description)

        author = QLabel(f"Autor: {AUTHOR_NAME}")
        author.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(author)

        email = QLabel(f"Email: {AUTHOR_EMAIL}")
        email.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        email.setOpenExternalLinks(True)
        email.setText(f'Email: <a href="mailto:{AUTHOR_EMAIL}">{AUTHOR_EMAIL}</a>')
        layout.addWidget(email)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
