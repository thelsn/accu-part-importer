from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .accu_client import AccuClient, AccuProduct
from .inventor_bridge import import_cad_to_ipt
from .parts import (
    format_part_number,
    parse_part_number,
    part_folder,
    part_number_exists,
    replace_existing_cad_files,
    suggest_next_part_number,
)
from .settings import load_config, save_config


ACCENT = "#3d8bfd"
NAVY = "#0d1b2a"
PANEL = "#152536"
FIELD = "#1c3348"
TEXT = "#e8eef5"
MUTED = "#93a4b8"


def _stylesheet() -> str:
    return f"""
    QMainWindow, QDialog, QWidget {{
        background: {NAVY};
        color: {TEXT};
        font-size: 13px;
    }}
    QLabel {{ color: {TEXT}; }}
    QLineEdit, QSpinBox, QPlainTextEdit, QListWidget, QTableWidget {{
        background: {FIELD};
        color: {TEXT};
        border: 1px solid #2b4660;
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: {ACCENT};
    }}
    QListWidget::item {{
        padding: 0px;
        border-bottom: 1px solid #24384c;
    }}
    QListWidget::item:selected {{
        background: #1f4d86;
        color: {TEXT};
    }}
    QPushButton {{
        background: {ACCENT};
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 14px;
        font-weight: 600;
    }}
    QPushButton:disabled {{
        background: #35506a;
        color: #9bb0c4;
    }}
    QPushButton#secondary {{
        background: #23405a;
        color: {TEXT};
        font-weight: 500;
    }}
    QHeaderView::section {{
        background: {PANEL};
        color: {MUTED};
        border: none;
        padding: 6px;
    }}
    QTableWidget {{
        gridline-color: #2b4660;
    }}
    QSplitter::handle {{ background: #1a2d40; }}
    QStatusBar {{ color: {MUTED}; }}
    """


class GuiBridge(QObject):
    search_ok = Signal(int, object)
    search_err = Signal(int, str)
    image_ok = Signal(str, object)
    image_err = Signal(str)
    thumb_ok = Signal(int, int, object)
    import_log = Signal(str)
    import_ok = Signal(object)
    import_err = Signal(str)


