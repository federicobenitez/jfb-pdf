"""Motor de renderizado asíncrono de páginas PDF.

Responsabilidades:
  * Mantener un hilo de trabajo dueño del documento MuPDF (MuPDF no es seguro
    frente a accesos concurrentes, por lo que un único hilo evita bloqueos y
    mantiene la memoria acotada).
  * Procesar una cola de prioridad de páginas: las visibles se renderizan
    primero y los pedidos obsoletos se descartan.
  * Atender búsquedas de texto página a página, intercaladas con el
    renderizado, con posibilidad de cancelarlas.
  * Entregar cada página como :class:`QImage` a través de una señal Qt.

Este módulo no conoce nada de la interfaz: sólo produce imágenes y resultados
de búsqueda.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import math
import threading
from typing import List, NamedTuple, Optional, Set, Tuple

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from .compat import fitz

logger = logging.getLogger(__name__)

#: Máximo de píxeles por página renderizada (evita consumos desmedidos con
#: zoom extremo o páginas enormes). Si se supera, se reescala el render.
MAX_RENDER_PIXELS = 24 * 1024 * 1024

#: Tiempo máximo de espera al detener el hilo de render, en milisegundos.
SHUTDOWN_TIMEOUT_MS = 5000

#: Hilos que no terminaron dentro del timeout de cierre. Se mantienen vivos
#: hasta que finalicen para que Qt no destruya un QThread en ejecución.
_retired_workers: List["_RenderWorker"] = []


def _forget_worker(worker: "_RenderWorker") -> None:
    try:
        _retired_workers.remove(worker)
    except ValueError:
        pass


class RenderKey(NamedTuple):
    """Identifica un render concreto.

    Además de la página y el zoom, incluye la rotación e inversión de la vista
    y el ``devicePixelRatio`` de la pantalla, de modo que cualquier cambio en
    esos parámetros invalida las imágenes cacheadas.
    """

    index: int
    zoom: float
    rotation: int = 0
    invert: bool = False
    dpr: float = 1.0


def _page_to_image(page, key: RenderKey) -> Optional[QImage]:
    """Renderiza una página según ``key`` y devuelve un QImage RGB.

    La escala usada es ``zoom * dpr`` para que la imagen se vea nítida en
    pantallas HiDPI. Si el resultado supera :data:`MAX_RENDER_PIXELS` se
    reduce la escala (la vista la reescalará al tamaño del widget).
    """
    try:
        scale = key.zoom * max(1.0, key.dpr)
        w_pt, h_pt = page.rect.width, page.rect.height
        if key.rotation % 180:
            w_pt, h_pt = h_pt, w_pt
        target_pixels = (w_pt * scale) * (h_pt * scale)
        if target_pixels > MAX_RENDER_PIXELS:
            scale *= math.sqrt(MAX_RENDER_PIXELS / target_pixels)
            logger.debug(
                "Render de la página %d reducido a escala %.3f por límite de memoria",
                key.index + 1,
                scale,
            )

        matrix = fitz().Matrix(scale, scale)
        if key.rotation:
            matrix.prerotate(key.rotation)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        if key.invert:
            pix.invert_irect(pix.irect)
        image = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        )
        # QImage no es dueño del buffer de `samples`: copiamos para poder
        # liberar la página/pixmap sin corromper la imagen.
        image = image.copy()
        image.setDevicePixelRatio(max(1.0, key.dpr))
        return image
    except Exception:
        logger.exception("No se pudo renderizar la página %d", key.index + 1)
        return None


class _RenderWorker(QThread):
    """Hilo que posee el documento y renderiza páginas a demanda."""

    #: (clave de render, imagen)
    pageReady = Signal(object, QImage)
    #: (generación, índice de página, lista de rects (x0, y0, x1, y1) en puntos)
    searchPageHits = Signal(int, int, object)
    #: (generación)
    searchFinished = Signal(int)
    #: mensaje de error legible
    error = Signal(str)

    def __init__(self, path: str, password: Optional[str] = None) -> None:
        super().__init__()
        self._path = path
        self._password = password
        self._doc = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._running = True
        self._seq = itertools.count()
        self._queue: List[Tuple[int, int, RenderKey]] = []  # (prioridad, seq, clave)
        self._pending: Set[RenderKey] = set()
        # Búsqueda de texto
        self._search_text: Optional[str] = None
        self._search_gen = 0
        self._search_page = 0

    # --- API llamada desde el hilo de UI ---
    def request(self, key: RenderKey, priority: int = 0) -> None:
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
            heapq.heappush(self._queue, (priority, next(self._seq), key))
        self._wake.set()

    def cancel_all(self) -> None:
        """Descarta todos los pedidos pendientes (p. ej. al cambiar de vista)."""
        with self._lock:
            self._queue.clear()
            self._pending.clear()
        self._wake.set()

    def start_search(self, text: str, generation: int) -> None:
        """Inicia (o reinicia) una búsqueda de texto en el documento."""
        with self._lock:
            self._search_text = text
            self._search_gen = generation
            self._search_page = 0
        self._wake.set()

    def cancel_search(self) -> None:
        """Cancela la búsqueda en curso e invalida sus resultados."""
        with self._lock:
            self._search_text = None
            self._search_gen += 1
        self._wake.set()

    def stop(self) -> None:
        self._running = False
        self._wake.set()

    # --- bucle de trabajo (hilo propio) ---
    def run(self) -> None:  # noqa: D102
        self._doc = self._open_document()
        while self._running:
            did_work = False
            key = self._pop_request()
            if key is not None:
                did_work = True
                self._render(key)
            if self._doc is not None and self._search_active():
                did_work = self._search_step() or did_work
            if not did_work:
                self._wake.wait(0.2)
                self._wake.clear()
        self._close_document()

    # --- internos ---
    def _open_document(self):
        try:
            doc = fitz().open(self._path)
        except Exception as exc:
            logger.exception("No se pudo abrir el documento %s", self._path)
            self.error.emit(f"No se pudo abrir el documento:\n{self._path}\n\n{exc}")
            return None
        if doc.needs_pass:
            if not (self._password and doc.authenticate(self._password)):
                try:
                    doc.close()
                except Exception:
                    logger.debug("Error cerrando documento protegido", exc_info=True)
                self.error.emit("No se pudo desbloquear el documento protegido.")
                return None
        return doc

    def _close_document(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                logger.debug("Error cerrando el documento", exc_info=True)
            self._doc = None

    def _pop_request(self) -> Optional[RenderKey]:
        with self._lock:
            if not self._queue:
                return None
            _priority, _seq, key = heapq.heappop(self._queue)
            self._pending.discard(key)
            return key

    def _render(self, key: RenderKey) -> None:
        if self._doc is None:
            return
        try:
            page = self._doc.load_page(key.index)
        except Exception:
            logger.exception("No se pudo cargar la página %d", key.index + 1)
            return
        image = _page_to_image(page, key)
        if image is not None and not image.isNull() and self._running:
            self.pageReady.emit(key, image)

    def _search_active(self) -> bool:
        with self._lock:
            return self._search_text is not None

    def _search_step(self) -> bool:
        """Busca en una página; devuelve True si hizo trabajo."""
        with self._lock:
            text = self._search_text
            gen = self._search_gen
            page_no = self._search_page
            total = self._doc.page_count
        if text is None:
            return False
        if page_no >= total:
            self._finish_search(gen)
            return True

        rects: List[Tuple[float, float, float, float]] = []
        try:
            page = self._doc.load_page(page_no)
            rects = [
                (r.x0, r.y0, r.x1, r.y1) for r in page.search_for(text)
            ]
        except Exception:
            logger.exception("Error buscando en la página %d", page_no + 1)

        with self._lock:
            cancelled = self._search_gen != gen or self._search_text is None
            if not cancelled:
                self._search_page = page_no + 1
                last_page = self._search_page >= total
            else:
                last_page = False
        if cancelled:
            return True
        if rects:
            self.searchPageHits.emit(gen, page_no, rects)
        if last_page:
            self._finish_search(gen)
        return True

    def _finish_search(self, gen: int) -> None:
        with self._lock:
            if self._search_gen != gen:
                return
            self._search_text = None
        self.searchFinished.emit(gen)


class PageRenderer(QObject):
    """Fachada del renderer usada por la vista (vive en el hilo de UI)."""

    #: (clave de render, imagen)
    pageReady = Signal(object, QImage)
    searchPageHits = Signal(int, int, object)
    searchFinished = Signal(int)
    error = Signal(str)

    def __init__(self, path: str, parent=None, password: Optional[str] = None) -> None:
        super().__init__(parent)
        self._worker = _RenderWorker(path, password=password)
        self._worker.pageReady.connect(self.pageReady)
        self._worker.searchPageHits.connect(self.searchPageHits)
        self._worker.searchFinished.connect(self.searchFinished)
        self._worker.error.connect(self.error)
        self._started = False

    # --- ciclo de vida ---
    def start(self) -> None:
        if not self._started:
            self._worker.start()
            self._started = True

    def shutdown(self) -> None:
        """Detiene el hilo de forma segura.

        Desconecta las señales para que un render tardío no llegue a widgets
        ya destruidos. Si el hilo no termina dentro del timeout, se mantiene
        referenciado hasta que finalice por sí solo.
        """
        if not self._started:
            return
        self._started = False
        worker = self._worker
        for signal in (
            worker.pageReady,
            worker.searchPageHits,
            worker.searchFinished,
            worker.error,
        ):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        worker.stop()
        if not worker.wait(SHUTDOWN_TIMEOUT_MS):
            logger.warning(
                "El hilo de render no terminó a tiempo; se liberará al finalizar."
            )
            _retired_workers.append(worker)
            worker.finished.connect(lambda w=worker: _forget_worker(w))
        worker.finished.connect(worker.deleteLater)

    # --- renderizado ---
    def request(self, key: RenderKey, priority: int = 0) -> None:
        if self._started:
            self._worker.request(key, priority)

    def cancel_all(self) -> None:
        if self._started:
            self._worker.cancel_all()

    # --- búsqueda ---
    def start_search(self, text: str, generation: int) -> None:
        if self._started:
            self._worker.start_search(text, generation)

    def cancel_search(self) -> None:
        if self._started:
            self._worker.cancel_search()
