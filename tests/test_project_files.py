from pathlib import Path
import re
import tomllib

from spd_model_injector import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_gitignore_excludes_large_spd_and_model_inputs() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "*.spd" in gitignore
    assert "*.mod" in gitignore
    assert "dist/" in gitignore


def test_packaging_files_define_app_and_installer_names() -> None:
    pyinstaller_spec = (ROOT / "packaging" / "spd-model-injector.spec").read_text(encoding="utf-8")
    inno_setup = (ROOT / "packaging" / "spd-model-injector.iss").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")

    assert "src/spd_model_injector/app.py" in pyinstaller_spec
    assert "SPD Model Injector" in pyinstaller_spec
    assert "spd-model-injector" in inno_setup
    assert "SPD-Model-Injector-Setup" in inno_setup
    assert "$LASTEXITCODE" in build_script


def test_version_metadata_is_synchronized() -> None:
    pyproject_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    inno_setup = (ROOT / "packaging" / "spd-model-injector.iss").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    publish_script = (ROOT / "scripts" / "publish_release.ps1").read_text(encoding="utf-8")

    assert pyproject_version == __version__
    assert re.search(r'#define MyAppVersion "' + re.escape(__version__) + r'"', inno_setup)
    assert re.search(r'\[string\]\$Version = "' + re.escape(__version__) + r'"', build_script)
    assert re.search(r'\[string\]\$Version = "' + re.escape(__version__) + r'"', publish_script)


def test_readme_documents_streaming_and_utf8_lf_guarantees() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "streaming" in readme.lower()
    assert "UTF-8" in readme
    assert "LF" in readme
    assert "PartialCkt" in readme
