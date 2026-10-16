"""Vista de documento con desplazamiento suave.

Estrategia para lograr un scroll fluido incluso con documentos grandes:

  * Cada página es un widget con tamaño fijo (reserva su espacio en el layout),
    por lo que la barra de desplazamiento refleja el documento completo.
  * Sólo se renderizan las páginas visibles (más un pequeño margen); el resto
    muestra un placeholder gris con su número.
  * El renderizado ocurre en un hilo de trabajo (:mod:`jfbpdf.renderer`), de
    modo que la interfaz nunca se bloquea.
  * Una caché LRU con presupuesto de memoria evita re-renderizar al volver
    atrás y libera memoria al alejarse.
"""

from __future__ import annotations

import bisect
from collections import OrderedDict
from typing import List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFrame,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .renderer import PageRenderer

#: Separación vertical entre páginas, en píxeles.
PAGE_MARGIN = 10

#: Cuántas páginas por encima/debajo del área visible se prerenderizan.
BUFFER_PAGES = 2

#: Presupuesto de la caché de páginas, en bytes (aprox. 4 bytes/píxel).
CACHE_BUDGET_BYTES = 150 * 1024 * 1024

MIN_ZOOM = 0.20
MAX_ZOOM = 8.0

#: Intervalo del temporizador de auto-scroll, en milisegundos (~60 fps).
AUTOSCROLL_INTERVAL_MS = 16
#: Zona muerta vertical (px) alrededor del ancla donde no hay desplazamiento.
AUTOSCROLL_DEAD_ZONE = 10
#: Velocidad máxima de auto-scroll, en píxeles por tick.
AUTOSCROLL_MAX_SPEED = 40.0


