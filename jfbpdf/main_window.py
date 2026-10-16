"""Ventana principal de JFB Pdf."""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

from PySide6.QtCore import QByteArray, QEvent, QRect, Qt
from PySide6.QtGui import QAction, QIntValidator, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QToolBar,
)

from .about_dialog import AboutDialog
from .compat import fitz
from .errors import InvalidPasswordError, PasswordRequiredError
from .pdf_view import PdfView
from .search_bar import SearchBar
from .sidebar import Sidebar
from .state import AppState
from .theme import apply_theme
from .version import APP_NAME

logger = logging.getLogger(__name__)


def read_pdf_info(
    path: str, password: Optional[str] = None
) -> Tuple[int, List[Tuple[float, float]]]:
    """Abre el PDF, valida que sea legible y devuelve (n_páginas, tamaños_en_puntos).

    Lanza :class:`PasswordRequiredError` / :class:`InvalidPasswordError` si el
    documento está protegido, o bien otras excepciones si no puede abrirse.
    """
    doc = fitz().open(path)
    try:
        if doc.needs_pass:
            if password is None:
                raise PasswordRequiredError()
            if not doc.authenticate(password):
                raise InvalidPasswordError()
        sizes = [(page.rect.width, page.rect.height) for page in doc]
        return doc.page_count, sizes
    finally:
        doc.close()


def read_pdf_toc(path: str, password: Optional[str] = None) -> list:
    """Devuelve el índice (TOC) del documento, o una lista vacía si no tiene."""
    try:
        doc = fitz().open(path)
    except Exception:
        logger.warning("No se pudo leer el índice de %s", path, exc_info=True)
        return []
    try:
        if doc.needs_pass and (password is None or not doc.authenticate(password)):
            return []
        return list(doc.get_toc(simple=True) or [])
    except Exception:
        logger.warning("No se pudo leer el índice de %s", path, exc_info=True)
        return []
    finally:
        doc.close()


