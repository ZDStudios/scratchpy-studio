#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a standalone ScratchPy Studio application for THIS computer.

    python build_apps.py

    Windows   ->  dist/ScratchPyStudio.exe          (double click, no Python)
    macOS     ->  dist/ScratchPy Studio.app         (+ a .zip beside it)
    Linux     ->  dist/scratchpy-studio             (+ a .desktop launcher)

PyInstaller cannot cross compile: a Windows machine can only make the .exe,
a Mac only the .app and a Linux box only the ELF binary.  To get all three
without owning all three machines, push this folder to GitHub - the workflow
in .github/workflows/build-apps.yml builds every platform for you and
attaches the results to the run.

Options:
    --onedir     folder instead of a single file (starts noticeably faster)
    --no-clean   keep the previous build folder
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "scratchpy_studio.py")
ASSETS = os.path.join(HERE, "scratchpy_assets")
# Intermediate files go to the temp folder: cloud synced folders (OneDrive,
# Dropbox) lock files while PyInstaller is trying to clean them up.
WORK = os.path.join(tempfile.gettempdir(), "scratchpy_build")
NAME = "ScratchPyStudio"
PRETTY = "ScratchPy Studio"


def run(args, **kw):
    print("$ " + " ".join(str(a) for a in args))
    return subprocess.run(args, check=True, **kw)


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("PyInstaller is not installed yet - fetching it with pip.")
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"])


def make_icons():
    print("Drawing the application icon...")
    run([sys.executable, SCRIPT, "--make-icons", ASSETS])


def build():
    onedir = "--onedir" in sys.argv
    system = platform.system()
    if "--no-clean" not in sys.argv:
        shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm",
            "--name", NAME if system != "Darwin" else PRETTY,
            "--windowed",
            "--onedir" if onedir else "--onefile",
            "--distpath", os.path.join(HERE, "dist"),
            "--workpath", WORK,
            "--specpath", WORK,
            "--add-data", ASSETS + os.pathsep + "scratchpy_assets"]

    if system == "Windows":
        args += ["--icon", os.path.join(ASSETS, "scratchpy_icon.ico")]
    elif system == "Darwin":
        args += ["--icon", os.path.join(ASSETS, "scratchpy_icon.icns"),
                 "--osx-bundle-identifier", "com.scratchpy.studio"]
    else:
        # ELF binaries carry no icon; the .desktop file below points at the png
        args += []

    args.append(SCRIPT)
    run(args)

    dist = os.path.join(HERE, "dist")
    if system == "Darwin":
        app = os.path.join(dist, PRETTY + ".app")
        if os.path.isdir(app):
            zip_path = os.path.join(dist, "ScratchPy-Studio-macOS.zip")
            print("Zipping " + app)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(app):
                    for f in files:
                        full = os.path.join(root, f)
                        zf.write(full, os.path.relpath(full, dist))
            print("Wrote " + zip_path)
    elif system == "Linux":
        png = os.path.join(ASSETS, "scratchpy_icon.png")
        shutil.copy(png, os.path.join(dist, "scratchpy-studio.png"))
        desktop = os.path.join(dist, "scratchpy-studio.desktop")
        with open(desktop, "w", encoding="utf-8") as fh:
            fh.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=ScratchPy Studio\n"
                "Comment=Build real Python programs out of Scratch style blocks\n"
                "Exec=%s/%s\n"
                "Icon=%s/scratchpy-studio.png\n"
                "Terminal=false\n"
                "Categories=Development;Education;IDE;\n"
                % (dist, NAME, dist))
        os.chmod(desktop, 0o755)
        print("Wrote " + desktop)

    print()
    print("Done. Look inside: " + dist)
    for entry in sorted(os.listdir(dist)):
        full = os.path.join(dist, entry)
        size = ""
        if os.path.isfile(full):
            size = "  %.1f MB" % (os.path.getsize(full) / 1048576.0)
        print("   " + entry + size)


if __name__ == "__main__":
    if not os.path.exists(SCRIPT):
        sys.exit("scratchpy_studio.py must sit next to this script.")
    ensure_pyinstaller()
    make_icons()
    build()
