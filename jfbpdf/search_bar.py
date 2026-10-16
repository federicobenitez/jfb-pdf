"""Barra de búsqueda de texto."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QWidget,
)

#: Retardo tras la última tecla antes de lanzar la búsqueda, en milisegundos.
SEARCH_DELAY_MS = 300


class SearchBar(QWidget):
    """Barra inferior con campo de texto, contador y navegación de coincidencias."""

    searchRequested = Signal(str)
    nextRequested = Signal()
    prevRequested = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("Buscar en el documento…")
        self._edit.setClearButtonEnabled(True)
        self._edit.setMinimumWidth(220)
        layout.addWidget(self._edit, 1)

        self._status = QLabel("", self)
        self._status.setMinimumWidth(120)
        layout.addWidget(self._status)

        self._prev_button = QToolButton(self)
        self._prev_button.setText("↑")
        self._prev_button.setToolTip("Coincidencia anterior (Shift+F3)")
        layout.addWidget(self._prev_button)

        self._next_button = QToolButton(self)
        self._next_button.setText("↓")
        self._next_button.setToolTip("Coincidencia siguiente (F3 / Enter)")
        layout.addWidget(self._next_button)

        self._close_button = QToolButton(self)
        self._close_button.setText("✕")
        self._close_button.setToolTip("Cerrar búsqueda (Esc)")
        layout.addWidget(self._close_button)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEARCH_DELAY_MS)
        self._timer.timeout.connect(self._emit_search)

        self._edit.textChanged.connect(lambda _text: self._timer.start())
        self._edit.returnPressed.connect(self.nextRequested)
        self._prev_button.clicked.connect(self.prevRequested)
        self._next_button.clicked.connect(self.nextRequested)
        self._close_button.clicked.connect(self.closed)

        # Esc cierra la barra sólo cuando el foco está dentro de ella (si no,
        # chocaría con el Esc del auto-scroll de la vista).
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.closed)

    # -------------------------------------------------------------- API
    def search_text(self) -> str:
        return self._edit.text().strip()

    def clear(self) -> None:
        """Vacía el campo sin disparar una búsqueda."""
        self._timer.stop()
        self._edit.blockSignals(True)
        try:
            self._edit.clear()
        finally:
            self._edit.blockSignals(False)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def focus_edit(self) -> None:
        """Muestra el foco en el campo; si ya había texto, lo selecciona."""
        self._edit.setFocus()
        self._edit.selectAll()

    def retrigger(self) -> None:
        """Repite la búsqueda actual (p. ej. al reabrir la barra)."""
        self._emit_search()

    def _emit_search(self) -> None:
        self._timer.stop()
        self.searchRequested.emit(self.search_text())
