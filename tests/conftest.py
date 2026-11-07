import os
import sys

import pytest

FAKE_DIR = os.path.join(os.path.dirname(__file__), "fake_tools")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    """PATH auf die Fake-Tools umbiegen (simuliertes iPhone)."""
    monkeypatch.setenv("PATH", FAKE_DIR + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_DELAY", "0")
    return tmp_path


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication
    app = QCoreApplication.instance() or QApplication([])
    return app
