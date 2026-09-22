"""Render packaging/icon.svg into the app icon files shipped inside the package.

Usage: python scripts/make_icon.py
Writes src/spd_model_injector/ui/app.ico (16-256 px, PNG-compressed entries) and app.png (256 px).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "packaging" / "icon.svg"
OUT_DIR = ROOT / "src" / "spd_model_injector" / "ui"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def pack_ico(images: list[tuple[int, bytes]]) -> bytes:
    """ICO container with PNG-compressed entries (supported by Windows Vista+, PyInstaller, Inno Setup, Qt)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    return header + entries + b"".join(png for _, png in images)


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        raise SystemExit(f"Invalid SVG: {SVG}")
    images = [(size, render_png(renderer, size)) for size in SIZES]
    (OUT_DIR / "app.ico").write_bytes(pack_ico(images))
    (OUT_DIR / "app.png").write_bytes(images[-1][1])
    print(f"Wrote {OUT_DIR / 'app.ico'} ({len(SIZES)} sizes) and app.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
