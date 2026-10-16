"""Motor de renderizado asíncrono de páginas PDF.

Responsabilidades:
  * Mantener un hilo de trabajo dueño del documento MuPDF (MuPDF no es seguro
    frente a accesos concurrentes, por lo que un único hilo evita bloqueos y
    mantiene la memoria acotada).
  * Procesar una cola de prioridad de páginas: las visibles se renderizan
    primero y los pedidos obsoletos se descartan.
  * Entregar cada página como :class:`QImage` a través de una señal Qt.

Este módulo no conoce nada de la interfaz: sólo produce imágenes.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

_fitz_module = None


def _fitz():
    """Importa PyMuPDF (``pymupdf`` o su alias histórico ``fitz``) de forma perezosa."""
    global _fitz_module
    if _fitz_module is None:
        try:  # PyMuPDF moderno
            import pymupdf as m
        except ImportError:  # alias histórico
            import fitz as m  # type: ignore
        _fitz_module = m
    return _fitz_module


def _page_to_image(page, zoom: float) -> Optional[QImage]:
    """Renderiza una página a ``zoom`` (escala sobre 72 dpi) y devuelve un QImage RGB."""
    try:
        matrix = _fitz().Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        image = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        )
        # QImage no es dueño del buffer de `samples`: copiamos para poder
        # liberar la página/pixmap sin corromper la imagen.
        return image.copy()
    except Exception:
        return None


class _RenderWorker(QThread):
    """Hilo que posee el documento y renderiza páginas a demanda."""

    pageReady = Signal(int, float, QImage)  # (page_index, zoom, image)

    def __init__(self, path: str, password: Optional[str] = None) -> None:
        super().__init__()
        self._path = path
        self._password = password
        self._doc: Optional["fitz.Document"] = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._running = True
        self._seq = itertools.count()
        self._queue: List[Tuple[int, int, int, float]] = []  # (priority, seq, index, zoom)
        self._pending = set()  # claves (index, zoom) ya encoladas

    # --- API llamada desde el hilo de UI ---
    def request(self, index: int, zoom: float, priority: int = 0) -> None:
        key = (index, zoom)
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
            heapq.heappush(self._queue, (priority, next(self._seq), index, zoom))
        self._wake.set()

    def cancel_all(self) -> None:
        """Descarta todos los pedidos pendientes (p. ej. al cambiar de documento o zoom)."""
        with self._lock:
            self._queue.clear()
            self._pending.clear()

    def stop(self) -> None:
        self._running = False
        self._wake.set()

    # --- bucle de trabajo (hilo propio) ---
    def run(self) -> None:  # noqa: D102
        try:
            self._doc = _fitz().open(self._path)
            if self._doc is not None and self._doc.needs_pass:
                if self._password and self._doc.authenticate(self._password):
                    pass
                else:
                    try:
                        self._doc.close()
                    except Exception:
                        pass
                    self._doc = None
        except Exception:
            self._doc = None

        while self._running:
            item = None
            with self._lock:
                if self._queue:
                    priority, _seq, index, zoom = heapq.heappop(self._queue)
                    self._pending.discard((index, zoom))
                    item = (index, zoom)
            if item is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue

            index, zoom = item
            if self._doc is None:
                continue
            try:
                page = self._doc.load_page(index)
            except Exception:
                continue
            image = _page_to_image(page, zoom)
            if image is not None and not image.isNull() and self._running:
                self.pageReady.emit(index, zoom, image)

        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass


class PageRenderer(QObject):
    """Fachada del renderer usada por la vista (vive en el hilo de UI)."""

    pageReady = Signal(int, float, QImage)  # (page_index, zoom, image)

    def __init__(self, path: str, parent=None, password: Optional[str] = None) -> None:
        super().__init__(parent)
        self._worker = _RenderWorker(path, password=password)
        self._worker.pageReady.connect(self.pageReady)
        self._started = False

    # --- ciclo de vida ---
    def start(self) -> None:
        if not self._started:
            self._worker.start()
            self._started = True

    def shutdown(self) -> None:
        if self._started:
            self._worker.stop()
            self._worker.wait(3000)
            self._started = False

    def request(self, index: int, zoom: float, priority: int = 0) -> None:
        if self._started:
            self._worker.request(index, zoom, priority)

    def cancel_all(self) -> None:
        if self._started:
            self._worker.cancel_all()
