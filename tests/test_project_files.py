from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

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


def test_app_startup_check_exits_successfully() -> None:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run([sys.executable, "-m", "spd_model_injector.app", "--smoke-test"],
                            cwd=ROOT, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script_name, commands, expected", [
    ("build.ps1", "function global:python { $global:LASTEXITCODE = 7 }; "
     "function global:pyinstaller { throw 'Unexpected side effect' }; ", b"Tests failed with exit code 7"),
    ("publish_release.ps1", "function global:gh { $global:LASTEXITCODE = 7 }; "
     "function global:git { throw 'Unexpected side effect' }; ", b"GitHub authentication failed"),
])
def test_build_and_release_stop_when_a_required_command_fails(script_name, commands, expected) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    script = str(ROOT / "scripts" / script_name).replace("'", "''")
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         commands + f"& '{script}'"],
        capture_output=True, timeout=30,
    )
    assert result.returncode != 0
    assert expected in result.stderr
    assert b"Unexpected side effect" not in result.stderr
