"""Ventana principal de JFB Pdf."""

from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIntValidator, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

from .about_dialog import AboutDialog
from .pdf_view import PdfView
from .version import APP_NAME, VERSION


def _fitz():
    try:  # PyMuPDF moderno
        import pymupdf as m
    except ImportError:  # alias histórico
        import fitz as m  # type: ignore
    return m


def read_pdf_info(path: str, password: Optional[str] = None) -> Tuple[int, List[Tuple[float, float]]]:
    """Abre el PDF, valida que sea legible y devuelve (n_páginas, tamaños_en_puntos).

    Lanza ``ValueError``/``ImportError`` si el documento no puede abrirse.
    """
    doc = _fitz().open(path)
    try:
        if doc.needs_pass:
            if password is None or not doc.authenticate(password):
                raise ValueError("El documento está protegido con contraseña.")
        sizes = [(page.rect.width, page.rect.height) for page in doc]
        return doc.page_count, sizes
    finally:
        doc.close()


_STATE_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "jfbpdf", "last_pages.json"
)


def _load_last_pages() -> dict:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_last_pages(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


class MainWindow(QMainWindow):
    """Ventana con menú, barra de herramientas y barra de estado."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 1000)
        self.setAcceptDrops(True)

        self._view = PdfView(self)
        self.setCentralWidget(self._view)
        self._view.pageChanged.connect(self._on_page_changed)
        self._view.zoomChanged.connect(self._on_zoom_changed)

        self._current_path: Optional[str] = None
        self._last_pages: dict = _load_last_pages()

        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self._build_shortcuts()

    # ------------------------------------------------------------- acción
    def _on_open_triggered(self, _checked: bool = False) -> None:
        """Slot de QAction.triggered: descarta el bool `checked` y abre el diálogo."""
        self.open_file()

    def open_file(self, path: Optional[str] = None) -> None:
        if path is None:
            path, _filter = QFileDialog.getOpenFileName(
                self, "Abrir PDF", "", "Documentos PDF (*.pdf);;Todos los archivos (*)"
            )
            if not path:
                return

        password: Optional[str] = None
        while True:
            try:
                count, sizes = read_pdf_info(path, password)
                break
            except ValueError as exc:
                if "protegido" in str(exc):
                    from PySide6.QtWidgets import QInputDialog, QLineEdit

                    pwd, ok = QInputDialog.getText(
                        self,
                        "Documento protegido",
                        "Contraseña:",
                        QLineEdit.EchoMode.Password,
                    )
                    if not ok:
                        return
                    password = pwd
                    continue
                QMessageBox.critical(
                    self,
                    "No se pudo abrir el documento",
                    f"No fue posible abrir el archivo:\n{path}\n\n{exc}",
                )
                return
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(
                    self,
                    "No se pudo abrir el documento",
                    f"No fue posible abrir el archivo:\n{path}\n\n{exc}",
                )
                return

        abs_path = os.path.abspath(path)
        start_page = self._last_pages.get(abs_path)
        self._view.open_document(path, sizes, password=password, start_page=start_page)
        self._current_path = abs_path
        self.setWindowTitle(f"{APP_NAME} — {path}")

    def close_document(self) -> None:
        if self._current_path and self._view.has_document():
            self._last_pages[self._current_path] = self._view.current_page()
            _save_last_pages(self._last_pages)
        self._view.close_document()
        self._current_path = None
        self.setWindowTitle(APP_NAME)
        self._page_edit.clear()
        self._total_label.setText("/ 0")
        self._status_page.setText("")
        self._status_zoom.setText("")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._current_path and self._view.has_document():
            self._last_pages[self._current_path] = self._view.current_page()
            _save_last_pages(self._last_pages)
        self.close_document()
        super().closeEvent(event)

    def _ask_goto_page(self) -> None:
        if not self._view.has_document():
            return
        from PySide6.QtWidgets import QInputDialog

        page, ok = QInputDialog.getInt(
            self,
            "Ir a la página",
            "Número de página:",
            self._view.current_page(),
            1,
            self._view.page_count(),
        )
        if ok:
            self._view.goto_page(page)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    # ------------------------------------------------------------ eventos
    def _on_page_changed(self, current: int, total: int) -> None:
        self._total_label.setText(f"/ {total}")
        if not self._page_edit.hasFocus():
            self._page_edit.setText(str(current))
        self._status_page.setText(f"Página {current} de {total}")
        if self._current_path:
            self._last_pages[self._current_path] = current
            _save_last_pages(self._last_pages)

    def _on_zoom_changed(self, percent: float) -> None:
        self._status_zoom.setText(f"{percent:.0f} %")

    def _on_page_edit_return(self) -> None:
        if not self._view.has_document():
            return
        try:
            page = int(self._page_edit.text())
        except ValueError:
            return
        self._view.goto_page(page)

    # ---------------------------------------------------------- drag&drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                self.open_file(url.toLocalFile())
                return

    # ------------------------------------------------------- construcción
    def _build_actions(self) -> None:
        self.act_open = QAction("Abrir…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self._on_open_triggered)

        self.act_quit = QAction("Salir", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)

        self.act_zoom_in = QAction("Acercar", self)
        self.act_zoom_in.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self.act_zoom_in.triggered.connect(self._view.zoom_in)

        self.act_zoom_out = QAction("Alejar", self)
        self.act_zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self.act_zoom_out.triggered.connect(self._view.zoom_out)

        self.act_fit_width = QAction("Ajustar ancho", self)
        self.act_fit_width.triggered.connect(self._view.fit_width)

        self.act_fit_page = QAction("Ajustar página", self)
        self.act_fit_page.triggered.connect(self._view.fit_page)

        self.act_fullscreen = QAction("Pantalla completa", self)
        self.act_fullscreen.setShortcut(QKeySequence("F11"))
        self.act_fullscreen.triggered.connect(self._toggle_fullscreen)

        self.act_goto = QAction("Ir a la página…", self)
        self.act_goto.setShortcut(QKeySequence("Ctrl+G"))
        self.act_goto.triggered.connect(self._ask_goto_page)

        self.act_next = QAction("Página siguiente", self)
        self.act_next.triggered.connect(self._view.next_page)

        self.act_prev = QAction("Página anterior", self)
        self.act_prev.triggered.connect(self._view.prev_page)

        self.act_first = QAction("Primera página", self)
        self.act_first.setShortcut(QKeySequence("Home"))
        self.act_first.triggered.connect(self._view.first_page)

        self.act_last = QAction("Última página", self)
        self.act_last.setShortcut(QKeySequence("End"))
        self.act_last.triggered.connect(self._view.last_page)

        self.act_about = QAction(f"Acerca de {APP_NAME}", self)
        self.act_about.triggered.connect(self._show_about)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Archivo")
        file_menu.addAction(self.act_open)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        view_menu = self.menuBar().addMenu("Ver")
        view_menu.addAction(self.act_zoom_in)
        view_menu.addAction(self.act_zoom_out)
        view_menu.addAction(self.act_fit_width)
        view_menu.addAction(self.act_fit_page)
        view_menu.addSeparator()
        view_menu.addAction(self.act_fullscreen)

        go_menu = self.menuBar().addMenu("Ir a")
        go_menu.addAction(self.act_first)
        go_menu.addAction(self.act_prev)
        go_menu.addAction(self.act_next)
        go_menu.addAction(self.act_last)
        go_menu.addAction(self.act_goto)

        help_menu = self.menuBar().addMenu("Ayuda")
        help_menu.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Barra principal", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self.act_open)
        toolbar.addSeparator()
        toolbar.addAction(self.act_prev)
        toolbar.addAction(self.act_next)
        toolbar.addSeparator()

        self._page_edit = QLineEdit()
        self._page_edit.setFixedWidth(52)
        self._page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_edit.setValidator(QIntValidator(1, 2_000_000_000, self))
        self._page_edit.returnPressed.connect(self._on_page_edit_return)
        toolbar.addWidget(self._page_edit)

        self._total_label = QLineEdit()
        self._total_label.setReadOnly(True)
        self._total_label.setFrame(False)
        self._total_label.setFixedWidth(48)
        self._total_label.setText("/ 0")
        toolbar.addWidget(self._total_label)

        toolbar.addSeparator()
        toolbar.addAction(self.act_zoom_out)
        toolbar.addAction(self.act_fit_width)
        toolbar.addAction(self.act_zoom_in)

    def _build_statusbar(self) -> None:
        from PySide6.QtWidgets import QLabel

        self._status_page = QLabel("")
        self.statusBar().addWidget(self._status_page)
        self._status_zoom = QLabel("")
        self.statusBar().addPermanentWidget(self._status_zoom)

    def _build_shortcuts(self) -> None:
        # Espacio: avanzar una pantalla; Shift+Espacio: retroceder.
        QShortcut(QKeySequence("Space"), self, activated=self._view.page_down)
        QShortcut(
            QKeySequence("Shift+Space"), self, activated=self._view.page_up
        )
        QShortcut(QKeySequence("PgDown"), self, activated=self._view.page_down)
        QShortcut(QKeySequence("PgUp"), self, activated=self._view.page_up)
        QShortcut(QKeySequence("Home"), self, activated=self._view.first_page)
        QShortcut(QKeySequence("End"), self, activated=self._view.last_page)
