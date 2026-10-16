"""Persistencia del estado de la aplicación.

Todo el estado del usuario (última página por archivo, documentos recientes,
preferencias de zoom y tema, geometría de la ventana) vive en un único JSON
dentro del directorio de configuración XDG (``~/.config/jfbpdf/state.json``).

Las escrituras son atómicas (archivo temporal + ``os.replace``) y se agrupan
con un temporizador para no tocar el disco en cada cambio de página.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .version import APP_ID

logger = logging.getLogger(__name__)

STATE_FILE = "state.json"
LEGACY_STATE_FILE = "last_pages.json"

#: Retardo antes de escribir el estado a disco, en milisegundos.
SAVE_DELAY_MS = 1500

MAX_RECENT = 10
#: Máximo de entradas de "última página" conservadas.
MAX_LAST_PAGES = 200


def config_dir() -> str:
    """Directorio de configuración XDG de la aplicación."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, APP_ID)


class AppState(QObject):
    """Estado persistente de la aplicación."""

    recentChanged = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._path = os.path.join(config_dir(), STATE_FILE)
        data = self._load()
        self.last_pages: Dict[str, int] = self._clean_last_pages(
            data.get("last_pages")
        )
        self.recent: List[str] = [
            p for p in data.get("recent", []) if isinstance(p, str)
        ][:MAX_RECENT]
        mode = data.get("zoom_mode")
        self.zoom_mode: str = mode if mode in ("free", "width", "page") else "width"
        self.invert: bool = bool(data.get("invert", False))
        self.dark_theme: bool = bool(data.get("dark_theme", False))
        self.sidebar_visible: bool = bool(data.get("sidebar_visible", False))
        self.geometry: Optional[bytes] = self._decode_geometry(data.get("geometry"))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self.save)

    # ------------------------------------------------------------ consulta
    def last_page(self, path: str) -> Optional[int]:
        return self.last_pages.get(path)

    def set_last_page(self, path: str, page: int) -> None:
        if self.last_pages.get(path) == page:
            return
        self.last_pages[path] = page
        self.schedule_save()

    def add_recent(self, path: str) -> None:
        if self.recent and self.recent[0] == path:
            return
        self.recent = [path] + [p for p in self.recent if p != path]
        del self.recent[MAX_RECENT:]
        self.recentChanged.emit()
        self.schedule_save()

    def save_geometry(self, geometry) -> None:
        self.geometry = bytes(geometry.toBase64())
        self.schedule_save()

    # ------------------------------------------------------------ guardado
    def schedule_save(self) -> None:
        self._timer.start()

    def save(self) -> None:
        """Escribe el estado a disco (atómico)."""
        data: Dict[str, Any] = {
            "last_pages": self.last_pages,
            "recent": self.recent,
            "zoom_mode": self.zoom_mode,
            "invert": self.invert,
            "dark_theme": self.dark_theme,
            "sidebar_visible": self.sidebar_visible,
            "geometry": (
                base64.b64encode(self.geometry).decode("ascii")
                if self.geometry
                else ""
            ),
        }
        directory = os.path.dirname(self._path)
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=directory, prefix=".state-", suffix=".json"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(tmp_path, self._path)
            except OSError:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError:
            logger.warning("No se pudo guardar el estado en %s", self._path)

    def save_now(self) -> None:
        """Vuelca el estado sin esperar al temporizador (al cerrar)."""
        self._timer.stop()
        self.save()

    # ------------------------------------------------------------ internos
    def _load(self) -> Dict[str, Any]:
        data = self._read_json(self._path)
        if data is None:
            legacy = self._read_json(os.path.join(config_dir(), LEGACY_STATE_FILE))
            if legacy is not None:
                # Migración desde el formato antiguo (sólo última página).
                data = {"last_pages": legacy}
        return data or {}

    @staticmethod
    def _read_json(path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _clean_last_pages(raw: Any) -> Dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        clean: Dict[str, int] = {}
        for path, page in raw.items():
            if not isinstance(path, str) or not isinstance(page, int):
                continue
            if page < 1 or not os.path.exists(path):
                continue
            clean[path] = page
        # Poda: si hay demasiadas entradas, se conservan las más recientes
        # (dict preserva el orden de inserción).
        if len(clean) > MAX_LAST_PAGES:
            clean = dict(list(clean.items())[-MAX_LAST_PAGES:])
        return clean

    @staticmethod
    def _decode_geometry(value: Any) -> Optional[bytes]:
        if not isinstance(value, str) or not value:
            return None
        try:
            return base64.b64decode(value.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            return None
