"""Native UI contracts: serialized execution, cancellation, and safe data edits."""

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from openstat.gui import DataFrameModel, OpenStatWindow


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication(["OpenStat tests"])


class FakeEngine:
    def __init__(self, cwd):
        self.cwd = Path(cwd)
        self.data = pd.DataFrame({"x": [1, 2, 3], "group": ["a", "b", "a"]})
        self.variable_labels = {"x": "Example variable"}
        self.dataset_label = "Synthetic GUI checks"
        self.graphs = []
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.calls = []

    def run(self, script):
        self.calls.append(script)
        self.cancelled.clear()
        self.started.set()
        if script == "slow":
            self.cancelled.wait(2)
            return SimpleNamespace(output="Stopped.\n", rc=1, line=1)
        return SimpleNamespace(output="Done.\n", rc=0, line=None)

    def run_file(self, path):
        return self.run(Path(path).read_text(encoding="utf-8"))

    def cancel(self):
        self.cancelled.set()


def drain_until(application, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()
    assert predicate(), "GUI worker did not complete within the bounded timeout"


def test_execution_is_serialized_and_stop_unlocks_dataset(application, tmp_path):
    engine = FakeEngine(tmp_path)
    window = OpenStatWindow(engine)
    window.show_data_browser()
    window.execute("slow")
    assert window.running
    assert not window.command.isEnabled()
    assert not window._browser.table.isEnabled()
    window.execute("second command")
    assert window.history.count() == 1
    drain_until(application, engine.started.is_set)
    window.stop()
    drain_until(application, lambda: not window.running)
    assert engine.calls == ["slow"]
    assert window.command.isEnabled()
    assert window._browser.table.isEnabled()
    assert "Stopped." in window.results.toPlainText()
    assert "r(1)" in window.results.toPlainText()
    window.close()


def test_data_model_edits_validate_and_preserve_large_integers(application):
    original = pd.DataFrame({"x": pd.Series([9007199254740993], dtype="int64")})
    writes = []
    model = DataFrameModel(original, on_edit=lambda row, col, value: writes.append(value))
    index = model.index(0, 0)
    assert not model.setData(index, "9")
    model.editable = True
    assert not model.setData(index, "not numeric")
    assert not writes
    assert model.setData(index, "9007199254740995")
    assert writes == [9007199254740995]
    assert model.data(index) == "9007199254740995"
    assert original.iat[0, 0] == 9007199254740993


def test_do_file_save_and_filtered_variable_browser(application, tmp_path):
    window = OpenStatWindow(FakeEngine(tmp_path))
    window.do_path = tmp_path / "check.do"
    window.editor.setPlainText("summarize x\n")
    window.editor.insertPlainText("* synthetic check\n")
    assert window.save_do_file()
    assert window.do_path.read_text(encoding="utf-8") == window.editor.toPlainText()
    assert not window.editor.document().isModified()
    window.variable_filter.setText("Example")
    assert not window.variables.topLevelItem(0).isHidden()
    assert window.variables.topLevelItem(1).isHidden()
    window.variable_filter.clear()
    assert not window.variables.topLevelItem(1).isHidden()
    window.close()


def test_real_engine_refreshes_dataset_panels_after_background_run(application, tmp_path):
    from openstat.engine import Engine

    window = OpenStatWindow(Engine(cwd=tmp_path, ado_dir=tmp_path / "ado"))
    window.execute("clear\nset obs 4\ngenerate x = _n\ngenerate y = 2*x\nsummarize x y")
    drain_until(application, lambda: not window.running)
    assert list(window.engine.data.columns) == ["x", "y"]
    assert window.variables.topLevelItemCount() == 2
    assert "Obs: 4" in window.dataset_status.text()
    assert "Variable" in window.results.toPlainText()
    assert window.history.count() == 1
    window.close()