class _PageWidget(QWidget):
    """Widget que muestra una única página (o un placeholder mientras carga)."""

    def __init__(self, number: int) -> None:
        super().__init__()
        self._number = number
        self._pixmap: Optional[QPixmap] = None

    def set_page_size(self, size: QSize) -> None:
        self.setFixedSize(size)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def clear_pixmap(self) -> None:
        self._pixmap = None
        self.update()

    def has_pixmap(self) -> bool:
        return self._pixmap is not None

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillRect(self.rect(), QColor(246, 246, 248))
            painter.setPen(QColor(168, 168, 174))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, f"Página {self._number}"
            )
            painter.setPen(QColor(214, 214, 218))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class _AutoScrollIndicator(QWidget):
    """Marcador visual del punto de anclaje del auto-scroll (botón central)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._direction = 0  # -1 arriba, 0 neutro, +1 abajo
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def set_direction(self, direction: int) -> None:
        if direction != self._direction:
            self._direction = direction
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(90, 90, 90, 200), 1.5))
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawEllipse(3, 3, 30, 30)

        up = QPolygon([QPoint(18, 8), QPoint(11, 17), QPoint(25, 17)])
        down = QPolygon([QPoint(18, 28), QPoint(11, 19), QPoint(25, 19)])

        painter.setPen(Qt.PenStyle.NoPen)
        if self._direction < 0:
            painter.setBrush(QColor(60, 60, 60, 230))
            painter.drawPolygon(up)
        elif self._direction > 0:
            painter.setBrush(QColor(60, 60, 60, 230))
            painter.drawPolygon(down)
        else:
            painter.setBrush(QColor(170, 170, 175, 200))
            painter.drawPolygon(up)
            painter.drawPolygon(down)
        painter.end()


class PdfView(QWidget):
    """Área de visualización con scroll suave y renderizado perezoso."""

    #: (página_actual_1based, total_páginas)
    pageChanged = Signal(int, int)
    #: porcentaje de zoom actual
    zoomChanged = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pages: List[_PageWidget] = []
        self._page_sizes: List[Tuple[float, float]] = []  # (ancho, alto) en puntos
        self._tops: List[int] = []  # coordenada Y superior de cada página
        self._content_height = 0
        self._zoom = 1.0
        self._current_page = 0
        self._last_range: Optional[Tuple[int, int]] = None
        self._pending_fit_width = False
        self._pending_restore_page: Optional[int] = None
        self._renderer: Optional[PageRenderer] = None
        self._rendering = set()  # claves (índice, zoom) en vuelo
        self._cache: "OrderedDict[Tuple[int, float], QPixmap]" = OrderedDict()
        self._cache_bytes = 0

        # Estado del auto-scroll por botón central del ratón.
        self._autoscroll_active = False
        self._autoscroll_anchor_y = 0  # coordenada Y global del clic

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(PAGE_MARGIN)
        self._scroll.setWidget(self._container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Auto-scroll (botón central): temporizador + marcador sobre el viewport.
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(AUTOSCROLL_INTERVAL_MS)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)

        self._autoscroll_indicator = _AutoScrollIndicator(self._scroll.viewport())
        self._scroll.viewport().installEventFilter(self)

        self._autoscroll_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self, activated=self._stop_autoscroll
        )

    # ------------------------------------------------------ auto-scroll
    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._scroll.viewport():
            if event.type() == QEvent.Type.Wheel:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        self.zoom_in()
                    elif delta < 0:
                        self.zoom_out()
                    return True
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.MiddleButton:
                    if self._autoscroll_active:
                        self._stop_autoscroll()
                    elif self._pages:
                        self._start_autoscroll(event.position().toPoint())
                    return True
                if self._autoscroll_active:
                    # Cualquier otro botón detiene el auto-scroll.
                    self._stop_autoscroll()
                    return True
        return super().eventFilter(watched, event)

    def _start_autoscroll(self, pos: QPoint) -> None:
        self._autoscroll_active = True
        # Ancla en coordenadas globales (punto exacto del clic).
        self._autoscroll_anchor_y = self._scroll.viewport().mapToGlobal(pos).y()
        self._autoscroll_indicator.move(pos.x() - 18, pos.y() - 18)
        self._autoscroll_indicator.set_direction(0)
        self._autoscroll_indicator.show()
        self._scroll.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
        self._autoscroll_timer.start()

    def _stop_autoscroll(self) -> None:
        if not self._autoscroll_active:
            return
        self._autoscroll_active = False
        self._autoscroll_timer.stop()
        self._autoscroll_indicator.hide()
        self._scroll.viewport().unsetCursor()

    def _autoscroll_tick(self) -> None:
        if not self._autoscroll_active:
            return
        dy = QCursor.pos().y() - self._autoscroll_anchor_y
        speed = 0.0
        if dy > AUTOSCROLL_DEAD_ZONE:
            speed = min((dy - AUTOSCROLL_DEAD_ZONE) * 0.15, AUTOSCROLL_MAX_SPEED)
        elif dy < -AUTOSCROLL_DEAD_ZONE:
            speed = max((dy + AUTOSCROLL_DEAD_ZONE) * 0.15, -AUTOSCROLL_MAX_SPEED)

        direction = 1 if speed > 0 else (-1 if speed < 0 else 0)
        self._autoscroll_indicator.set_direction(direction)

        if speed != 0.0:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.value() + round(speed))

    # ------------------------------------------------------------------ doc
    def open_document(
        self,
        path: str,
        page_sizes: List[Tuple[float, float]],
        password: Optional[str] = None,
        start_page: Optional[int] = None,
    ) -> None:
        """Carga un documento. ``page_sizes`` es la lista de (ancho, alto) en puntos."""
        self.close_document()
        self._page_sizes = list(page_sizes)
        self._zoom = 1.0
        self._last_range = None
        self._pending_fit_width = True
        self._pending_restore_page = start_page

        for i in range(len(page_sizes)):
            widget = _PageWidget(i + 1)
            self._pages.append(widget)
            self._layout.addWidget(widget)

        self._renderer = PageRenderer(path, self, password=password)
        self._renderer.pageReady.connect(self._on_page_ready)
        self._renderer.start()

        self._relayout()
        if self._scroll.viewport().width() > 0:
            self.fit_width()
        if self._pending_restore_page is not None:
            self.goto_page(self._pending_restore_page)
            self._pending_restore_page = None
        self._schedule()
        self.pageChanged.emit(self.current_page(), len(self._pages))
        self.zoomChanged.emit(self._zoom * 100.0)

    def close_document(self) -> None:
        self._stop_autoscroll()
        if self._renderer is not None:
            self._renderer.shutdown()
            self._renderer = None
        for widget in self._pages:
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._pages.clear()
        self._page_sizes.clear()
        self._tops.clear()
        self._content_height = 0
        self._cache.clear()
        self._cache_bytes = 0
        self._rendering.clear()
        self._last_range = None
        self._current_page = 0
        self._pending_restore_page = None
    def has_document(self) -> bool:
        return bool(self._pages)

    # ---------------------------------------------------------------- zoom
    def set_zoom(self, zoom: float) -> None:
        if not self._pages:
            return
        zoom = min(MAX_ZOOM, max(MIN_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            return

        bar = self._scroll.verticalScrollBar()
        center_content = bar.value() + self._scroll.viewport().height() / 2.0
        ratio = center_content / max(1, self._content_height)

        self._zoom = zoom
        if self._renderer is not None:
            self._renderer.cancel_all()
        self._rendering.clear()
        self._clear_cache()
        self._last_range = None
        self._relayout()

        new_center = ratio * self._content_height
        bar.setValue(round(new_center - self._scroll.viewport().height() / 2.0))
        self._schedule()
        self.zoomChanged.emit(self._zoom * 100.0)

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def fit_width(self) -> None:
        if not self._pages:
            return
        vw = self._scroll.viewport().width()
        if vw <= 0:
            self._pending_fit_width = True
            return
        self._pending_fit_width = False
        max_w = max(w for w, _h in self._page_sizes)
        self.set_zoom((vw - 2 * PAGE_MARGIN) / max_w)
        if self._pending_restore_page is not None:
            self.goto_page(self._pending_restore_page)
            self._pending_restore_page = None

    def fit_page(self) -> None:
        if not self._pages:
            return
        vw = self._scroll.viewport().width()
        vh = self._scroll.viewport().height()
        if vw <= 0 or vh <= 0:
            return
        w, h = self._page_sizes[self._current_page]
        self.set_zoom(
            min((vw - 2 * PAGE_MARGIN) / w, (vh - 2 * PAGE_MARGIN) / h)
        )

    # ----------------------------------------------------------- navegación
    def goto_page(self, page: int) -> None:
        """Salta a la página ``page`` (1-based)."""
        if not self._pages:
            return
        index = min(len(self._pages) - 1, max(0, page - 1))
        self._scroll.verticalScrollBar().setValue(self._tops[index])

    def next_page(self) -> None:
        if not self._pages:
            return
        self.goto_page(min(self._current_page + 2, len(self._pages)))

    def prev_page(self) -> None:
        if not self._pages:
            return
        self.goto_page(max(self._current_page, 1))

    def first_page(self) -> None:
        if not self._pages:
            return
        self.goto_page(1)

    def last_page(self) -> None:
        if not self._pages:
            return
        self.goto_page(len(self._pages))

    def page_down(self) -> None:
        """Avanza una pantalla conservando el contexto (scroll suave)."""
        if not self._pages:
            return
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.value() + int(self._scroll.viewport().height() * 0.9))

    def page_up(self) -> None:
        """Retrocede una pantalla conservando el contexto (scroll suave)."""
        if not self._pages:
            return
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.value() - int(self._scroll.viewport().height() * 0.9))

    def current_page(self) -> int:
        """Devuelve la página actual (1-based)."""
        return self._current_page + 1

    def page_count(self) -> int:
        return len(self._pages)

    # ------------------------------------------------------------ internos
    def _relayout(self) -> None:
        tops: List[int] = []
        y = 0
        for widget, (w_pt, h_pt) in zip(self._pages, self._page_sizes):
            widget.set_page_size(
                QSize(round(w_pt * self._zoom), round(h_pt * self._zoom))
            )
            tops.append(y)
            y += widget.height() + PAGE_MARGIN
        self._tops = tops
        self._content_height = max(0, y - PAGE_MARGIN)
        width = (
            round(max(w for w, _h in self._page_sizes) * self._zoom)
            if self._page_sizes
            else 0
        )
        self._container.setFixedSize(width, self._content_height)

    def _page_index_at(self, y: int) -> int:
        if not self._tops:
            return 0
        index = bisect.bisect_right(self._tops, y) - 1
        return max(0, min(len(self._pages) - 1, index))

    def _visible_range(self) -> Tuple[int, int]:
        bar = self._scroll.verticalScrollBar()
        top = bar.value()
        bottom = top + self._scroll.viewport().height()
        first = self._page_index_at(top) - BUFFER_PAGES
        last = self._page_index_at(bottom) + BUFFER_PAGES
        return max(0, first), min(len(self._pages) - 1, last)

    def _on_scroll(self, _value: int) -> None:
        self._schedule()
        top_page = self._page_index_at(self._scroll.verticalScrollBar().value())
        if top_page != self._current_page:
            self._current_page = top_page
            self.pageChanged.emit(self._current_page + 1, len(self._pages))

    def _schedule(self) -> None:
        if self._renderer is None or not self._pages:
            return
        first, last = self._visible_range()
        if (first, last) == self._last_range:
            return
        self._last_range = (first, last)

        center = (first + last) // 2
        for i in range(first, last + 1):
            key = (i, self._zoom)
            if key in self._cache:
                self._cache.move_to_end(key)
                continue
            if key in self._rendering:
                continue
            self._rendering.add(key)
            self._renderer.request(i, self._zoom, abs(i - center))

        self._evict(first, last)

    def _on_page_ready(self, index: int, zoom: float, image: QImage) -> None:
        key = (index, zoom)
        self._rendering.discard(key)
        if abs(zoom - self._zoom) > 1e-6:
            return  # pedido obsoleto (cambió el zoom)
        if not (0 <= index < len(self._pages)):
            return
        pixmap = QPixmap.fromImage(image)
        self._cache[key] = pixmap
        self._cache_bytes += pixmap.width() * pixmap.height() * 4
        self._pages[index].set_pixmap(pixmap)
        self._trim_cache_by_budget()

    # -------------------------------------------------------------- caché
    def _clear_cache(self) -> None:
        for widget in self._pages:
            widget.clear_pixmap()
        self._cache.clear()
        self._cache_bytes = 0

    def _remove_from_cache(self, key: Tuple[int, float]) -> None:
        pixmap = self._cache.pop(key, None)
        if pixmap is None:
            return
        self._cache_bytes -= pixmap.width() * pixmap.height() * 4
        index, zoom = key
        if abs(zoom - self._zoom) < 1e-6 and 0 <= index < len(self._pages):
            self._pages[index].clear_pixmap()

    def _evict(self, first: int, last: int) -> None:
        for key in list(self._cache.keys()):
            index, zoom = key
            if abs(zoom - self._zoom) > 1e-6 or index < first or index > last:
                self._remove_from_cache(key)
        self._trim_cache_by_budget()

    def _trim_cache_by_budget(self) -> None:
        while self._cache_bytes > CACHE_BUDGET_BYTES and self._cache:
            oldest = next(iter(self._cache))
            self._remove_from_cache(oldest)

    # ------------------------------------------------------------ eventos
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule()
        if self._pending_fit_width and self._pages:
            QTimer.singleShot(0, self.fit_width)
