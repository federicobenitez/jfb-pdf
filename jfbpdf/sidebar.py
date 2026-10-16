"""Panel lateral: índice (TOC) del documento."""

from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)


class OutlinePanel(QTreeWidget):
    """Árbol con el índice (TOC) del documento."""

    pageRequested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.itemClicked.connect(self._on_item_clicked)

    def set_toc(self, toc: Optional[List[Any]]) -> None:
        self.clear()
        stack: List[tuple] = []  # (nivel, item)
        for entry in toc or []:
            try:
                level = int(entry[0])
                title = str(entry[1])
                page = int(entry[2])
            except (TypeError, ValueError, IndexError):
                continue
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, page)
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.addTopLevelItem(item)
            stack.append((level, item))
        self.expandToDepth(0)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(page, int) and page > 0:
            self.pageRequested.emit(page)


class Sidebar(QDockWidget):
    """Dock con el índice del documento."""

    pageRequested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Panel lateral", parent)
        self.setObjectName("sidebar")
        self._outline = OutlinePanel(self)
        self.setWidget(self._outline)
        self.setMinimumWidth(150)
        self._outline.pageRequested.connect(self.pageRequested)

    def set_document(self, toc: Optional[List[Any]]) -> None:
        self._outline.set_toc(toc)
