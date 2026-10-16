"""Vista de documento con desplazamiento suave.

Estrategia para lograr un scroll fluido incluso con documentos grandes:

  * Cada página es un widget con tamaño fijo (reserva su espacio en el layout),
    por lo que la barra de desplazamiento refleja el documento completo.
  * Sólo se renderizan las páginas visibles (más un pequeño margen); el resto
    muestra un placeholder gris con su número.
  * El renderizado ocurre en un hilo de trabajo (:mod:`jfbpdf.renderer`), de
    modo que la interfaz nunca se bloquea.
  * Una caché LRU con presupuesto de memoria evita re-renderizar al volver
    atrás y libera memoria al alejarse. Las entradas de niveles de zoom
    recientes se conservan mientras haya presupuesto.
"""

from __future__ import annotations

import bisect
import logging
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
)
from PySide6.QtWidgets import (
    QFrame,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .renderer import PageRenderer, RenderKey

logger = logging.getLogger(__name__)

#: Separación vertical entre páginas, en píxeles.
PAGE_MARGIN = 10

#: Cuántas páginas por encima/debajo del área visible se prerenderizan.
BUFFER_PAGES = 2

#: Cuántas páginas fuera del área visible se conservan cacheadas.
KEEP_PAGES = 6

#: Presupuesto de la caché de páginas, en bytes (aprox. 4 bytes/píxel).
CACHE_BUDGET_BYTES = 150 * 1024 * 1024

MIN_ZOOM = 0.20
MAX_ZOOM = 8.0

#: Zoom fijo con el que se renderizan las miniaturas del panel lateral.
THUMBNAIL_ZOOM = 0.10

#: Prioridad de los pedidos de miniatura (los más bajos se atienden antes).
THUMBNAIL_PRIORITY = 10_000

#: Intervalo del temporizador de auto-scroll, en milisegundos (~60 fps).
AUTOSCROLL_INTERVAL_MS = 16
#: Zona muerta vertical (px) alrededor del ancla donde no hay desplazamiento.
AUTOSCROLL_DEAD_ZONE = 10
#: Velocidad máxima de auto-scroll, en píxeles por tick.
AUTOSCROLL_MAX_SPEED = 40.0

Rect = Tuple[float, float, float, float]


def _rotate_rect(rect: Rect, width: float, height: float, rotation: int) -> Rect:
    """Rota un rect (en puntos, con origen arriba-izquierda) según ``rotation``.

    ``rotation`` es el giro horario aplicado a la vista (0, 90, 180 o 270),
    el mismo que usa MuPDF al renderizar.
    """
    x0, y0, x1, y1 = rect
    rotation %= 360
    if rotation == 90:
        return (height - y1, x0, height - y0, x1)
    if rotation == 180:
        return (width - x1, height - y1, width - x0, height - y0)
    if rotation == 270:
        return (y0, width - x1, y1, width - x0)
    return rect


class _PageWidget(QWidget):
    """Widget que muestra una única página (o un placeholder mientras carga)."""

    def __init__(self, number: int) -> None:
        super().__init__()
        self._number = number
        self._pixmap: Optional[QPixmap] = None
        self._page_size_pt: Tuple[float, float] = (1.0, 1.0)
        self._highlights: List[Rect] = []
        self._current_highlight = -1

    def set_page_size(self, size: QSize) -> None:
        self.setFixedSize(size)

    def set_page_size_pt(self, width: float, height: float) -> None:
        """Tamaño de la página en puntos (ya rotado)."""
        self._page_size_pt = (width, height)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def clear_pixmap(self) -> None:
        self._pixmap = None
        self.update()

    def has_pixmap(self) -> bool:
        return self._pixmap is not None

    def set_highlights(self, rects: List[Rect], current: int = -1) -> None:
        """Resalta coincidencias de búsqueda (rects en puntos, ya rotados)."""
        self._highlights = rects
        self._current_highlight = current
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if self._pixmap is not None:
            dpr = self._pixmap.devicePixelRatio() or 1.0
            logical = QSize(
                round(self._pixmap.width() / dpr),
                round(self._pixmap.height() / dpr),
            )
            if logical == self.size():
                painter.drawPixmap(0, 0, self._pixmap)
            else:
                # Render reducido por límite de memoria: se reescala al widget.
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(self.rect(), self._pixmap)
        else:
            painter.fillRect(self.rect(), QColor(246, 246, 248))
            painter.setPen(QColor(168, 168, 174))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, f"Página {self._number}"
            )
            painter.setPen(QColor(214, 214, 218))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        if self._highlights:
            self._paint_highlights(painter)
        painter.end()

    def _paint_highlights(self, painter: QPainter) -> None:
        w_pt, h_pt = self._page_size_pt
        if w_pt <= 0 or h_pt <= 0:
            return
        sx = self.width() / w_pt
        sy = self.height() / h_pt
        normal = QColor(255, 214, 0, 110)
        current = QColor(255, 140, 0, 165)
        border = QPen(QColor(180, 120, 0, 160), 1)
        painter.setPen(border)
        for i, (x0, y0, x1, y1) in enumerate(self._highlights):
            rect = QRectF(x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy)
            painter.fillRect(rect, current if i == self._current_highlight else normal)
            painter.drawRect(rect)


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
    #: rotación de la vista en grados (0/90/180/270)
    rotationChanged = Signal(int)
    #: fallo al abrir o renderizar el documento (mensaje)
    renderError = Signal(str)
    #: True mientras hay renders pendientes
    busyChanged = Signal(bool)
    #: miniatura lista (índice de página, imagen)
    thumbnailReady = Signal(int, QImage)
    #: (posición 1-based de la coincidencia actual, total de coincidencias)
    searchStatusChanged = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pages: List[_PageWidget] = []
        self._page_sizes: List[Tuple[float, float]] = []  # (ancho, alto) en puntos
        self._tops: List[int] = []  # coordenada Y superior de cada página
        self._content_height = 0
        self._zoom = 1.0
        self._zoom_mode = "free"  # "free" | "width" | "page"
        self._rotation = 0
        self._invert = False
        self._dpr = 1.0
        self._current_page = 0
        self._last_range: Optional[Tuple[int, int]] = None
        self._pending_fit_width = False
        self._pending_restore_page: Optional[int] = None
        self._programmatic_scroll = False
        self._renderer: Optional[PageRenderer] = None
        self._rendering = set()  # claves RenderKey en vuelo (de la vista)
        self._cache: "OrderedDict[RenderKey, QPixmap]" = OrderedDict()
        self._cache_bytes = 0
        self._busy = False

        # Búsqueda de texto
        self._search_gen = 0
        self._search_text = ""
        self._searching = False
        self._hits: Dict[int, List[Rect]] = {}
        self._hit_count = 0
        self._hit_pos: Optional[Tuple[int, Rect]] = None
        self._flat_hits_cache: Optional[List[Tuple[int, Rect]]] = None

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

    # ------------------------------------------------------ auto-scroll
    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._scroll.viewport():
            if event.type() == QEvent.Type.Wheel:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta:
                        # El zoom se ancla en el punto bajo el cursor.
                        anchor = event.position().y()
                        if delta > 0:
                            self.zoom_in(anchor_y=anchor)
                        else:
                            self.zoom_out(anchor_y=anchor)
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

    def is_autoscroll_active(self) -> bool:
        """Indica si el auto-scroll por botón central está en marcha."""
        return self._autoscroll_active

    def stop_autoscroll(self) -> None:
        """Detiene el auto-scroll si está activo.

        La ventana lo usa para que una sola tecla Esc atienda, en orden:
        presentación, búsqueda y auto-scroll (varios atajos Esc simultáneos
        se anulan entre sí en Qt).
        """
        self._stop_autoscroll()

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
        zoom_mode: str = "width",
    ) -> None:
        """Carga un documento. ``page_sizes`` es la lista de (ancho, alto) en puntos."""
        self.close_document()
        self._page_sizes = list(page_sizes)
        self._zoom = 1.0
        self._zoom_mode = zoom_mode if zoom_mode in ("width", "page") else "width"
        self._rotation = 0
        self._dpr = max(1.0, self.devicePixelRatioF())
        self._last_range = None
        self._pending_fit_width = True
        self._pending_restore_page = start_page

        for i in range(len(page_sizes)):
            widget = _PageWidget(i + 1)
            self._pages.append(widget)
            self._layout.addWidget(widget)

        self._renderer = PageRenderer(path, self, password=password)
        self._renderer.pageReady.connect(self._on_page_ready)
        self._renderer.error.connect(self.renderError)
        self._renderer.searchPageHits.connect(self._on_search_hits)
        self._renderer.searchFinished.connect(self._on_search_finished)
        self._renderer.start()

        self._relayout()
        self._apply_pending_layout()
        self._schedule()
        self.pageChanged.emit(self.current_page(), len(self._pages))
        self.zoomChanged.emit(self._zoom * 100.0)
        self.rotationChanged.emit(self._rotation)

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
        self._rotation = 0
        self._reset_search()
        self._sync_busy()

    def has_document(self) -> bool:
        return bool(self._pages)

    # ---------------------------------------------------------------- zoom
    def zoom_mode(self) -> str:
        """Modo de zoom actual: ``"free"``, ``"width"`` o ``"page"``."""
        return self._zoom_mode

    def set_zoom(
        self, zoom: float, anchor_y: Optional[float] = None, mode: Optional[str] = None
    ) -> None:
        """Cambia el zoom conservando el punto visible.

        ``anchor_y`` es la coordenada Y (en el viewport) que debe permanecer
        fija; por defecto se usa el centro del viewport.
        """
        if not self._pages:
            return
        zoom = min(MAX_ZOOM, max(MIN_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            if mode is not None:
                self._zoom_mode = mode
            return

        bar = self._scroll.verticalScrollBar()
        viewport_h = self._scroll.viewport().height()
        anchor = float(anchor_y) if anchor_y is not None else viewport_h / 2.0
        content_point = bar.value() + anchor
        ratio = content_point / max(1, self._content_height)

        self._zoom = zoom
        self._zoom_mode = mode or "free"
        self._invalidate_renders()
        self._relayout()

        new_center = ratio * self._content_height
        self._programmatic_scroll = True
        try:
            bar.setValue(round(new_center - anchor))
        finally:
            self._programmatic_scroll = False
        self._schedule()
        self.zoomChanged.emit(self._zoom * 100.0)

    def zoom_in(self, anchor_y: Optional[float] = None) -> None:
        self.set_zoom(self._zoom * 1.25, anchor_y=anchor_y)

    def zoom_out(self, anchor_y: Optional[float] = None) -> None:
        self.set_zoom(self._zoom / 1.25, anchor_y=anchor_y)

    def fit_width(self) -> None:
        if not self._pages:
            return
        vw = self._scroll.viewport().width()
        if vw <= 0:
            self._pending_fit_width = True
            return
        self._pending_fit_width = False
        max_w = max(self._rotated_size(w, h)[0] for w, h in self._page_sizes)
        self.set_zoom((vw - 2 * PAGE_MARGIN) / max_w, mode="width")

    def fit_page(self) -> None:
        if not self._pages:
            return
        vw = self._scroll.viewport().width()
        vh = self._scroll.viewport().height()
        if vw <= 0 or vh <= 0:
            self._pending_fit_width = True
            return
        self._pending_fit_width = False
        w, h = self._rotated_size(*self._page_sizes[self._current_page])
        self.set_zoom(
            min((vw - 2 * PAGE_MARGIN) / w, (vh - 2 * PAGE_MARGIN) / h),
            mode="page",
        )

    # ------------------------------------------------------------ rotación
    def rotation(self) -> int:
        return self._rotation

    def rotate(self, delta: int = 90) -> None:
        """Gira la vista completa (rotación global del documento)."""
        if not self._pages:
            return
        self._rotation = (self._rotation + delta) % 360
        self._invalidate_renders()
        self._relayout()
        self.goto_page(self._current_page + 1)
        if self._zoom_mode != "free":
            self._apply_pending_layout()
        else:
            self._schedule()
        self._apply_highlights()
        self.rotationChanged.emit(self._rotation)

    # ------------------------------------------------------------ inversión
    def is_inverted(self) -> bool:
        return self._invert

    def set_invert(self, enabled: bool) -> None:
        """Invierte los colores de las páginas (lectura nocturna)."""
        enabled = bool(enabled)
        if enabled == self._invert:
            return
        self._invert = enabled
        self._invalidate_renders()
        self._schedule()

    # ----------------------------------------------------------- navegación
    def goto_page(self, page: int) -> None:
        """Salta a la página ``page`` (1-based)."""
        if not self._pages:
            return
        index = min(len(self._pages) - 1, max(0, page - 1))
        self._programmatic_scroll = True
        try:
            self._scroll.verticalScrollBar().setValue(self._tops[index])
        finally:
            self._programmatic_scroll = False
        self._set_current_page(index)

    def next_page(self) -> None:
        if not self._pages:
            return
        self.goto_page(min(self._current_page + 2, len(self._pages)))

    def prev_page(self) -> None:
        if not self._pages:
            return
        bar = self._scroll.verticalScrollBar()
        top = self._tops[self._current_page]
        if bar.value() > top + PAGE_MARGIN:
            # Si ya se avanzó dentro de la página, primero se sube a su inicio.
            self.goto_page(self._current_page + 1)
        else:
            self.goto_page(self._current_page)

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

    # ------------------------------------------------------------- búsqueda
    def is_searching(self) -> bool:
        return self._searching

    def search_text(self) -> str:
        return self._search_text

    def start_search(self, text: str) -> None:
        """Busca ``text`` en todo el documento, progresivamente."""
        text = text.strip()
        if self._renderer is None or not self._pages:
            return
        if not text:
            self.clear_search()
            return
        self._reset_search()
        self._search_text = text
        self._searching = True
        self._search_gen += 1
        self._renderer.start_search(text, self._search_gen)
        self._emit_search_status()

    def clear_search(self) -> None:
        """Quita los resaltados y cancela una búsqueda en curso."""
        if self._renderer is not None:
            self._renderer.cancel_search()
        self._reset_search()
        for widget in self._pages:
            widget.set_highlights([], -1)
        self._emit_search_status()

    def search_next(self) -> None:
        self._navigate_hit(+1)

    def search_prev(self) -> None:
        self._navigate_hit(-1)

    def _reset_search(self) -> None:
        self._search_gen += 1
        self._search_text = ""
        self._searching = False
        self._hits.clear()
        self._hit_count = 0
        self._hit_pos = None
        self._flat_hits_cache = None

    def _flat_hits(self) -> List[Tuple[int, Rect]]:
        if self._flat_hits_cache is None:
            self._flat_hits_cache = [
                (page, rect)
                for page in sorted(self._hits)
                for rect in self._hits[page]
            ]
        return self._flat_hits_cache

    def _navigate_hit(self, step: int) -> None:
        flat = self._flat_hits()
        if not flat:
            return
        if self._hit_pos is None:
            pos = 0 if step > 0 else len(flat) - 1
        else:
            try:
                pos = (flat.index(self._hit_pos) + step) % len(flat)
            except ValueError:
                pos = 0
        self._goto_hit(flat[pos])

    def _goto_hit(self, hit: Tuple[int, Rect]) -> None:
        page, rect = hit
        if not (0 <= page < len(self._pages)):
            return
        self._hit_pos = hit
        self._apply_highlights()
        w_pt, h_pt = self._page_sizes[page]
        rotated = _rotate_rect(rect, w_pt, h_pt, self._rotation)
        target = (
            self._tops[page]
            + round(rotated[1] * self._zoom)
            - self._scroll.viewport().height() // 3
        )
        self._programmatic_scroll = True
        try:
            self._scroll.verticalScrollBar().setValue(target)
        finally:
            self._programmatic_scroll = False
        self._set_current_page(page)
        self._emit_search_status()

    def _apply_highlights(self) -> None:
        """Recalcula los resaltados rotados de todas las páginas con coincidencias."""
        for page, rects in self._hits.items():
            if not (0 <= page < len(self._pages)):
                continue
            w_pt, h_pt = self._page_sizes[page]
            rotated = [_rotate_rect(r, w_pt, h_pt, self._rotation) for r in rects]
            current = -1
            if self._hit_pos is not None and self._hit_pos[0] == page:
                try:
                    current = rects.index(self._hit_pos[1])
                except ValueError:
                    current = -1
            self._pages[page].set_highlights(rotated, current)

    def _on_search_hits(self, gen: int, page: int, rects: List[Rect]) -> None:
        if gen != self._search_gen or not (0 <= page < len(self._pages)):
            return
        self._hits[page] = list(rects)
        self._flat_hits_cache = None
        self._hit_count += len(rects)
        w_pt, h_pt = self._page_sizes[page]
        rotated = [_rotate_rect(r, w_pt, h_pt, self._rotation) for r in rects]
        self._pages[page].set_highlights(rotated, -1)
        self._emit_search_status()

    def _on_search_finished(self, gen: int) -> None:
        if gen != self._search_gen:
            return
        self._searching = False
        if self._hit_count and self._hit_pos is None:
            self._navigate_hit(+1)
        else:
            self._emit_search_status()

    def _emit_search_status(self) -> None:
        if self._hit_pos is None:
            self.searchStatusChanged.emit(0, self._hit_count)
            return
        flat = self._flat_hits()
        try:
            pos = flat.index(self._hit_pos) + 1
        except ValueError:
            pos = 0
        self.searchStatusChanged.emit(pos, self._hit_count)

    # ----------------------------------------------------------- miniaturas
    def request_thumbnail(self, index: int) -> None:
        """Pide el render de una miniatura para el panel lateral."""
        if self._renderer is None or not (0 <= index < len(self._pages)):
            return
        key = RenderKey(index, THUMBNAIL_ZOOM, self._rotation, False, 1.0)
        self._renderer.request(key, priority=THUMBNAIL_PRIORITY)

    # ------------------------------------------------------------ internos
    def _rotated_size(self, w_pt: float, h_pt: float) -> Tuple[float, float]:
        if self._rotation % 180:
            return h_pt, w_pt
        return w_pt, h_pt

    def _key(self, index: int) -> RenderKey:
        return RenderKey(index, self._zoom, self._rotation, self._invert, self._dpr)

    def _invalidate_renders(self) -> None:
        if self._renderer is not None:
            self._renderer.cancel_all()
        self._rendering.clear()
        self._last_range = None
        self._sync_busy()

    def _sync_busy(self) -> None:
        busy = bool(self._rendering)
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit(busy)

    def _apply_pending_layout(self) -> None:
        """Aplica el ajuste inicial (y el modo continuo) cuando hay viewport."""
        if not self._pages:
            return
        if self._zoom_mode == "page":
            self.fit_page()
        else:
            self.fit_width()
        self._maybe_restore_start_page()

    def _maybe_restore_start_page(self) -> None:
        if (
            self._pending_restore_page is None
            or self._pending_fit_width
            or not self._pages
            or self._scroll.viewport().width() <= 0
        ):
            return
        page = self._pending_restore_page
        self._pending_restore_page = None
        QTimer.singleShot(0, lambda p=page: self.goto_page(p))

    def _relayout(self) -> None:
        tops: List[int] = []
        y = 0
        max_width = 0.0
        for widget, (w_pt, h_pt) in zip(self._pages, self._page_sizes):
            w_pt, h_pt = self._rotated_size(w_pt, h_pt)
            widget.set_page_size_pt(w_pt, h_pt)
            widget.set_page_size(
                QSize(round(w_pt * self._zoom), round(h_pt * self._zoom))
            )
            tops.append(y)
            y += widget.height() + PAGE_MARGIN
            max_width = max(max_width, w_pt)
        self._tops = tops
        self._content_height = max(0, y - PAGE_MARGIN)
        width = round(max_width * self._zoom) if self._page_sizes else 0
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
        if self._programmatic_scroll:
            return
        top_page = self._page_index_at(self._scroll.verticalScrollBar().value())
        self._set_current_page(top_page)

    def _set_current_page(self, index: int) -> None:
        if index != self._current_page:
            self._current_page = index
            self.pageChanged.emit(index + 1, len(self._pages))

    def _schedule(self) -> None:
        if self._renderer is None or not self._pages:
            return
        first, last = self._visible_range()
        if (first, last) == self._last_range:
            return
        self._last_range = (first, last)

        center = (first + last) // 2
        for i in range(first, last + 1):
            key = self._key(i)
            if key in self._cache:
                self._cache.move_to_end(key)
                continue
            if key in self._rendering:
                continue
            self._rendering.add(key)
            self._renderer.request(key, abs(i - center))
        self._sync_busy()
        self._evict(first, last)

    def _on_page_ready(self, key: RenderKey, image: QImage) -> None:
        if key.zoom == THUMBNAIL_ZOOM:
            self.thumbnailReady.emit(key.index, image)
            return
        self._rendering.discard(key)
        self._sync_busy()
        if key != self._key(key.index):
            return  # pedido obsoleto (cambió zoom, rotación o pantalla)
        if not (0 <= key.index < len(self._pages)):
            return
        pixmap = QPixmap.fromImage(image)
        nbytes = pixmap.width() * pixmap.height() * 4
        if nbytes <= CACHE_BUDGET_BYTES:
            self._cache[key] = pixmap
            self._cache_bytes += nbytes
        self._pages[key.index].set_pixmap(pixmap)
        self._trim_cache_by_budget()

    # -------------------------------------------------------------- caché
    def _remove_from_cache(self, key: RenderKey) -> None:
        pixmap = self._cache.pop(key, None)
        if pixmap is None:
            return
        self._cache_bytes -= pixmap.width() * pixmap.height() * 4
        index = key.index
        if key == self._key(index) and 0 <= index < len(self._pages):
            self._pages[index].clear_pixmap()

    def _evict(self, first: int, last: int) -> None:
        """Descarta lo que no sirve: otra rotación/inversión/pantalla o muy lejos.

        Las páginas cercanas de otros niveles de zoom se conservan para poder
        volver a ellos sin re-renderizar.
        """
        for key in list(self._cache.keys()):
            index = key.index
            stale = (
                key.rotation != self._rotation
                or key.invert != self._invert
                or abs(key.dpr - self._dpr) > 1e-6
            )
            if stale or index < first - KEEP_PAGES or index > last + KEEP_PAGES:
                self._remove_from_cache(key)
        self._trim_cache_by_budget()

    def _trim_cache_by_budget(self) -> None:
        if self._cache_bytes <= CACHE_BUDGET_BYTES:
            return
        visible = set()
        if self._last_range is not None:
            first, last = self._last_range
            visible = {self._key(i) for i in range(first, last + 1)}
        for key in list(self._cache.keys()):
            if self._cache_bytes <= CACHE_BUDGET_BYTES:
                break
            if key in visible:
                # Lo visible se conserva aunque el presupuesto se exceda.
                continue
            self._remove_from_cache(key)

    # ------------------------------------------------------------ eventos
    def _refresh_dpr(self) -> bool:
        """Detecta cambios de pantalla (HiDPI) e invalida los renders."""
        dpr = max(1.0, self.devicePixelRatioF())
        if abs(dpr - self._dpr) < 1e-6:
            return False
        self._dpr = dpr
        self._invalidate_renders()
        self._schedule()
        return True

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule()
        if self._refresh_dpr():
            return
        if self._pages and (self._pending_fit_width or self._zoom_mode != "free"):
            QTimer.singleShot(0, self._apply_pending_layout)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_dpr()
        if self._pages and self._pending_fit_width:
            QTimer.singleShot(0, self._apply_pending_layout)
