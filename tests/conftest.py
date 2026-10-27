import pytest
from PySide6.QtCore import QSettings

from spd_model_injector.ui import model_library


@pytest.fixture(autouse=True)
def isolated_model_library_settings(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "library-settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(model_library, "QSettings", lambda *args: settings)
