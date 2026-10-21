"""Native Qt workstation for the independent OpenStat runtime.

Only the worker touches the engine while a command is running. The GUI refreshes
its dataset snapshots after the thread exits; edits are allowed only when idle.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QObject, QRect, QSize, Qt, QThread, QTimer,
    QUrl, Signal, Slot,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence,
    QPainter, QPixmap, QTextCursor, QTextFormat,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QStyle, QTableView, QTabWidget, QTextEdit,
    QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .highlight import StataHighlighter


ROOT = Path(__file__).resolve().parent.parent
MONO = "Cascadia Mono" if sys.platform == "win32" else "DejaVu Sans Mono"


def asset_path(name):
    """Find both wheel-installed and source-checkout brand assets."""
    for directory in (Path(__file__).resolve().parent / "assets", ROOT / "assets"):
        path = directory / name
        if path.exists():
            return path
    return ROOT / "assets" / name


def prepare_fonts():
    """Qt's Windows offscreen plugin needs explicit local font discovery."""
    global MONO
    if sys.platform == "win32" and not QFontDatabase.families():
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuil.ttf", "consola.ttf", "consolab.ttf"):
            path = directory / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    families = set(QFontDatabase.families())
    MONO = next((name for name in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono") if name in families), "monospace")


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("codeEditor")
        self.setFont(QFont(MONO, 10))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.numbers = LineNumberArea(self)
        self.highlighter = StataHighlighter(self.document())
        self.blockCountChanged.connect(self.update_margin)
        self.updateRequest.connect(self.update_numbers)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.update_margin()
        self.highlight_current_line()

    def line_number_width(self):
        return 16 + self.fontMetrics().horizontalAdvance("9") * max(3, len(str(self.blockCount())))

    def update_margin(self, *_):
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def update_numbers(self, rect, dy):
        if dy:
            self.numbers.scroll(0, dy)
        else:
            self.numbers.update(0, rect.y(), self.numbers.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_margin()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.numbers.setGeometry(QRect(rect.left(), rect.top(), self.line_number_width(), rect.height()))

    def paint_line_numbers(self, event):
        painter = QPainter(self.numbers)
        painter.fillRect(event.rect(), QColor("#f0f3f6"))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(QColor("#7b8b9d"))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self.numbers.width() - 8,
                                 self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    def highlight_current_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#eef7f8"))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])


class CommandLine(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = []
        self.history_position = 0
        self.draft = ""

    def remember(self, command):
        if not self.history or self.history[-1] != command:
            self.history.append(command)
        self.history_position = len(self.history)
        self.draft = ""

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.history:
            if self.history_position == len(self.history):
                self.draft = self.text()
            step = -1 if event.key() == Qt.Key.Key_Up else 1
            self.history_position = max(0, min(len(self.history), self.history_position + step))
            self.setText(self.history[self.history_position] if self.history_position < len(self.history) else self.draft)
            return
        super().keyPressEvent(event)


class RunWorker(QObject):
    finished = Signal(object)

    def __init__(self, engine, script, file_path=None):
        super().__init__()
        self.engine = engine
        self.script = script
        self.file_path = file_path

    @Slot()
    def run(self):
        try:
            result = self.engine.run_file(self.file_path) if self.file_path else self.engine.run(self.script)
        except Exception as exc:
            result = SimpleNamespace(output=f"OpenStat runtime error: {exc}\n", rc=1, line=None)
        self.finished.emit(result)


class DataFrameModel(QAbstractTableModel):
    """Dataset snapshot, with validated writes delegated to the idle engine."""

    def __init__(self, frame, labels=None, on_edit=None, parent=None):
        super().__init__(parent)
        self.frame = frame.copy(deep=True)
        self.labels = labels or {}
        self.on_edit = on_edit
        self.editable = False
        self.last_error = ""

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.frame.iat[index.row(), index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if pd.isna(value):
                return "."
            if isinstance(value, float):
                return format(value, ".10g")
            return str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) if pd.api.types.is_numeric_dtype(self.frame.dtypes.iloc[index.column()]) else int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and pd.isna(value):
            return QColor("#98a3af")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return str(self.frame.columns[section]) if orientation == Qt.Orientation.Horizontal else str(section + 1)
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            name = str(self.frame.columns[section])
            return self.labels.get(name, name)
        return None

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsEditable if self.editable and self.on_edit else flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not self.editable or not self.on_edit:
            return False
        try:
            text = str(value)
            dtype = self.frame.dtypes.iloc[index.column()]
            if pd.api.types.is_numeric_dtype(dtype):
                if pd.api.types.is_integer_dtype(dtype) and text.strip().lstrip("+-").isdigit():
                    parsed = int(text)
                else:
                    parsed = float("nan") if text.strip() in ("", ".") else float(text)
                    if pd.api.types.is_integer_dtype(dtype) and math.isfinite(parsed) and parsed.is_integer():
                        parsed = int(parsed)
            else:
                parsed = text
            self.on_edit(index.row(), index.column(), parsed)
            self.frame.iat[index.row(), index.column()] = parsed
            self.last_error = ""
            self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
            return True
        except (ValueError, TypeError, RuntimeError) as exc:
            self.last_error = str(exc)
            return False


class DataBrowser(QMainWindow):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Data Browser — OpenStat")
        self.resize(1000, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(130)
        self.setCentralWidget(self.table)
        toolbar = self.addToolBar("Dataset")
        toolbar.setMovable(False)
        toolbar.addAction(owner.save_data_action)
        toolbar.addSeparator()
        self.edit_toggle = QCheckBox("Enable editing")
        self.edit_toggle.setToolTip("Edits modify the dataset in memory. Use Save dataset to write a file.")
        self.edit_toggle.toggled.connect(self.toggle_editing)
        toolbar.addWidget(self.edit_toggle)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("  Go to observation: "))
        self.observation = QLineEdit()
        self.observation.setMaximumWidth(100)
        self.observation.setPlaceholderText("1")
        self.observation.returnPressed.connect(self.goto_observation)
        toolbar.addWidget(self.observation)
        copy_action = QAction("Copy selection", self)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(self.copy_selection)
        self.addAction(copy_action)
        self.refresh()

    def refresh(self):
        previous = self.edit_toggle.isChecked()
        previous_model = getattr(self, "model", None)
        self.model = DataFrameModel(self.owner.engine.data, self.owner.engine.variable_labels,
                                    self.edit_value, self)
        self.table.setModel(self.model)
        if previous_model is not None:
            previous_model.deleteLater()
        self.model.editable = previous and not self.owner.running
        self.statusBar().showMessage(f"{len(self.model.frame):,} observations  ·  {len(self.model.frame.columns):,} variables  ·  Changes stay in memory until saved")

    def toggle_editing(self, checked):
        self.model.editable = checked and not self.owner.running
        self.setWindowTitle(("Data Editor" if self.model.editable else "Data Browser") + " — OpenStat")

    def set_running(self, running):
        self.table.setEnabled(not running)
        self.edit_toggle.setEnabled(not running)
        self.model.editable = self.edit_toggle.isChecked() and not running

    def edit_value(self, row, col, value):
        if self.owner.running:
            raise RuntimeError("A command is running; editing is locked.")
        self.owner.engine.data.iat[row, col] = value
        self.owner.statusBar().showMessage("Dataset edited in memory; save dataset to keep changes.", 6000)

    def goto_observation(self):
        try:
            row = int(self.observation.text()) - 1
            if 0 <= row < self.model.rowCount() and self.model.columnCount():
                index = self.model.index(row, 0)
                self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.table.setCurrentIndex(index)
        except ValueError:
            self.statusBar().showMessage("Enter an observation number.", 3000)

    def copy_selection(self):
        indexes = self.table.selectedIndexes()
        if not indexes:
            return
        selected = {(index.row(), index.column()) for index in indexes}
        rows = range(min(index.row() for index in indexes), max(index.row() for index in indexes) + 1)
        columns = range(min(index.column() for index in indexes), max(index.column() for index in indexes) + 1)
        text = "\n".join("\t".join(str(self.model.data(self.model.index(row, col))) if (row, col) in selected else "" for col in columns) for row in rows)
        QApplication.clipboard().setText(text)


class OpenStatWindow(QMainWindow):
    def __init__(self, engine=None):
        prepare_fonts()
        super().__init__()
        if engine is None:
            from .engine import Engine
            engine = Engine(cwd=Path.cwd())
        # Stata reports c(mode) as "interactive" in the GUI and "batch" when
        # a do-file is run from the command line.
        engine.mode = "interactive"
        self.engine = engine
        self.running = False
        self.do_path = None
        self._thread = None
        self._worker = None
        self._pending_result = None
        self._active_script = ""
        self._active_history_item = None
        self._active_editor_offset = None
        self._close_when_idle = False
        self._browser = None
        self._graph_paths = []
        self._run_actions = []
        self._dataset_actions = []
        self.setWindowTitle("OpenStat — Statistical Workstation")
        self.resize(1420, 900)
        self.setMinimumSize(1000, 650)
        logo = asset_path("icon.svg")
        if logo.exists():
            self.setWindowIcon(QIcon(str(logo)))
        self._make_actions()
        self._make_menus()
        self._make_toolbar()
        self._make_workspace()
        self._make_statusbar()
        self._apply_style()
        self._refresh_dataset()
        self.results.setPlainText(
            "OpenStat\n"
            "Independent, open-source statistical workstation\n\n"
            "Ready. Type a command below, open a dataset, or run a do-file.\n"
            "Experimental runtime · Stata compatibility is incomplete.\n"
            "Help > Compatibility shows the supported surface and limits.\n\n"
        )
        self.command.setFocus()

    def _action(self, text, slot, shortcut=None, standard=None, run=False, dataset=False):
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if standard:
            action.setIcon(self.style().standardIcon(standard))
        if run:
            self._run_actions.append(action)
        if dataset:
            self._dataset_actions.append(action)
        return action

    def _make_actions(self):
        sp = QStyle.StandardPixmap
        self.open_data_action = self._action("Open dataset…", self.open_dataset, "Ctrl+Shift+O", sp.SP_DirOpenIcon, dataset=True)
        self.save_data_action = self._action("Save dataset as…", self.save_dataset, standard=sp.SP_DialogSaveButton, dataset=True)
        self.import_action = self._action("Import CSV…", self.import_csv, dataset=True)
        self.export_action = self._action("Export CSV…", self.export_csv, dataset=True)
        self.new_action = self._action("New do-file", self.new_do_file, "Ctrl+N", sp.SP_FileIcon)
        self.open_action = self._action("Open do-file…", self.open_do_file, "Ctrl+O", sp.SP_DialogOpenButton)
        self.save_action = self._action("Save do-file", self.save_do_file, "Ctrl+S", sp.SP_DialogSaveButton)
        self.save_as_action = self._action("Save do-file as…", lambda: self.save_do_file(True), "Ctrl+Shift+S")
        self.run_action = self._action("Run do-file", self.run_editor, "Ctrl+D", sp.SP_MediaPlay, run=True)
        self.run_selection_action = self._action("Run selection", self.run_selection, "Ctrl+Shift+D", run=True)
        self.stop_action = self._action("Stop", self.stop, "Ctrl+Break", sp.SP_MediaStop)
        self.stop_action.setEnabled(False)
        self.browse_action = self._action("Data Browser", self.show_data_browser, "Ctrl+8", sp.SP_FileDialogDetailedView, dataset=True)
        self.clear_action = self._action("Clear dataset…", self.clear_dataset, dataset=True)
        self.cwd_action = self._action("Change working directory…", self.change_directory, dataset=True)
        self.exit_action = self._action("Exit", self.close, "Alt+F4")

    def _make_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        for action in (self.open_data_action, self.save_data_action, self.import_action, self.export_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        for action in (self.new_action, self.open_action, self.save_action, self.save_as_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(self.cwd_action)
        file_menu.addAction(self._action("Save results…", self.save_results))
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)
        edit = self.menuBar().addMenu("&Edit")
        for title, shortcut, method in (("Undo", "Ctrl+Z", "undo"), ("Redo", "Ctrl+Y", "redo"), ("Cut", "Ctrl+X", "cut"), ("Copy", "Ctrl+C", "copy"), ("Paste", "Ctrl+V", "paste"), ("Select all", "Ctrl+A", "selectAll")):
            # Standard widget shortcuts remain native; menu actions target focus.
            action = self._action(title, lambda checked=False, method=method: self._focused_edit(method))
            action.setShortcutVisibleInContextMenu(True)
            edit.addAction(action)
        edit.addSeparator()
        edit.addAction(self._action("Find in do-file…", self.find_in_editor, "Ctrl+F"))
        edit.addAction(self._action("Clear Results", lambda: self.results.clear()))
        data = self.menuBar().addMenu("&Data")
        data.addAction(self.browse_action)
        data.addAction(self._action("Describe data", lambda: self.execute("describe"), run=True))
        data.addAction(self._action("Summarize data", lambda: self.execute("summarize"), run=True))
        data.addSeparator()
        data.addAction(self._action("Sort variables…", lambda: self.command_dialog("Sort data", "sort", ["Variables"]), dataset=True))
        data.addAction(self._action("Generate variable…", lambda: self.command_dialog("Generate variable", "generate", ["Name = expression"]), dataset=True))
        data.addAction(self._action("Rename variable…", lambda: self.command_dialog("Rename variable", "rename", ["Old name", "New name"]), dataset=True))
        data.addSeparator()
        data.addAction(self.clear_action)
        graphics = self.menuBar().addMenu("&Graphics")
        graphics.addAction(self._action("Scatterplot…", lambda: self.command_dialog("Scatterplot", "scatter", ["Y variable", "X variable"]), dataset=True))
        graphics.addAction(self._action("Histogram…", lambda: self.command_dialog("Histogram", "histogram", ["Variable"]), dataset=True))
        graphics.addAction(self._action("Line plot…", lambda: self.command_dialog("Line plot", "line", ["Y variable", "X variable"]), dataset=True))
        graphics.addSeparator()
        graphics.addAction(self._action("Show graphs", lambda: self.tabs.setCurrentWidget(self.graph_page)))
        stats = self.menuBar().addMenu("&Statistics")
        stats.addAction(self._action("Summary statistics…", lambda: self.command_dialog("Summary statistics", "summarize", ["Variables (blank = all)"]), dataset=True))
        stats.addAction(self._action("Frequency table…", lambda: self.command_dialog("Frequency table", "tabulate", ["Variable"]), dataset=True))
        stats.addAction(self._action("Linear regression…", lambda: self.command_dialog("Linear regression", "regress", ["Dependent variable", "Explanatory variables"]), dataset=True))
        stats.addAction(self._action("Correlation…", lambda: self.command_dialog("Correlation", "correlate", ["Variables"]), dataset=True))
        stats.addSeparator()
        stats.addAction(self._action("Command compatibility…", self.show_compatibility))
        user = self.menuBar().addMenu("&User")
        user.addAction(self._action("Install SSC package…", self.install_package, dataset=True))
        user.addAction(self._action("Package compatibility…", self.show_compatibility))
        window = self.menuBar().addMenu("&Window")
        window.addAction(self._action("Results", lambda: self.tabs.setCurrentWidget(self.results), "Ctrl+1"))
        window.addAction(self._action("Do-file editor", lambda: self.tabs.setCurrentWidget(self.editor_page), "Ctrl+9"))
        window.addAction(self.browse_action)
        window.addAction(self._action("Command", lambda: self.command.setFocus(), "Ctrl+L"))
        window.addSeparator()
        window.addAction(self.run_action)
        window.addAction(self.run_selection_action)
        window.addAction(self.stop_action)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("Command help…", self.command_help, "F1"))
        help_menu.addAction(self._action("Compatibility", self.show_compatibility))
        help_menu.addAction(self._action("Keyboard shortcuts", self.show_shortcuts))
        help_menu.addSeparator()
        help_menu.addAction(self._action("About OpenStat", self.show_about))

    def _make_toolbar(self):
        toolbar = QToolBar("Main toolbar", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(19, 19))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        for action in (self.open_data_action, self.browse_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        for action in (self.new_action, self.open_action, self.save_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.run_action)
        toolbar.addAction(self.stop_action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.runtime_label = QLabel("INDEPENDENT RUNTIME  ")
        self.runtime_label.setObjectName("runtimeLabel")
        toolbar.addWidget(self.runtime_label)

    def _section_header(self, title, subtitle=None):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        if subtitle:
            small = QLabel(subtitle)
            small.setObjectName("sectionSubtitle")
            layout.addWidget(small)
        return box

    def _make_workspace(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        brand = QFrame()
        brand.setObjectName("brandStrip")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(16, 12, 16, 12)
        logo = asset_path("icon.svg")
        if logo.exists():
            icon = QLabel()
            icon.setPixmap(QIcon(str(logo)).pixmap(34, 34))
            brand_layout.addWidget(icon)
        title = QLabel("OpenStat")
        title.setObjectName("brandTitle")
        brand_layout.addWidget(title)
        subtitle = QLabel("STATISTICAL WORKSTATION")
        subtitle.setObjectName("brandSubtitle")
        brand_layout.addWidget(subtitle)
        brand_layout.addStretch()
        badge = QLabel("Experimental · MIT licensed")
        badge.setObjectName("brandBadge")
        brand_layout.addWidget(badge)
        layout.addWidget(brand)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        history_panel = QWidget()
        history_panel.setMinimumWidth(200)
        history_panel.setMaximumWidth(300)
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(0)
        history_layout.addWidget(self._section_header("History", "Click to recall · double-click to run"))
        self.history_filter = QLineEdit()
        self.history_filter.setPlaceholderText("Filter history…")
        self.history_filter.setClearButtonEnabled(True)
        self.history_filter.textChanged.connect(self.filter_history)
        history_layout.addWidget(self.history_filter)
        self.history = QListWidget()
        self.history.setObjectName("historyList")
        self.history.setFont(QFont(MONO, 9))
        self.history.itemClicked.connect(self.recall_history)
        self.history.itemDoubleClicked.connect(lambda item: self.execute(item.data(Qt.ItemDataRole.UserRole)))
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self.history_menu)
        history_layout.addWidget(self.history)
        self.history_count = QLabel("  0 commands")
        self.history_count.setObjectName("panelFootnote")
        history_layout.addWidget(self.history_count)
        self.splitter.addWidget(history_panel)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setObjectName("results")
        self.results.setFont(QFont(MONO, 10))
        self.results.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.tabs.addTab(self.results, "Results")
        self.editor_page = QWidget()
        editor_layout = QVBoxLayout(self.editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_bar = QHBoxLayout()
        editor_bar.setContentsMargins(12, 7, 12, 7)
        self.editor_path = QLabel("Untitled.do")
        self.editor_path.setObjectName("sectionSubtitle")
        editor_bar.addWidget(self.editor_path)
        editor_bar.addStretch()
        self.editor_position = QLabel("Ln 1, Col 1")
        self.editor_position.setObjectName("sectionSubtitle")
        editor_bar.addWidget(self.editor_position)
        editor_layout.addLayout(editor_bar)
        self.editor = CodeEditor()
        self.editor.document().modificationChanged.connect(self.update_editor_title)
        self.editor.cursorPositionChanged.connect(self.update_editor_position)
        editor_layout.addWidget(self.editor)
        self.tabs.addTab(self.editor_page, "Do-file editor")
        self.graph_page = QWidget()
        graph_layout = QVBoxLayout(self.graph_page)
        graph_tools = QHBoxLayout()
        self.graph_selector = QComboBox()
        self.graph_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.graph_selector.currentIndexChanged.connect(self.show_graph)
        graph_tools.addWidget(self.graph_selector)
        self.graph_export = QPushButton("Save image…")
        self.graph_export.clicked.connect(self.save_graph)
        self.graph_export.setEnabled(False)
        graph_tools.addWidget(self.graph_export)
        graph_layout.addLayout(graph_tools)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.graph_label = QLabel("No graphs yet.\nCreate a graph from Graphics or a command.")
        self.graph_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.graph_label.setObjectName("graphPreview")
        self.graph_label.setMinimumSize(300, 240)
        scroll.setWidget(self.graph_label)
        graph_layout.addWidget(scroll)
        self.tabs.addTab(self.graph_page, "Graphs")
        self.splitter.addWidget(self.tabs)
        right = QWidget()
        right.setMinimumWidth(240)
        right.setMaximumWidth(360)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._section_header("Variables", "Dataset in memory"))
        self.variable_filter = QLineEdit()
        self.variable_filter.setPlaceholderText("Find variable…")
        self.variable_filter.setClearButtonEnabled(True)
        self.variable_filter.textChanged.connect(self.filter_variables)
        right_layout.addWidget(self.variable_filter)
        self.variables = QTreeWidget()
        self.variables.setHeaderLabels(["Name", "Type"])
        self.variables.setRootIsDecorated(False)
        self.variables.setAlternatingRowColors(True)
        self.variables.setColumnWidth(0, 150)
        self.variables.itemDoubleClicked.connect(self.insert_variable)
        self.variables.itemSelectionChanged.connect(self.show_variable_properties)
        right_layout.addWidget(self.variables, 3)
        right_layout.addWidget(self._section_header("Properties"))
        self.properties = QTreeWidget()
        self.properties.setHeaderHidden(True)
        self.properties.setRootIsDecorated(False)
        self.properties.setColumnCount(2)
        self.properties.setColumnWidth(0, 85)
        right_layout.addWidget(self.properties, 2)
        self.splitter.addWidget(right)
        self.splitter.setSizes([220, 900, 280])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        layout.addWidget(self.splitter, 1)
        command_panel = QFrame()
        command_panel.setObjectName("commandPanel")
        cmd_layout = QHBoxLayout(command_panel)
        cmd_layout.setContentsMargins(14, 10, 14, 10)
        command_label = QLabel("Command")
        command_label.setObjectName("sectionTitle")
        cmd_layout.addWidget(command_label)
        prompt = QLabel(".")
        prompt.setObjectName("commandPrompt")
        cmd_layout.addWidget(prompt)
        self.command = CommandLine()
        self.command.setObjectName("commandInput")
        self.command.setFont(QFont(MONO, 10))
        self.command.setPlaceholderText("Enter command · ↑ ↓ recall history")
        self.command.returnPressed.connect(self.run_command)
        cmd_layout.addWidget(self.command, 1)
        self.command_run = QPushButton("Run")
        self.command_run.setObjectName("commandRun")
        self.command_run.clicked.connect(self.run_command)
        cmd_layout.addWidget(self.command_run)
        layout.addWidget(command_panel)
        self.setCentralWidget(central)

    def _make_statusbar(self):
        self.statusBar().setSizeGripEnabled(True)
        self.state_label = QLabel("●  Ready")
        self.state_label.setObjectName("readyState")
        self.statusBar().addWidget(self.state_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(100)
        self.progress.setMaximumHeight(12)
        self.progress.hide()
        self.statusBar().addWidget(self.progress)
        self.dataset_status = QLabel()
        self.statusBar().addPermanentWidget(self.dataset_status)
        self.cwd_status = QLabel()
        self.cwd_status.setMaximumWidth(500)
        self.statusBar().addPermanentWidget(self.cwd_status)

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { color: #263a4e; font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 12px; }
            QMainWindow { background: #edf1f5; }
            QMenuBar { background: #ffffff; padding: 3px 8px; border-bottom: 1px solid #d6dfe8; }
            QMenuBar::item { padding: 4px 9px; }
            QMenuBar::item:selected, QMenu::item:selected { background: #dceef0; color: #14364b; }
            QMenu { background: #ffffff; border: 1px solid #ccd6df; padding: 4px; }
            QMenu::item { padding: 7px 26px 7px 14px; }
            QMenu::separator { height: 1px; background: #e0e6eb; margin: 4px; }
            QToolBar { background: #f8fafc; spacing: 4px; padding: 7px 10px; border-bottom: 1px solid #d6dfe8; }
            QToolButton { border: 1px solid transparent; border-radius: 3px; padding: 6px 7px; }
            QToolButton:hover { background: #e4eef3; border-color: #c3d9e1; }
            QToolButton:disabled { color: #9da9b4; }
            QToolBar::separator { background: #d4dee7; width: 1px; margin: 4px 6px; }
            #runtimeLabel { color: #738394; font-size: 9px; letter-spacing: 1px; }
            #brandStrip { background: #132c43; border-bottom: 3px solid #12a49c; }
            #brandTitle { color: #ffffff; font-size: 23px; font-weight: 650; }
            #brandSubtitle { color: #a4bccd; font-size: 9px; letter-spacing: 2px; padding-left: 11px; }
            #brandBadge { color: #bfdddE; background: #21475b; padding: 6px 9px; border-radius: 3px; font-size: 10px; }
            QSplitter::handle { background: #d4dee7; width: 1px; }
            QSplitter > QWidget { background: #f7f9fb; }
            #sectionTitle { color: #233d52; font-weight: 650; font-size: 12px; }
            #sectionSubtitle { color: #748494; font-size: 10px; }
            QLineEdit { background: #ffffff; border: 1px solid #d1dce5; border-radius: 3px; padding: 7px 8px; selection-background-color: #b6dce2; }
            QLineEdit:focus { border-color: #269b9c; }
            QLineEdit:disabled { background: #eef2f5; color: #8794a1; }
            QTabWidget::pane { border: 0; background: white; }
            QTabBar::tab { background: #eaf0f5; padding: 11px 20px; color: #687c8e; border-right: 1px solid #d6dfe8; }
            QTabBar::tab:selected { background: #ffffff; color: #0b7f82; border-top: 3px solid #169c9c; padding-top: 8px; font-weight: 600; }
            QPlainTextEdit { background: #ffffff; border: 0; color: #263a4e; selection-background-color: #c3e5e8; padding: 10px; }
            #results, #codeEditor { font-family: 'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono'; font-size: 13px; }
            #commandInput, #historyList { font-family: 'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono'; }
            QListWidget, QTreeWidget, QTableView { background: #ffffff; border: 0; outline: none; alternate-background-color: #f5f8fa; selection-background-color: #d8edef; selection-color: #174c5e; }
            QListWidget::item { padding: 8px 9px; border-bottom: 1px solid #edf1f5; }
            QTreeWidget::item { padding: 5px 3px; }
            QHeaderView::section { background: #edf2f6; border: 0; border-bottom: 1px solid #d2dde6; border-right: 1px solid #e1e7ed; color: #63778b; padding: 7px 5px; font-size: 10px; font-weight: 600; }
            #panelFootnote { color: #8493a2; background: #f0f4f7; padding: 8px; font-size: 10px; }
            #commandPanel { background: #edf3f6; border-top: 1px solid #cddbe4; }
            #commandPrompt { color: #168f93; font-size: 25px; font-family: monospace; font-weight: bold; padding: 0 2px 6px 7px; }
            #commandInput { padding: 9px; }
            QPushButton { background: #f6f9fb; color: #28495e; border: 1px solid #cad8e1; border-radius: 3px; padding: 7px 14px; }
            QPushButton:hover { background: #e6f0f3; }
            QPushButton:disabled { color: #8f9eaa; background: #eef2f4; }
            #commandRun { background: #087f86; color: white; border: 1px solid #087f86; padding: 9px 22px; font-weight: 600; }
            #commandRun:hover { background: #066d74; }
            #commandRun:disabled { background: #b1c4cb; border-color: #b1c4cb; }
            #graphPreview { background: #ffffff; color: #8392a0; font-size: 14px; }
            QStatusBar { background: #f7f9fb; border-top: 1px solid #d6dfe8; font-size: 10px; }
            QStatusBar::item { border: 0; }
            QStatusBar QLabel { padding: 4px 8px; font-size: 10px; color: #6c7e8f; }
            #readyState { color: #13878a; }
            QProgressBar { background: #e1eaef; border: 0; border-radius: 3px; }
            QProgressBar::chunk { background: #189c9b; }
            QScrollBar:vertical { width: 12px; background: #f1f4f7; }
            QScrollBar::handle:vertical { background: #bdcbd6; min-height: 24px; border-radius: 4px; margin: 2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

    def _focused_edit(self, method):
        widget = QApplication.focusWidget()
        if hasattr(widget, method):
            getattr(widget, method)()

    def _append_output(self, text):
        cursor = self.results.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.results.setTextCursor(cursor)
        self.results.ensureCursorVisible()

    def run_command(self):
        script = self.command.text().strip()
        if script:
            self.command.clear()
            self.execute(script)

    def execute(self, script, file_path=None, editor_offset=None):
        if self.running:
            self.statusBar().showMessage("A command is already running. Stop it before starting another.", 4000)
            return
        if not script.strip() and not file_path:
            return
        self._active_script = script
        self._active_editor_offset = editor_offset
        self.command.remember(script)
        short = script.splitlines()[0]
        if len(script.splitlines()) > 1:
            short += f"  … ({len(script.splitlines())} lines)"
        self._active_history_item = QListWidgetItem(short)
        self._active_history_item.setData(Qt.ItemDataRole.UserRole, script)
        self._active_history_item.setToolTip(script[:6000])
        self.history.addItem(self._active_history_item)
        self.history.scrollToBottom()
        self.history_count.setText(f"  {self.history.count()} commands")
        self.filter_history(self.history_filter.text())
        self._append_output(". " + (f'do "{file_path}"' if file_path else script) + "\n")
        self.tabs.setCurrentWidget(self.results)
        self._set_running(True)
        self._pending_result = None
        self._thread = QThread(self)
        self._worker = RunWorker(self.engine, script, file_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._receive_result)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._finish_run)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @Slot(object)
    def _receive_result(self, result):
        self._pending_result = result

    @Slot()
    def _finish_run(self):
        result = self._pending_result or SimpleNamespace(output="Command ended without a result.\n", rc=1, line=None)
        output = getattr(result, "output", "") or ""
        self._append_output(output + ("" if output.endswith("\n") else "\n"))
        rc = int(getattr(result, "rc", 0))
        if rc:
            self._active_history_item.setForeground(QColor("#b14e50"))
            self._active_history_item.setToolTip(self._active_script[:5500] + f"\n\nReturn code: {rc}")
            if f"r({rc})" not in output:
                self._append_output(f"r({rc});\n")
            line = getattr(result, "line", None)
            if line and self._active_editor_offset is not None:
                block = self.editor.document().findBlockByNumber(self._active_editor_offset + int(line) - 1)
                if block.isValid():
                    self.editor.setTextCursor(QTextCursor(block))
        self._append_output("\n")
        self._thread = None
        self._worker = None
        self._set_running(False)
        self._refresh_dataset()
        self._refresh_graphs()
        self.state_label.setText("●  Ready" if not rc else f"●  Finished · r({rc})")
        if self._browser:
            self._browser.refresh()
        if self._close_when_idle:
            self._close_when_idle = False
            self.close()
        else:
            self.command.setFocus()

    def _set_running(self, running):
        self.running = running
        for action in self._run_actions + self._dataset_actions:
            action.setEnabled(not running)
        self.command.setEnabled(not running)
        self.command_run.setEnabled(not running)
        self.stop_action.setEnabled(running)
        self.progress.setVisible(running)
        self.state_label.setText("●  Running" if running else "●  Ready")
        if self._browser:
            self._browser.set_running(running)

    def stop(self):
        if self.running:
            self.engine.cancel()
            self.state_label.setText("●  Stop requested")
            self.stop_action.setEnabled(False)

    def _refresh_dataset(self):
        frame = self.engine.data
        self.variables.clear()
        for name, dtype in frame.dtypes.items():
            item = QTreeWidgetItem([str(name), str(dtype)])
            item.setToolTip(0, self.engine.variable_labels.get(str(name), ""))
            self.variables.addTopLevelItem(item)
        self.filter_variables(self.variable_filter.text())
        self.properties.clear()
        for name, value in (("Label", self.engine.dataset_label or "—"), ("Observations", f"{len(frame):,}"), ("Variables", f"{len(frame.columns):,}"), ("Memory", f"{frame.memory_usage(deep=True).sum() / 1024:.1f} KB")):
            self.properties.addTopLevelItem(QTreeWidgetItem([name, value]))
        self.dataset_status.setText(f"Obs: {len(frame):,}    Vars: {len(frame.columns):,}   ")
        self.cwd_status.setText(str(self.engine.cwd))
        self.cwd_status.setToolTip(str(self.engine.cwd))

    def filter_variables(self, text):
        needle = text.lower()
        for index in range(self.variables.topLevelItemCount()):
            item = self.variables.topLevelItem(index)
            item.setHidden(needle not in item.text(0).lower() and needle not in item.toolTip(0).lower())

    def show_variable_properties(self):
        if self.running:
            return
        selected = self.variables.selectedItems()
        if not selected:
            return
        name = selected[0].text(0)
        if name not in self.engine.data.columns:
            return
        series = self.engine.data[name]
        self.properties.clear()
        for key, value in (("Name", name), ("Type", str(series.dtype)), ("Label", self.engine.variable_labels.get(name, "—")), ("Nonmissing", f"{series.notna().sum():,}"), ("Missing", f"{series.isna().sum():,}")):
            self.properties.addTopLevelItem(QTreeWidgetItem([key, value]))

    def insert_variable(self, item, _column):
        self.command.insert(item.text(0) + " ")
        self.command.setFocus()

    def filter_history(self, text):
        for index in range(self.history.count()):
            item = self.history.item(index)
            item.setHidden(text.lower() not in item.data(Qt.ItemDataRole.UserRole).lower())

    def recall_history(self, item):
        self.command.setText(item.data(Qt.ItemDataRole.UserRole))
        self.command.setFocus()

    def history_menu(self, pos):
        item = self.history.itemAt(pos)
        menu = QMenu(self)
        if item:
            menu.addAction("Recall command", lambda: self.recall_history(item))
            menu.addAction("Copy command", lambda: QApplication.clipboard().setText(item.data(Qt.ItemDataRole.UserRole)))
            menu.addSeparator()
        menu.addAction("Clear history", self.clear_history).setEnabled(not self.running)
        menu.exec(self.history.mapToGlobal(pos))

    def clear_history(self):
        if self.running:
            return
        self.history.clear()
        self.history_count.setText("  0 commands")
        self.command.history.clear()
        self.command.history_position = 0

    def update_editor_title(self, *_):
        name = self.do_path.name if self.do_path else "Untitled.do"
        mark = " *" if self.editor.document().isModified() else ""
        self.editor_path.setText((str(self.do_path) if self.do_path else name) + mark)
        self.tabs.setTabText(self.tabs.indexOf(self.editor_page), "Do-file editor" + mark)

    def update_editor_position(self):
        cursor = self.editor.textCursor()
        self.editor_position.setText(f"Ln {cursor.blockNumber() + 1}, Col {cursor.positionInBlock() + 1}")

    def _discard_or_save(self):
        if not self.editor.document().isModified():
            return True
        answer = QMessageBox.question(self, "Unsaved do-file", "Save changes to the do-file?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return self.save_do_file() if answer == QMessageBox.StandardButton.Save else True

    def new_do_file(self):
        if self._discard_or_save():
            self.do_path = None
            self.editor.clear()
            self.editor.document().setModified(False)
            self.update_editor_title()
            self.tabs.setCurrentWidget(self.editor_page)
            self.editor.setFocus()

    def open_do_file(self, checked=False, path=None):
        if not self._discard_or_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open do-file", str(self.engine.cwd), "Do-files (*.do *.ado);;Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            file = Path(path)
            try:
                text = file.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = file.read_text(encoding="cp1252")
            self.editor.setPlainText(text)
            self.do_path = file.resolve()
            self.editor.document().setModified(False)
            self.update_editor_title()
            self.tabs.setCurrentWidget(self.editor_page)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot open do-file", str(exc))

    def save_do_file(self, save_as=False):
        path = self.do_path
        if path is None or save_as:
            name, _ = QFileDialog.getSaveFileName(self, "Save do-file", str(path or self.engine.cwd / "Untitled.do"), "Do-files (*.do);;Ado-files (*.ado);;All files (*)")
            if not name:
                return False
            path = Path(name)
            if not path.suffix:
                path = path.with_suffix(".do")
        try:
            path.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.do_path = path.resolve()
            self.editor.document().setModified(False)
            self.update_editor_title()
            return True
        except OSError as exc:
            QMessageBox.warning(self, "Cannot save do-file", str(exc))
            return False

    def run_editor(self):
        self.execute(self.editor.toPlainText(), editor_offset=0)

    def run_selection(self):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            self.statusBar().showMessage("Select code in the do-file editor first.", 4000)
            self.tabs.setCurrentWidget(self.editor_page)
            return
        start_block = self.editor.document().findBlock(cursor.selectionStart()).blockNumber()
        self.execute(cursor.selectedText().replace("\u2029", "\n"), editor_offset=start_block)

    def open_dataset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open dataset", str(self.engine.cwd), "Stata datasets (*.dta);;All files (*)")
        if path:
            self.execute(f'use "{Path(path).as_posix()}", clear')

    def save_dataset(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save dataset", str(self.engine.cwd / "data.dta"), "Stata datasets (*.dta)")
        if path:
            self.execute(f'save "{Path(path).as_posix()}", replace')

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import CSV", str(self.engine.cwd), "CSV files (*.csv);;Text files (*.txt);;All files (*)")
        if path:
            self.execute(f'import delimited "{Path(path).as_posix()}", clear')

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(self.engine.cwd / "data.csv"), "CSV files (*.csv)")
        if path:
            self.execute(f'export delimited using "{Path(path).as_posix()}", replace')

    def clear_dataset(self):
        if QMessageBox.question(self, "Clear dataset", "Remove the dataset from memory? Unsaved data changes will be lost.") == QMessageBox.StandardButton.Yes:
            self.execute("clear")

    def change_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Working directory", str(self.engine.cwd))
        if directory:
            self.execute(f'cd "{Path(directory).as_posix()}"')

    def save_results(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save results", str(self.engine.cwd / "results.log"), "Log files (*.log);;Text files (*.txt)")
        if path:
            try:
                Path(path).write_text(self.results.toPlainText(), encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Cannot save results", str(exc))

    def show_data_browser(self):
        if self.running:
            return
        if self._browser is None:
            self._browser = DataBrowser(self)
        else:
            self._browser.refresh()
        self._browser.show()
        self._browser.raise_()
        self._browser.activateWindow()

    def command_dialog(self, title, prefix, fields):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        inputs = []
        for field in fields:
            control = QLineEdit()
            inputs.append(control)
            form.addRow(field + ":", control)
        layout.addLayout(form)
        preview = QLabel(prefix + " …")
        preview.setFont(QFont(MONO, 10))
        preview.setWordWrap(True)
        layout.addWidget(preview)
        def command():
            return prefix + " " + " ".join(field.text().strip() for field in inputs)
        for field in inputs:
            field.textChanged.connect(lambda *_: preview.setText(command()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.execute(command().strip())

    def install_package(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Install SSC package")
        dialog.resize(510, 240)
        layout = QVBoxLayout(dialog)
        explanation = QLabel("Downloads package files from SSC. Installation does not guarantee execution: commands must be supported by OpenStat's independent runtime. Package files keep their own licenses.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        package = QLineEdit()
        package.setPlaceholderText("Package name, e.g. estout")
        layout.addWidget(package)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Install")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and package.text().strip():
            self.execute("ssc install " + package.text().strip())

    def find_in_editor(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Find in do-file")
        layout = QHBoxLayout(dialog)
        text = QLineEdit()
        text.setPlaceholderText("Search text")
        layout.addWidget(text)
        button = QPushButton("Find next")
        layout.addWidget(button)
        def find():
            self.tabs.setCurrentWidget(self.editor_page)
            if not self.editor.find(text.text()):
                cursor = self.editor.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                self.editor.setTextCursor(cursor)
                if not self.editor.find(text.text()):
                    self.statusBar().showMessage("Text not found.", 3000)
        button.clicked.connect(find)
        text.returnPressed.connect(find)
        dialog.exec()

    def _refresh_graphs(self):
        paths = [Path(path) for path in getattr(self.engine, "graphs", []) if Path(path).is_file()]
        if paths == self._graph_paths:
            return
        new_graph = len(paths) > len(self._graph_paths)
        self._graph_paths = paths
        self.graph_selector.blockSignals(True)
        self.graph_selector.clear()
        self.graph_selector.addItems([path.name for path in paths])
        self.graph_selector.blockSignals(False)
        if paths:
            self.graph_selector.setCurrentIndex(len(paths) - 1)
            self.show_graph(len(paths) - 1)
        else:
            self.graph_label.setText("No graphs yet.\nCreate a graph from Graphics or a command.")
        self.graph_export.setEnabled(bool(paths))
        if new_graph:
            self.tabs.setCurrentWidget(self.graph_page)

    def show_graph(self, index):
        if not 0 <= index < len(self._graph_paths):
            return
        pixmap = QPixmap(str(self._graph_paths[index]))
        if pixmap.isNull():
            self.graph_label.setText("This graph could not be previewed.")
        else:
            size = self.graph_label.size() - QSize(24, 24)
            self.graph_label.setPixmap(pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "graph_selector"):
            QTimer.singleShot(0, lambda: self.show_graph(self.graph_selector.currentIndex()))

    def save_graph(self):
        index = self.graph_selector.currentIndex()
        if not 0 <= index < len(self._graph_paths):
            return
        source = self._graph_paths[index]
        path, _ = QFileDialog.getSaveFileName(self, "Save graph image", str(self.engine.cwd / source.name), f"Image (*{source.suffix})")
        if path:
            try:
                Path(path).write_bytes(source.read_bytes())
            except OSError as exc:
                QMessageBox.warning(self, "Cannot save graph", str(exc))

    def command_help(self):
        self.command_dialog("Command help", "help", ["Command (blank = overview)"])

    def show_compatibility(self):
        for path in (ROOT / "docs" / "COMPATIBILITY.md", ROOT / "docs" / "compatibility.md", Path(__file__).resolve().parent / "docs" / "compatibility.md", ROOT / "COMPATIBILITY.md"):
            if path.exists():
                dialog = QDialog(self)
                dialog.setWindowTitle("OpenStat compatibility")
                dialog.resize(850, 650)
                layout = QVBoxLayout(dialog)
                text = QTextEdit()
                text.setReadOnly(True)
                text.setMarkdown(path.read_text(encoding="utf-8"))
                layout.addWidget(text)
                buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
                buttons.rejected.connect(dialog.reject)
                layout.addWidget(buttons)
                dialog.exec()
                return
        QMessageBox.information(self, "Compatibility", "OpenStat is an experimental independent implementation. It supports a subset of Stata syntax. Full do-file, Mata, SSC package, and statistical compatibility is not established. Unsupported commands report an error; validate results before relying on them.")

    def show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", "Ctrl+N   New do-file\nCtrl+O   Open do-file\nCtrl+S   Save do-file\nCtrl+D   Run do-file\nCtrl+Shift+D   Run selection\nCtrl+Break   Request stop\nCtrl+1   Results\nCtrl+9   Do-file editor\nCtrl+8   Data Browser\nCtrl+L   Command entry\nCtrl+F   Find in do-file\nUp / Down   Recall command history\nF1   Command help")

    def show_about(self):
        QMessageBox.about(self, "About OpenStat", "<h2>OpenStat</h2><p>Independent, open-source statistical workstation.</p><p>MIT licensed. Experimental runtime.</p><p>Stata-like workflows, do-file editing, data browsing, and an explicit compatibility surface.</p><p>OpenStat is not affiliated with or endorsed by StataCorp. Stata and Mata are trademarks of StataCorp LLC.</p>")

    def closeEvent(self, event):
        if self.running:
            answer = QMessageBox.question(self, "Command running", "Request stop, then close OpenStat?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self._close_when_idle = True
                self.stop()
            event.ignore()
            return
        if self._discard_or_save():
            if self._browser:
                self._browser.close()
            event.accept()
        else:
            event.ignore()


def main(argv=None):
    """Start the workstation; --smoke-test PNG saves an offscreen UI capture."""
    parser = argparse.ArgumentParser(description="OpenStat native statistical workstation")
    parser.add_argument("file", nargs="?", help="Do-file to open in the editor")
    parser.add_argument("--smoke-test", metavar="PNG", help="Capture the application and exit (set QT_QPA_PLATFORM=offscreen for CI)")
    args = parser.parse_args(argv)
    application = QApplication.instance() or QApplication(["OpenStat"])
    application.setApplicationName("OpenStat")
    application.setOrganizationName("OpenStat")
    application.setStyle("Fusion")
    window = OpenStatWindow()
    if args.file:
        window.open_do_file(path=args.file)
    window.show()
    if args.smoke_test:
        def capture():
            output = Path(args.smoke_test).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            success = window.grab().save(str(output), "PNG")
            application.exit(0 if success else 1)
        QTimer.singleShot(500, capture)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