class ResultRow(QWidget):
    def __init__(self, product: AccuProduct, parent=None) -> None:
        super().__init__(parent)
        self.product_id = product.product_id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 12, 8)
        layout.setSpacing(12)

        self.thumb = QLabel()
        self.thumb.setFixedSize(56, 56)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(
            "background: #eef1f4; border-radius: 6px; color: #8a96a3; font-size: 10px;"
        )
        layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)

        crumb = QLabel(product.breadcrumb or product.category or "Accu")
        crumb.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")
        crumb.setWordWrap(False)

        title = QLabel(product.title)
        title.setWordWrap(True)
        title.setStyleSheet("color: #f4f7fb; font-weight: 700; font-size: 13px; background: transparent;")

        sku = QLabel(product.reference)
        sku.setStyleSheet(f"color: {MUTED}; font-size: 11px; background: transparent;")

        text.addWidget(crumb)
        text.addWidget(title)
        text.addWidget(sku)
        text.addStretch(1)
        layout.addLayout(text, 1)

    def sizeHint(self) -> QSize:
        return QSize(420, 80)

    def set_thumb(self, pix: QPixmap) -> None:
        if pix.isNull():
            return
        self.thumb.setPixmap(
            pix.scaled(
                self.thumb.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(640)
        self.cfg = dict(cfg)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.parts_root = QLineEdit(self.cfg.get("parts_root", ""))
        browse = QPushButton("Browse")
        browse.setObjectName("secondary")
        browse.clicked.connect(self._browse)
        parts_row = QHBoxLayout()
        parts_row.addWidget(self.parts_root, 1)
        parts_row.addWidget(browse)
        parts_wrap = QWidget()
        parts_wrap.setLayout(parts_row)
        form.addRow("Parts folder", parts_wrap)

        self.prefix = QLineEdit(self.cfg.get("part_prefix", "TA"))
        self.prefix.setMaximumWidth(80)
        form.addRow("Part prefix", self.prefix)

        self.digits = QSpinBox()
        self.digits.setRange(3, 8)
        self.digits.setValue(int(self.cfg.get("part_digits", 6)))
        form.addRow("Part digits", self.digits)

        self.base_url = QLineEdit(self.cfg.get("accu_base_url", "https://www.accu.co.uk"))
        form.addRow("Accu base URL", self.base_url)

        self.email = QLineEdit(self.cfg.get("accu_email", ""))
        form.addRow("Accu email", self.email)

        self.password = QLineEdit(self.cfg.get("accu_password", ""))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Accu password", self.password)

        self.inventor_visible = QCheckBox("Show Inventor while converting")
        self.inventor_visible.setChecked(bool(self.cfg.get("inventor_visible", True)))
        form.addRow("", self.inventor_visible)

        self.open_folder = QCheckBox("Open the part folder after import")
        self.open_folder.setChecked(bool(self.cfg.get("open_folder_after_import", True)))
        form.addRow("", self.open_folder)

        test_login = QPushButton("Test Accu login")
        test_login.setObjectName("secondary")
        test_login.clicked.connect(self._test_login)
        form.addRow("", test_login)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Parts folder", self.parts_root.text())
        if path:
            self.parts_root.setText(path)

    def _test_login(self) -> None:
        email = self.email.text().strip()
        password = self.password.text()
        if not email or not password:
            QMessageBox.warning(self, "Accu login", "Enter email and password first.")
            return
        try:
            client = AccuClient(self.base_url.text().strip() or "https://www.accu.co.uk")
            client.login(email, password)
            QMessageBox.information(self, "Accu login", "Signed in successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Accu login", str(exc))

    def values(self) -> dict:
        return {
            "parts_root": self.parts_root.text().strip(),
            "part_prefix": self.prefix.text().strip() or "TA",
            "part_digits": int(self.digits.value()),
            "accu_base_url": self.base_url.text().strip() or "https://www.accu.co.uk",
            "accu_email": self.email.text().strip(),
            "accu_password": self.password.text(),
            "keep_step": False,
            "inventor_visible": self.inventor_visible.isChecked(),
            "open_folder_after_import": self.open_folder.isChecked(),
            "close_document_after_save": True,
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Accu Part Importer")
        self.resize(1280, 820)
        self.setStyleSheet(_stylesheet())

        self.cfg = load_config()
        self.client = AccuClient(self.cfg.get("accu_base_url", "https://www.accu.co.uk"))
        self.products: list[AccuProduct] = []
        self.current: AccuProduct | None = None
        self._import_busy = False
        self._search_seq = 0
        self._image_seq = 0
        self._result_rows: dict[int, ResultRow] = {}
        self._thumb_cache: dict[str, QPixmap] = {}
        self._thumb_gate = threading.Semaphore(6)

        self.bridge = GuiBridge(self)
        self.bridge.search_ok.connect(self._on_search_ok)
        self.bridge.search_err.connect(self._on_search_err)
        self.bridge.image_ok.connect(self._on_image_ok)
        self.bridge.image_err.connect(self._on_image_err)
        self.bridge.thumb_ok.connect(self._on_thumb_ok)
        self.bridge.import_log.connect(self._log)
        self.bridge.import_ok.connect(self._import_done)
        self.bridge.import_err.connect(self._import_failed)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(280)
        self._debounce.timeout.connect(self._run_search)

        self._build_menu()
        self._build_ui()
        self.refresh_part_number()
        self._log("Ready. Search Accu's catalogue, pick a part, then import to Inventor.")

    def _build_menu(self) -> None:
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings)
        refresh_action = QAction("Refresh next part number", self)
        refresh_action.triggered.connect(self.refresh_part_number)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(settings_action)
        file_menu.addAction(refresh_action)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(
            "Search Accu: M8 hex nut A4, HPN-M8-A4, or paste https://www.accu.co.uk/..."
        )
        self.search_box.textChanged.connect(lambda _t: self._debounce.start())
        self.search_box.returnPressed.connect(self._run_search)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._run_search)
        settings_btn = QPushButton("Settings")
        settings_btn.setObjectName("secondary")
        settings_btn.clicked.connect(self.open_settings)
        search_row.addWidget(self.search_box, 1)
        search_row.addWidget(search_btn)
        search_row.addWidget(settings_btn)
        outer.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("Type a query to search Accu.")
        self.status_label.setStyleSheet(f"color: {MUTED};")
        self.results = QListWidget()
        self.results.setIconSize(QSize(56, 56))
        self.results.setSpacing(0)
        self.results.setUniformItemSizes(False)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.status_label)
        left_layout.addWidget(self.results, 1)
        splitter.addWidget(left)

        right = QWidget()
        right.setMinimumWidth(420)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        self.image_frame = QFrame()
        self.image_frame.setFixedHeight(220)
        self.image_frame.setStyleSheet(
            f"QFrame {{ background: {PANEL}; border-radius: 8px; }}"
        )
        frame_layout = QVBoxLayout(self.image_frame)
        frame_layout.setContentsMargins(8, 8, 8, 8)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setText("No part selected")
        frame_layout.addWidget(self.image_label)
        right_layout.addWidget(self.image_frame)

        self.title_label = QLabel("Select a part")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        self.title_label.setFont(font)
        self.title_label.setWordWrap(True)
        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet(f"color: {MUTED};")
        self.meta_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.meta_label.setOpenExternalLinks(True)
        self.meta_label.setWordWrap(True)
        right_layout.addWidget(self.title_label)
        right_layout.addWidget(self.meta_label)

        self.features = QTableWidget(0, 2)
        self.features.setHorizontalHeaderLabels(["Property", "Value"])
        self.features.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.features.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.features.verticalHeader().setVisible(False)
        self.features.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.features.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        right_layout.addWidget(self.features, 1)

        part_box = QFrame()
        part_box.setStyleSheet(f"QFrame {{ background: {PANEL}; border-radius: 8px; }}")
        part_layout = QGridLayout(part_box)
        part_layout.addWidget(QLabel("Part number"), 0, 0)
        self.part_number = QLineEdit()
        self.part_number.setPlaceholderText("TA000475")
        self.part_number.textChanged.connect(self._validate_part_number)
        refresh_btn = QPushButton("Suggest latest")
        refresh_btn.setObjectName("secondary")
        refresh_btn.clicked.connect(self.refresh_part_number)
        part_layout.addWidget(self.part_number, 1, 0)
        part_layout.addWidget(refresh_btn, 1, 1)
        self.part_hint = QLabel("")
        self.part_hint.setStyleSheet(f"color: {MUTED};")
        part_layout.addWidget(self.part_hint, 2, 0, 1, 2)

        self.import_btn = QPushButton("Download STEP and create IPT")
        self.import_btn.setEnabled(False)
        self.import_btn.setMinimumHeight(40)
        self.import_btn.clicked.connect(self._start_import)
        part_layout.addWidget(self.import_btn, 3, 0, 1, 2)
        right_layout.addWidget(part_box)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(140)
        outer.addWidget(self.log_view)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg.update(dlg.values())
            save_config(self.cfg)
            self.client = AccuClient(self.cfg.get("accu_base_url", "https://www.accu.co.uk"))
            self.refresh_part_number()
            self._log(f"Parts folder: {self.cfg['parts_root']}")

    def refresh_part_number(self) -> None:
        number = suggest_next_part_number(
            self.cfg["parts_root"], self.cfg["part_prefix"], self.cfg["part_digits"]
        )
        self.part_number.setText(number)
        self._validate_part_number()

    def _validate_part_number(self) -> None:
        parsed = parse_part_number(self.part_number.text())
        if parsed is None:
            self.part_hint.setText("Use prefix + digits, for example TA000475.")
            self._update_import_enabled()
            return
        prefix, number = parsed
        expected_prefix = self.cfg.get("part_prefix", "TA").upper()
        formatted = format_part_number(prefix, number, self.cfg.get("part_digits", 6))
        folder = part_folder(self.cfg["parts_root"], formatted)
        if prefix != expected_prefix:
            self.part_hint.setText(f"Prefix is usually {expected_prefix}. Folder: {folder}")
        elif folder.exists():
            self.part_hint.setText(f"{formatted} already exists. Download will ask to replace it.")
        else:
            self.part_hint.setText(f"Will create {folder}")
        if self.part_number.text().strip().upper() != formatted:
            cursor = self.part_number.cursorPosition()
            self.part_number.blockSignals(True)
            self.part_number.setText(formatted)
            self.part_number.setCursorPosition(cursor)
            self.part_number.blockSignals(False)
        self._update_import_enabled()

    def _update_import_enabled(self) -> None:
        ok = (
            self.current is not None
            and parse_part_number(self.part_number.text()) is not None
            and not self._import_busy
        )
        self.import_btn.setEnabled(ok)

    def _run_search(self) -> None:
        query = self.search_box.text().strip()
        if not query:
            self.results.clear()
            self.status_label.setText("Type a query to search Accu.")
            return
        self._search_seq += 1
        seq = self._search_seq
        self.status_label.setText(f'Searching Accu for "{query}"...')
        client = self.client

        def work() -> None:
            try:
                results = client.search(query)
                self.bridge.search_ok.emit(seq, results)
            except Exception as exc:
                self.bridge.search_err.emit(seq, f"{type(exc).__name__}: {exc}")

        threading.Thread(target=work, name=f"accu-search-{seq}", daemon=True).start()

    def _on_search_ok(self, seq: int, products: object) -> None:
        if seq != self._search_seq:
            return
        self._populate_results(list(products or []))

    def _on_search_err(self, seq: int, message: str) -> None:
        if seq != self._search_seq:
            return
        self.status_label.setText("Search failed.")
        self._log(f"Search error: {message}")

    def _populate_results(self, products: list[AccuProduct]) -> None:
        self.products = products
        self._result_rows = {}
        self.results.clear()
        if not products:
            self.status_label.setText("No Accu parts matched that search.")
            return
        self.status_label.setText(f"{len(products)} matches, ranked with fuzzy scoring.")
        for product in products:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, product.product_id)
            row = ResultRow(product)
            item.setSizeHint(row.sizeHint())
            self.results.addItem(item)
            self.results.setItemWidget(item, row)
            self._result_rows[product.product_id] = row
            cached = self._thumb_cache.get(product.image)
            if cached is not None:
                row.set_thumb(cached)
        self.results.setCurrentRow(0)
        self._start_thumbnails(products)

    def _start_thumbnails(self, products: list[AccuProduct]) -> None:
        seq = self._search_seq
        client = self.client
        gate = self._thumb_gate
        needed = [p for p in products if p.image and p.image not in self._thumb_cache]
        if not needed:
            return

        def work(product: AccuProduct) -> None:
            with gate:
                if seq != self._search_seq:
                    return
                try:
                    data = client.fetch_preview_image(product.image)
                except Exception:
                    return
                self.bridge.thumb_ok.emit(seq, product.product_id, data)

        for product in needed:
            threading.Thread(
                target=work,
                args=(product,),
                name=f"accu-thumb-{product.product_id}",
                daemon=True,
            ).start()

    def _on_thumb_ok(self, seq: int, product_id: int, payload: object) -> None:
        if seq != self._search_seq:
            return
        pix = QPixmap()
        try:
            data = bytes(payload) if payload is not None else b""
        except Exception:
            data = b""
        if not data or not pix.loadFromData(data):
            return
        product = next((p for p in self.products if p.product_id == product_id), None)
        if product and product.image:
            self._thumb_cache[product.image] = pix
        row = self._result_rows.get(product_id)
        if row is not None:
            row.set_thumb(pix)

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self.products):
            self.current = None
            self._update_import_enabled()
            return
        product = self.products[row]
        self.current = product
        self.title_label.setText(product.title)
        url = product.page_url(self.cfg["accu_base_url"])
        self.meta_label.setText(
            f'<b>{product.reference}</b>  ·  {product.manufacturer or "Accu"}  ·  '
            f'id {product.product_id}  ·  <a href="{url}">Open on Accu</a>'
        )
        self.features.setRowCount(0)
        rows = [("APC", product.reference), ("Category", product.category)]
        rows.extend(product.features.items())
        self.features.setRowCount(len(rows))
        for i, (key, value) in enumerate(rows):
            self.features.setItem(i, 0, QTableWidgetItem(str(key)))
            self.features.setItem(i, 1, QTableWidgetItem(str(value)))
        self._load_image(product.image)
        self._update_import_enabled()

    def _load_image(self, url: str) -> None:
        self.image_label.setText("Loading image...")
        if not url:
            self.image_label.setText("No image")
            return
        self._image_seq += 1
        seq = self._image_seq
        client = self.client

        def work() -> None:
            try:
                payload = client.fetch_preview_image(url)
                self.bridge.image_ok.emit(f"{seq}|{url}", payload)
            except Exception:
                self.bridge.image_err.emit(str(seq))

        threading.Thread(target=work, name=f"accu-image-{seq}", daemon=True).start()

    def _on_image_ok(self, token: str, payload: object) -> None:
        seq_text, _sep, _url = token.partition("|")
        try:
            seq = int(seq_text)
        except ValueError:
            return
        if seq != self._image_seq:
            return
        pix = QPixmap()
        data = b""
        if isinstance(payload, (bytes, bytearray, memoryview)):
            data = bytes(payload)
        else:
            try:
                data = bytes(payload)
            except Exception:
                data = b""
        if not data or not pix.loadFromData(data):
            self.image_label.setText("No image")
            return
        target = self.image_label.size()
        if target.width() < 8 or target.height() < 8:
            target = self.image_frame.size()
        self.image_label.setPixmap(
            pix.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_image_err(self, token: str) -> None:
        try:
            seq = int(str(token).split("|", 1)[0])
        except ValueError:
            return
        if seq != self._image_seq:
            return
        self.image_label.setText("No image")

    def _start_import(self) -> None:
        if self.current is None:
            return
        parsed = parse_part_number(self.part_number.text())
        if parsed is None:
            QMessageBox.warning(self, "Part number", "Enter a valid part number first.")
            return
        prefix, number = parsed
        part_number = format_part_number(prefix, number, self.cfg.get("part_digits", 6))
        folder = part_folder(self.cfg["parts_root"], part_number)
        replace_existing = False
        if part_number_exists(self.cfg["parts_root"], part_number):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Part already exists")
            box.setText(f"{part_number} already exists.")
            box.setInformativeText(
                f"Replace the existing part with {self.current.reference}?\n\n{folder}"
            )
            replace_btn = box.addButton("Replace", QMessageBox.ButtonRole.YesRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.NoRole)
            box.setDefaultButton(cancel_btn)
            box.exec()
            if box.clickedButton() is not replace_btn:
                return
            replace_existing = True
        if not self.cfg.get("accu_email") or not self.cfg.get("accu_password"):
            QMessageBox.warning(
                self,
                "Accu login",
                "Accu requires an account to download STEP files. Add the email and password in Settings first.",
            )
            self.open_settings()
            if not self.cfg.get("accu_email") or not self.cfg.get("accu_password"):
                return

        self.import_btn.setEnabled(False)
        self._import_busy = True
        self._log(f"Importing {self.current.reference} as {part_number}...")
        client = self.client
        cfg = dict(self.cfg)
        product = self.current
        bridge = self.bridge

        def work() -> None:
            try:
                folder = part_folder(cfg["parts_root"], part_number)
                folder.mkdir(parents=True, exist_ok=True)
                if replace_existing:
                    removed = replace_existing_cad_files(folder, part_number)
                    if removed:
                        bridge.import_log.emit(
                            "Replaced: " + ", ".join(path.name for path in removed)
                        )
                bridge.import_log.emit(f"Using folder {folder}")
                bridge.import_log.emit("Looking up Accu CAD models...")
                model = client.pick_step_model(product.product_id)
                bridge.import_log.emit(f"Using {model.format} model {model.name} (id {model.model_id})")
                suffix = Path(model.name).suffix.lower()
                if suffix not in {".stp", ".step", ".igs", ".iges"}:
                    suffix = ".stp"
                step_path = folder / f"{part_number}{suffix}"
                bridge.import_log.emit("Downloading CAD file from Accu...")
                client.download_model(
                    model,
                    step_path,
                    email=cfg.get("accu_email", ""),
                    password=cfg.get("accu_password", ""),
                )
                bridge.import_log.emit(f"Saved {step_path.name} ({step_path.stat().st_size} bytes)")
                bridge.import_ok.emit(
                    {
                        "folder": str(folder),
                        "step_path": str(step_path),
                        "part_number": part_number,
                        "title": product.title,
                        "reference": product.reference,
                        "product_id": product.product_id,
                        "url": product.page_url(cfg["accu_base_url"]),
                    }
                )
            except Exception as exc:
                bridge.import_err.emit(str(exc))

        threading.Thread(target=work, name="accu-import", daemon=True).start()

    def _import_done(self, payload: object) -> None:
        self._import_busy = False
        if not isinstance(payload, dict):
            self._update_import_enabled()
            return
        step_path = Path(payload["step_path"])
        part_number = payload["part_number"]
        folder = Path(payload["folder"])
        ipt_path = folder / f"{part_number}.ipt"
        extra = {
            "stock_number": payload.get("reference", ""),
            "vendor": "Accu",
            "project": "Tiny Air",
            "comments": payload.get("url", ""),
            "AccuAPC": payload.get("reference", ""),
            "AccuURL": payload.get("url", ""),
            "AccuProductId": str(payload.get("product_id", "")),
            "AccuTitle": payload.get("title", ""),
        }
        try:
            self._log("Opening Inventor and converting to IPT...")
            import_cad_to_ipt(
                step_path,
                ipt_path,
                part_number,
                payload.get("title", part_number),
                extra=extra,
                visible=bool(self.cfg.get("inventor_visible", True)),
                close_after_save=bool(self.cfg.get("close_document_after_save", True)),
            )
            self._log(f"Saved {ipt_path}")
            if step_path.exists():
                step_path.unlink()
                self._log("Removed STEP file after IPT conversion")
        except Exception as exc:
            self._log(f"Inventor conversion failed: {exc}")
            QMessageBox.critical(
                self,
                "Inventor conversion failed",
                f"{exc}\n\nThe STEP file is still in:\n{step_path}",
            )
            self._update_import_enabled()
            return

        self._log(f"Done: {folder}")
        self.refresh_part_number()
        self._update_import_enabled()
        if self.cfg.get("open_folder_after_import", True):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _import_failed(self, message: str) -> None:
        self._log(f"Import failed: {message}")
        QMessageBox.critical(self, "Import failed", message)
        self._import_busy = False
        self._update_import_enabled()