class MainWindow(QMainWindow):
    """Ventana con menú, barra de herramientas, panel lateral y barra de estado."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 1000)
        self.setAcceptDrops(True)

        self._state = AppState(self)
        self._current_path: Optional[str] = None
        self._password: Optional[str] = None
        self._zoom_percent = 100.0
        self._presentation = False
        self._search_visible = False
        self._sidebar_was_visible = False
        self._search_was_visible = False
        self._was_maximized = False

        self._view = PdfView(self)
        self.setCentralWidget(self._view)
        self._view.pageChanged.connect(self._on_page_changed)
        self._view.zoomChanged.connect(self._on_zoom_changed)
        self._view.rotationChanged.connect(self._on_rotation_changed)
        self._view.renderError.connect(self._on_render_error)
        self._view.busyChanged.connect(self._on_busy_changed)
        self._view.searchStatusChanged.connect(self._on_search_status)

        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self._build_search()
        self._build_sidebar()
        self._build_shortcuts()

        self._state.recentChanged.connect(self._rebuild_recent_menu)
        self._rebuild_recent_menu()
        if self._state.geometry:
            self.restoreGeometry(QByteArray(self._state.geometry))
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self._state.dark_theme)
        self.act_dark_theme.setChecked(self._state.dark_theme)
        self.act_invert.setChecked(self._state.invert)
        self._view.set_invert(self._state.invert)
        self._sidebar.setVisible(self._state.sidebar_visible)
        self.act_panel.setChecked(self._sidebar.isVisible())

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
            except (PasswordRequiredError, InvalidPasswordError) as exc:
                pwd, ok = QInputDialog.getText(
                    self,
                    "Documento protegido",
                    f"{exc}\n\nContraseña:",
                    QLineEdit.EchoMode.Password,
                )
                if not ok:
                    return
                password = pwd
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(
                    self,
                    "No se pudo abrir el documento",
                    f"No fue posible abrir el archivo:\n{path}\n\n{exc}",
                )
                return

        abs_path = os.path.abspath(path)
        start_page = self._state.last_page(abs_path)
        self._password = password
        self._current_path = None  # evita guardar la página sobre el documento anterior
        self._view.open_document(
            path,
            sizes,
            password=password,
            start_page=start_page,
            zoom_mode=self._state.zoom_mode,
        )
        self._current_path = abs_path
        self._state.add_recent(abs_path)
        self._sidebar.set_document(read_pdf_toc(path, password), count)
        self._sidebar.set_current_page(self._view.current_page())
        self._hide_search(clear_text=True)
        self.setWindowTitle(f"{APP_NAME} — {path}")

    def close_document(self) -> None:
        if self._current_path and self._view.has_document():
            self._state.set_last_page(self._current_path, self._view.current_page())
        self._view.close_document()
        self._current_path = None
        self._password = None
        self._sidebar.set_document([], 0)
        self._sidebar.set_current_page(0)
        self._hide_search(clear_text=True)
        self.setWindowTitle(APP_NAME)
        self._page_edit.clear()
        self._total_label.setText("/ 0")
        self._status_page.setText("")
        self._status_zoom.setText("")
        self._status_busy.setText("")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._current_path and self._view.has_document():
            self._state.set_last_page(self._current_path, self._view.current_page())
        self._state.save_geometry(self.saveGeometry())
        self._state.save_now()
        self.close_document()
        super().closeEvent(event)

    def _ask_goto_page(self) -> None:
        if not self._view.has_document():
            return
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

    # ---------------------------------------------------- búsqueda de texto
    def _show_search(self) -> None:
        if not self._view.has_document():
            return
        self._search_toolbar.show()
        self._search_visible = True
        self._search_bar.focus_edit()
        if self._search_bar.search_text():
            self._search_bar.retrigger()

    def _hide_search(self, clear_text: bool = False) -> None:
        if clear_text:
            self._search_bar.clear()
        self._search_toolbar.hide()
        self._search_visible = False
        self._search_bar.set_status("")
        self._view.clear_search()
        if self._view.has_document():
            self._view.setFocus()

    def _on_search_requested(self, text: str) -> None:
        if text:
            self._view.start_search(text)
        else:
            self._view.clear_search()
            self._search_bar.set_status("")

    def _on_search_status(self, position: int, total: int) -> None:
        if total == 0:
            if self._view.is_searching():
                self._search_bar.set_status("Buscando…")
            else:
                self._search_bar.set_status("")
            return
        self._search_bar.set_status(f"{position or 1} de {total}")

    def _find_next(self) -> None:
        if self._search_bar.search_text():
            self._view.search_next()

    def _find_prev(self) -> None:
        if self._search_bar.search_text():
            self._view.search_prev()

    # ------------------------------------------------------------- impresión
    def _print_document(self) -> None:
        if not self._current_path or not self._view.has_document():
            return
        try:
            from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        except ImportError:
            QMessageBox.information(
                self,
                "Imprimir",
                "Para imprimir hace falta el módulo QtPrintSupport de PySide6 "
                "(paquete python3-pyside6.qtprintsupport).",
            )
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._render_to_printer(printer)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error imprimiendo %s", self._current_path)
            QMessageBox.critical(
                self, "Error al imprimir", f"No se pudo imprimir el documento:\n{exc}"
            )

    def _render_to_printer(self, printer) -> None:
        from PySide6.QtGui import QImage, QPainter

        doc = fitz().open(self._current_path)
        try:
            if doc.needs_pass and self._password:
                doc.authenticate(self._password)
            rotation = self._view.rotation()
            progress = QProgressDialog(
                "Imprimiendo…", "Cancelar", 0, doc.page_count, self
            )
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            painter = QPainter(printer)
            try:
                for i in range(doc.page_count):
                    if i:
                        printer.newPage()
                    progress.setValue(i)
                    if progress.wasCanceled():
                        break
                    page = doc.load_page(i)
                    area = painter.viewport()
                    w_pt, h_pt = page.rect.width, page.rect.height
                    if rotation % 180:
                        w_pt, h_pt = h_pt, w_pt
                    scale = min(area.width() / w_pt, area.height() / h_pt)
                    matrix = fitz().Matrix(scale, scale)
                    if rotation:
                        matrix.prerotate(rotation)
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    image = QImage(
                        pix.samples,
                        pix.width,
                        pix.height,
                        pix.stride,
                        QImage.Format.Format_RGB888,
                    ).copy()
                    target = QRect(
                        0, 0, round(w_pt * scale), round(h_pt * scale)
                    )
                    target.moveCenter(area.center())
                    painter.drawImage(target, image)
            finally:
                painter.end()
                progress.setValue(doc.page_count)
        finally:
            doc.close()

    # -------------------------------------------------------- presentación
    def _toggle_presentation(self) -> None:
        if self._presentation:
            self._exit_presentation()
        else:
            self._enter_presentation()

    def _enter_presentation(self) -> None:
        if not self._view.has_document():
            return
        self._presentation = True
        self._was_maximized = self.isMaximized()
        self._sidebar_was_visible = self._sidebar.isVisible()
        self._search_was_visible = self._search_visible
        self._search_toolbar.hide()
        self._sidebar.hide()
        self.menuBar().hide()
        self._toolbar.hide()
        self.statusBar().hide()
        self.act_presentation.setChecked(True)
        self.showFullScreen()
        self._view.fit_page()

    def _exit_presentation(self) -> None:
        if not self._presentation:
            return
        self._presentation = False
        self._view.stop_autoscroll()
        self.menuBar().show()
        self._toolbar.show()
        self.statusBar().show()
        if self._search_was_visible:
            self._search_toolbar.show()
            self._search_visible = True
        if self._sidebar_was_visible:
            self._sidebar.show()
        if self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.act_presentation.setChecked(False)

    # -------------------------------------------------------------- eventos
    def eventFilter(self, watched, event):  # noqa: N802
        # Home/End se desactivan mientras se edita el campo de página para que
        # el cursor del texto pueda usarlos.
        if watched is self._page_edit:
            if event.type() == QEvent.Type.FocusIn:
                self.act_first.setEnabled(False)
                self.act_last.setEnabled(False)
            elif event.type() == QEvent.Type.FocusOut:
                self.act_first.setEnabled(True)
                self.act_last.setEnabled(True)
        return super().eventFilter(watched, event)

    def _on_page_changed(self, current: int, total: int) -> None:
        self._total_label.setText(f"/ {total}")
        if not self._page_edit.hasFocus():
            self._page_edit.setText(str(current))
        self._status_page.setText(f"Página {current} de {total}")
        if self._current_path:
            self._state.set_last_page(self._current_path, current)
        self._sidebar.set_current_page(current)

    def _on_zoom_changed(self, percent: float) -> None:
        self._zoom_percent = percent
        self._update_status_zoom()
        self._state.zoom_mode = self._view.zoom_mode()
        self._state.schedule_save()

    def _on_rotation_changed(self, _rotation: int) -> None:
        self._update_status_zoom()

    def _update_status_zoom(self) -> None:
        text = f"{self._zoom_percent:.0f} %"
        rotation = self._view.rotation()
        if rotation:
            text += f" · {rotation}°"
        self._status_zoom.setText(text)

    def _on_busy_changed(self, busy: bool) -> None:
        self._status_busy.setText("Renderizando…" if busy else "")

    def _on_render_error(self, message: str) -> None:
        QMessageBox.warning(self, "Error de renderizado", message)

    def _on_page_edit_return(self) -> None:
        if not self._view.has_document():
            return
        try:
            page = int(self._page_edit.text())
        except ValueError:
            return
        self._view.goto_page(page)
        self._view.setFocus()

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

        self.act_close_doc = QAction("Cerrar documento", self)
        self.act_close_doc.setShortcut(QKeySequence.StandardKey.Close)
        self.act_close_doc.triggered.connect(self.close_document)

        self.act_print = QAction("Imprimir…", self)
        self.act_print.setShortcut(QKeySequence.StandardKey.Print)
        self.act_print.triggered.connect(self._print_document)

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

        self.act_rotate_right = QAction("Girar a la derecha", self)
        self.act_rotate_right.setShortcut(QKeySequence("Ctrl+R"))
        self.act_rotate_right.triggered.connect(lambda: self._view.rotate(90))

        self.act_rotate_left = QAction("Girar a la izquierda", self)
        self.act_rotate_left.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.act_rotate_left.triggered.connect(lambda: self._view.rotate(-90))

        self.act_invert = QAction("Invertir colores", self)
        self.act_invert.setCheckable(True)
        self.act_invert.setShortcut(QKeySequence("Ctrl+I"))
        self.act_invert.toggled.connect(self._on_invert_toggled)

        self.act_dark_theme = QAction("Tema oscuro", self)
        self.act_dark_theme.setCheckable(True)
        self.act_dark_theme.toggled.connect(self._on_dark_theme_toggled)

        self.act_panel = QAction("Panel lateral", self)
        self.act_panel.setCheckable(True)
        self.act_panel.setShortcut(QKeySequence("F9"))
        self.act_panel.toggled.connect(self._on_panel_toggled)

        self.act_fullscreen = QAction("Pantalla completa", self)
        self.act_fullscreen.setShortcut(QKeySequence("F11"))
        self.act_fullscreen.triggered.connect(self._toggle_fullscreen)

        self.act_presentation = QAction("Presentación", self)
        self.act_presentation.setCheckable(True)
        self.act_presentation.setShortcut(QKeySequence("F5"))
        self.act_presentation.triggered.connect(self._toggle_presentation)

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

        self.act_search = QAction("Buscar…", self)
        self.act_search.setShortcut(QKeySequence.StandardKey.Find)
        self.act_search.triggered.connect(self._show_search)

        self.act_find_next = QAction("Coincidencia siguiente", self)
        self.act_find_next.setShortcut(QKeySequence("F3"))
        self.act_find_next.triggered.connect(self._find_next)

        self.act_find_prev = QAction("Coincidencia anterior", self)
        self.act_find_prev.setShortcut(QKeySequence("Shift+F3"))
        self.act_find_prev.triggered.connect(self._find_prev)

        self.act_about = QAction(f"Acerca de {APP_NAME}", self)
        self.act_about.triggered.connect(self._show_about)

        # Los atajos de una QAction se desactivan cuando todos sus widgets
        # asociados quedan ocultos (p. ej. la barra de menú y la de
        # herramientas en el modo presentación). Asociarlas también a la
        # ventana las mantiene activas mientras la ventana sea visible.
        for action in (
            self.act_open,
            self.act_close_doc,
            self.act_print,
            self.act_quit,
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_fit_width,
            self.act_fit_page,
            self.act_rotate_right,
            self.act_rotate_left,
            self.act_invert,
            self.act_dark_theme,
            self.act_panel,
            self.act_fullscreen,
            self.act_presentation,
            self.act_goto,
            self.act_first,
            self.act_last,
            self.act_search,
            self.act_find_next,
            self.act_find_prev,
        ):
            self.addAction(action)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Archivo")
        file_menu.addAction(self.act_open)
        self._recent_menu = file_menu.addMenu("Abrir reciente")
        file_menu.addAction(self.act_close_doc)
        file_menu.addSeparator()
        file_menu.addAction(self.act_print)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        view_menu = self.menuBar().addMenu("Ver")
        view_menu.addAction(self.act_zoom_in)
        view_menu.addAction(self.act_zoom_out)
        view_menu.addAction(self.act_fit_width)
        view_menu.addAction(self.act_fit_page)
        view_menu.addSeparator()
        view_menu.addAction(self.act_rotate_right)
        view_menu.addAction(self.act_rotate_left)
        view_menu.addAction(self.act_invert)
        view_menu.addAction(self.act_dark_theme)
        view_menu.addSeparator()
        view_menu.addAction(self.act_panel)
        view_menu.addAction(self.act_fullscreen)
        view_menu.addAction(self.act_presentation)

        go_menu = self.menuBar().addMenu("Ir a")
        go_menu.addAction(self.act_first)
        go_menu.addAction(self.act_prev)
        go_menu.addAction(self.act_next)
        go_menu.addAction(self.act_last)
        go_menu.addAction(self.act_goto)

        search_menu = self.menuBar().addMenu("Buscar")
        search_menu.addAction(self.act_search)
        search_menu.addAction(self.act_find_next)
        search_menu.addAction(self.act_find_prev)

        help_menu = self.menuBar().addMenu("Ayuda")
        help_menu.addAction(self.act_about)

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = self._state.recent
        self._recent_menu.setEnabled(bool(recents))
        for path in recents:
            action = self._recent_menu.addAction(os.path.basename(path))
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self.open_file(p))

    def _build_toolbar(self) -> None:
        self._toolbar = QToolBar("Barra principal", self)
        self._toolbar.setObjectName("main_toolbar")
        self._toolbar.setMovable(False)
        self.addToolBar(self._toolbar)

        self._toolbar.addAction(self.act_open)
        self._toolbar.addSeparator()
        self._toolbar.addAction(self.act_prev)
        self._toolbar.addAction(self.act_next)
        self._toolbar.addSeparator()

        self._page_edit = QLineEdit()
        self._page_edit.setFixedWidth(52)
        self._page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_edit.setValidator(QIntValidator(1, 2_000_000_000, self))
        self._page_edit.returnPressed.connect(self._on_page_edit_return)
        self._page_edit.installEventFilter(self)
        self._toolbar.addWidget(self._page_edit)

        self._total_label = QLineEdit()
        self._total_label.setReadOnly(True)
        self._total_label.setFrame(False)
        self._total_label.setFixedWidth(48)
        self._total_label.setText("/ 0")
        self._toolbar.addWidget(self._total_label)

        self._toolbar.addSeparator()
        self._toolbar.addAction(self.act_zoom_out)
        self._toolbar.addAction(self.act_fit_width)
        self._toolbar.addAction(self.act_zoom_in)
        self._toolbar.addSeparator()
        self._toolbar.addAction(self.act_search)
        self._toolbar.addAction(self.act_rotate_right)
        self._toolbar.addAction(self.act_panel)

    def _build_statusbar(self) -> None:
        self._status_page = QLabel("")
        self.statusBar().addWidget(self._status_page)
        self._status_busy = QLabel("")
        self.statusBar().addPermanentWidget(self._status_busy)
        self._status_zoom = QLabel("")
        self.statusBar().addPermanentWidget(self._status_zoom)

    def _build_search(self) -> None:
        self._search_toolbar = QToolBar("Búsqueda", self)
        self._search_toolbar.setObjectName("search_toolbar")
        self._search_toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, self._search_toolbar)
        self._search_bar = SearchBar(self._search_toolbar)
        self._search_toolbar.addWidget(self._search_bar)
        self._search_toolbar.hide()

        self._search_bar.searchRequested.connect(self._on_search_requested)
        self._search_bar.nextRequested.connect(self._find_next)
        self._search_bar.prevRequested.connect(self._find_prev)
        self._search_bar.closed.connect(self._hide_search)

    def _build_sidebar(self) -> None:
        self._sidebar = Sidebar(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._sidebar)
        self._sidebar.pageRequested.connect(self._view.goto_page)
        self._sidebar.bind_view(self._view)
        self._sidebar.visibilityChanged.connect(self._on_sidebar_visibility)

    def _build_shortcuts(self) -> None:
        # Espacio: avanzar una pantalla; Shift+Espacio: retroceder.
        QShortcut(QKeySequence("Space"), self, activated=self._view.page_down)
        QShortcut(
            QKeySequence("Shift+Space"), self, activated=self._view.page_up
        )
        QShortcut(QKeySequence("PgDown"), self, activated=self._view.page_down)
        QShortcut(QKeySequence("PgUp"), self, activated=self._view.page_up)
        # Un único Esc para toda la ventana: varios atajos Esc simultáneos
        # (presentación, búsqueda, auto-scroll) se anulan entre sí en Qt.
        QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self, activated=self._on_escape
        )

    def _on_escape(self) -> None:
        """Esc de la ventana, por prioridad: presentación, búsqueda, auto-scroll."""
        if self._presentation:
            self._exit_presentation()
        elif self._search_visible:
            self._hide_search()
        elif self._view.is_autoscroll_active():
            self._view.stop_autoscroll()

    # ------------------------------------------------------------ opciones
    def _on_invert_toggled(self, checked: bool) -> None:
        self._view.set_invert(checked)
        self._state.invert = checked
        self._state.schedule_save()

    def _on_dark_theme_toggled(self, checked: bool) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, checked)
        self._state.dark_theme = checked
        self._state.schedule_save()

    def _on_panel_toggled(self, checked: bool) -> None:
        self._sidebar.setVisible(checked)

    def _on_sidebar_visibility(self, visible: bool) -> None:
        self.act_panel.setChecked(visible)
        self._state.sidebar_visible = visible
        self._state.schedule_save()
