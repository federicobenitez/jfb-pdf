"""Panel lateral: índice del documento y miniaturas de páginas."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QDockWidget,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

logger = logging.getLogger(__name__)

#: Cantidad de miniaturas extra solicitadas alrededor del área visible.
THUMBNAIL_BUFFER = 3
THUMBNAIL_ICON_SIZE = QSize(110, 150)


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


class ThumbnailsPanel(QListWidget):
    """Lista de miniaturas renderizadas bajo demanda."""

    pageRequested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._view = None
        self._count = 0
        self._pending: set = set()
        self.setIconSize(THUMBNAIL_ICON_SIZE)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.itemClicked.connect(self._on_item_clicked)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.refresh())

    # ------------------------------------------------------------- API
    def bind_view(self, view) -> None:
        """Conecta con la vista para pedir y recibir miniaturas."""
        self._view = view
        view.thumbnailReady.connect(self._on_thumbnail_ready)
        view.rotationChanged.connect(lambda _rotation: self.reset())

    def set_page_count(self, count: int) -> None:
        self._count = max(0, count)
        self.clear()
        self._pending.clear()
        for i in range(self._count):
            QListWidgetItem(str(i + 1), self)
        self.refresh()

    def reset(self) -> None:
        """Descarta lo cargado (p. ej. tras rotar) y vuelve a pedirlo."""
        self._pending.clear()
        empty = QIcon()
        for i in range(self._count):
            item = self.item(i)
            if item is not None:
                item.setIcon(empty)
        self.refresh()

    def set_current_page(self, page: int) -> None:
        if not self.isVisible() or not (1 <= page <= self._count):
            return
        self.blockSignals(True)
        try:
            self.setCurrentRow(page - 1)
        finally:
            self.blockSignals(False)

    def refresh(self) -> None:
        """Solicita las miniaturas del área visible."""
        if self._view is None or self._count == 0 or not self.isVisible():
            return
        bar = self.verticalScrollBar()
        top = bar.value()
        bottom = top + max(self.viewport().height(), 1)
        row_height = max(1, self.sizeHintForRow(0))
        first = max(0, top // row_height - THUMBNAIL_BUFFER)
        last = min(self._count - 1, bottom // row_height + THUMBNAIL_BUFFER)
        for index in range(first, last + 1):
            if index not in self._pending:
                self._pending.add(index)
                self._view.request_thumbnail(index)

    # -------------------------------------------------------- eventos
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.pageRequested.emit(self.row(item) + 1)

    def _on_thumbnail_ready(self, index: int, image: QImage) -> None:
        self._pending.discard(index)
        if not (0 <= index < self._count):
            return
        item = self.item(index)
        if item is None:
            return
        item.setIcon(QIcon(QPixmap.fromImage(image)))


class Sidebar(QDockWidget):
    """Dock con pestañas de índice y miniaturas."""

    pageRequested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Panel lateral", parent)
        self.setObjectName("sidebar")
        self._tabs = QTabWidget(self)
        self._outline = OutlinePanel(self)
        self._thumbs = ThumbnailsPanel(self)
        self._tabs.addTab(self._outline, "Índice")
        self._tabs.addTab(self._thumbs, "Miniaturas")
        self.setWidget(self._tabs)
        self.setMinimumWidth(150)
        self._outline.pageRequested.connect(self.pageRequested)
        self._thumbs.pageRequested.connect(self.pageRequested)

    def bind_view(self, view) -> None:
        self._thumbs.bind_view(view)

    def set_document(self, toc: Optional[List[Any]], page_count: int) -> None:
        self._outline.set_toc(toc)
        self._thumbs.set_page_count(page_count)

    def set_current_page(self, page: int) -> None:
        self._thumbs.set_current_page(page)
