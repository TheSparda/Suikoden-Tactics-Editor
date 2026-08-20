#!/usr/bin/env python3
"""Build the single-file editor: dist/SuikodenTacticsEditorPackage.pyz (stdlib zipapp).

Bundles st-editor/*.py + data/st_*.json into one executable Python archive, plus
double-click launchers for macOS/Linux and Windows, and a flat release zip of
just the end-user files (no source tree).

Run the bundle with `python3 dist/SuikodenTacticsEditorPackage.pyz` (double-click
on Windows with the Python launcher installed; use the emitted .command on macOS).

Unlike a flat zipapp, the editor reads its JSON tables via open() relative to
each module, which does not work from inside a zip. So the bundled __main__.py
extracts the archive to a temp dir on first launch and runs the editor from
there — every open() then resolves normally, with zero changes to the modules.
"""
import os, sys, shutil, stat, tempfile, zipapp, zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "st-editor")
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "SuikodenTacticsEditorPackage.pyz")

# Bootstrap: if we're running from inside the .pyz, self-extract to a temp dir
# (so data/*.json load via plain open()), put it on sys.path, then launch.
MAIN = '''import os, sys, tempfile, zipfile
_here = os.path.dirname(os.path.abspath(__file__))
if zipfile.is_zipfile(_here):
    _dst = tempfile.mkdtemp(prefix="st_editor_")
    with zipfile.ZipFile(_here) as _z:
        _z.extractall(_dst)
    sys.path.insert(0, _dst)
    os.chdir(_dst)
import steditor
steditor.main()
'''

SH_LAUNCHER = '''#!/bin/sh
# Suikoden Tactics Editor — macOS / Linux launcher (double-click on macOS).
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/"
  read -r _; exit 1
fi
exec "$PY" "$DIR/SuikodenTacticsEditorPackage.pyz" "$@"
'''

BAT_LAUNCHER = '''@echo off
rem Suikoden Tactics Editor - Windows launcher (double-click).
where py >nul 2>nul && ( py -3 "%~dp0SuikodenTacticsEditorPackage.pyz" %* & goto :eof )
where python >nul 2>nul && ( python "%~dp0SuikodenTacticsEditorPackage.pyz" %* & goto :eof )
echo Python 3 is required. Install from https://www.python.org/downloads/ and tick "Add to PATH".
pause
'''


def main():
    if not os.path.isdir(SRC):
        print("error: st-editor/ not found next to make_release.py"); return 1
    os.makedirs(DIST, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        npy = 0
        for name in sorted(os.listdir(SRC)):
            if name.endswith(".py"):
                shutil.copy2(os.path.join(SRC, name), os.path.join(tmp, name)); npy += 1
        data_src = os.path.join(SRC, "data")
        njson = 0
        if os.path.isdir(data_src):
            os.makedirs(os.path.join(tmp, "data"))
            for name in sorted(os.listdir(data_src)):
                if name.endswith(".json"):
                    shutil.copy2(os.path.join(data_src, name),
                                 os.path.join(tmp, "data", name)); njson += 1
        with open(os.path.join(tmp, "__main__.py"), "w") as f:
            f.write(MAIN)
        zipapp.create_archive(tmp, OUT, interpreter="/usr/bin/env python3", compressed=True)
    os.chmod(OUT, os.stat(OUT).st_mode | stat.S_IEXEC)

    launchers = []
    mac = os.path.join(DIST, "Start Editor (Mac, Linux).command")
    with open(mac, "w", newline="\n") as f: f.write(SH_LAUNCHER)
    os.chmod(mac, os.stat(mac).st_mode | stat.S_IEXEC); launchers.append(mac)
    win = os.path.join(DIST, "Start Editor (Windows).bat")
    with open(win, "w", newline="\r\n") as f: f.write(BAT_LAUNCHER)
    launchers.append(win)

    print("bundled %d modules + %d json -> %s (%d KB)"
          % (npy, njson, OUT, os.path.getsize(OUT) // 1024))
    for p in launchers:
        print("launcher -> %s" % p)

    # Flat release zip: only the 3 end-user files, no source tree.
    ver = sys.argv[1] if len(sys.argv) > 1 else ""
    zname = "SuikodenTacticsEditor-%s.zip" % ver if ver else "SuikodenTacticsEditor.zip"
    zpath = os.path.join(DIST, zname)
    payload = [OUT] + launchers
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in payload:
            zi = zipfile.ZipInfo(os.path.basename(p))
            zi.external_attr = (os.stat(p).st_mode & 0xFFFF) << 16   # keep +x bit
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as f:
                z.writestr(zi, f.read())
    print("release zip -> %s (%d KB, %d files)"
          % (zpath, os.path.getsize(zpath) // 1024, len(payload)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
