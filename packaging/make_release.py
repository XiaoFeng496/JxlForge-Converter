# -*- coding: utf-8 -*-
"""JxlForge Converter release packager (ZIP + 7Z). [English version]

Usage:
    Double-click packaging/make_release.bat  (recommended)
    CLI: python packaging/make_release.py
    Custom: python packaging/make_release.py --src <dir> --out <dir>

What it does:
    1. Reads __version__ from jxlforge/__init__.py (no hardcoded version);
    2. Packs dist/JxlForge Converter/ into
       JxlForge-Converter_v<version>_win64.zip and .7z;
    3. Writes to JxlForge-Build/release/ (sibling of the repo, not in git).

Backends:
    ZIP - 7-Zip ("-tzip -mx=9") when available (better/faster deflate);
          falls back to stdlib zipfile with compresslevel=9 (zero deps).
          Note: ZIP has no solid compression, so it is inherently larger than
          7z for a dist full of similar DLLs - this is a format limit, not a bug.
    7Z  - 7-Zip (7z.exe) -> py7zr. If neither exists, 7Z is skipped with a
          hint (ZIP is still produced).

This script only packs; it does not build. Run build_dist.bat first.
"""

import os
import re
import sys
import shutil
import zipfile
import subprocess
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))          # packaging/
REPO = os.path.normpath(os.path.join(_HERE, ".."))           # repo root
# Same convention as build_dist.bat: build output sits in JxlForge-Build/
# next to the repo (not inside it).
BUILD = os.path.normpath(os.path.join(REPO, "..", "JxlForge-Build"))

DEFAULT_SRC = os.path.join(BUILD, "dist", "JxlForge Converter")
DEFAULT_OUT = os.path.join(BUILD, "release")

EXE_NAME = "JxlForge Converter.exe"

# Build-time artifacts that must NOT ship to end users.
# selftest_report.txt records the BUILD machine's self-test (including whether
# libjxl was on that machine's PATH), so it says nothing about the user's PC
# and only invites confusion next to the exe. Users can regenerate it any time
# by running `JxlForge Converter.exe --selftest`.
# NOTE: it is still kept in dist/ -- the pre-pack gate reads it there, it is
# just excluded from the archives.
EXCLUDE_FROM_PACKAGE = ("selftest_report.txt",)

SRC_DIR = DEFAULT_SRC
OUT_DIR = DEFAULT_OUT
NO_PAUSE = False


def read_version():
    """Parse __version__ from jxlforge/__init__.py."""
    init_py = os.path.join(REPO, "jxlforge", "__init__.py")
    with open(init_py, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"""__version__\s*=\s*["']([^"']+)["']""", line)
            if m:
                return m.group(1)
    raise RuntimeError("__version__ not found in %s" % init_py)


def check_dist():
    """Pre-pack gate: dist exists, exe present, self-test PASS (warn only)."""
    if not os.path.isdir(SRC_DIR):
        raise RuntimeError(
            "Build output not found: %s\nRun packaging\\build_dist.bat first." % SRC_DIR
        )
    if not os.path.isfile(os.path.join(SRC_DIR, EXE_NAME)):
        raise RuntimeError("Missing %s in build output; aborting." % EXE_NAME)

    report = os.path.join(SRC_DIR, "selftest_report.txt")
    if os.path.isfile(report):
        text = open(report, "r", encoding="utf-8", errors="replace").read()
        if "RESULT: PASS" in text:
            print("[OK ] Self-test report is PASS")
        else:
            print("[WARN] Self-test report is NOT PASS. Packing continues, "
                  "but verify the build:")
            print("       %s" % report)
    else:
        print("[WARN] selftest_report.txt not found (build may be unverified)")


def source_size():
    total = 0
    for root, _dirs, files in os.walk(SRC_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _find_exe(names, extra_paths):
    """PATH first (portable across machines), then known install paths."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for p in extra_paths:
        if p and os.path.isfile(p):
            return p
    return None


def find_7z_exe():
    """Locate 7-Zip. PATH wins, so a portable install elsewhere still works.

    Not using Bandizip: Bandizip.exe is the GUI binary and rejects 7z-style
    CLI args with an "invalid parameter" dialog (its CLI is a separate bz.exe).
    """
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "E:\\7-Zip",
    ]
    seen = set()
    extra = []
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        extra.append(os.path.join(root, "7-Zip", "7z.exe"))
    return _find_exe(("7z", "7za"), extra)


def _remove_stale_archive(path):
    """Delete a previous archive before packing.

    7-Zip's "a" (add) command never removes entries that already exist in the
    archive, it only adds/updates. Re-packing onto an old archive would
    therefore keep excluded files (e.g. selftest_report.txt) forever. Always
    start from a fresh archive so exclusion actually takes effect.
    """
    if os.path.exists(path):
        os.remove(path)


def make_zip(out_path):
    """Pack ZIP. Prefer 7-Zip (-tzip -mx=9); fall back to stdlib zipfile(level 9).

    Returns the file count, or None when produced by 7-Zip.
    """
    _remove_stale_archive(out_path)
    sevenz = find_7z_exe()
    if sevenz:
        print("[ZIP] Using 7-Zip: %s" % sevenz)
        cmd = [sevenz, "a", "-tzip", out_path, os.path.basename(SRC_DIR),
               "-mx=9", "-mmt=on", "-y"]
        for name in EXCLUDE_FROM_PACKAGE:
            cmd.append("-xr!" + name)
        subprocess.run(cmd, cwd=os.path.dirname(SRC_DIR), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None

    print("[ZIP] Using stdlib zipfile (DEFLATE level 9), may take a while...")
    base = os.path.dirname(SRC_DIR)
    n = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, files in os.walk(SRC_DIR):
            for name in files:
                if name in EXCLUDE_FROM_PACKAGE:
                    continue
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, base))
                n += 1
    return n


def make_7z_with_7zip(sevenz, out_path):
    """7Z via official 7-Zip CLI (fastest, best ratio)."""
    print("[7Z ] Using 7-Zip: %s" % sevenz)
    cmd = [sevenz, "a", "-t7z", out_path, os.path.basename(SRC_DIR),
           "-mx=9", "-mmt=on", "-y"]
    for name in EXCLUDE_FROM_PACKAGE:
        cmd.append("-xr!" + name)
    subprocess.run(cmd, cwd=os.path.dirname(SRC_DIR), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_7z_with_py7zr(out_path):
    """Fallback: pure-Python py7zr (slower, no external binary needed)."""
    import py7zr
    print("[7Z ] Using py7zr (pure Python, slower, please wait)...")
    filters = [{"id": py7zr.FILTER_LZMA2, "preset": 7}]
    base = os.path.dirname(SRC_DIR)
    with py7zr.SevenZipFile(out_path, "w", filters=filters) as z:
        for root, _dirs, files in os.walk(SRC_DIR):
            for name in files:
                if name in EXCLUDE_FROM_PACKAGE:
                    continue
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, base))


def make_7z(out_path):
    """Probe 7-Zip -> py7zr. False if neither is available (ZIP unaffected)."""
    _remove_stale_archive(out_path)
    sevenz = find_7z_exe()
    if sevenz:
        make_7z_with_7zip(sevenz, out_path)
        return True
    try:
        import py7zr  # noqa: F401
    except ImportError:
        return False
    make_7z_with_py7zr(out_path)
    return True


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.2f %s" % (size, unit)
        size /= 1024.0


def main():
    global SRC_DIR, OUT_DIR, NO_PAUSE

    ap = argparse.ArgumentParser(description="Pack JxlForge Converter release (ZIP + 7Z)")
    ap.add_argument("--src", default=DEFAULT_SRC, help="source dir (default dist/JxlForge Converter)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output dir (default JxlForge-Build/release)")
    ap.add_argument("--no-pause", action="store_true",
                    help="do not wait for a key at the end (used by the .bat launcher)")
    args = ap.parse_args()
    SRC_DIR = os.path.abspath(args.src)
    OUT_DIR = os.path.abspath(args.out)
    NO_PAUSE = args.no_pause

    print("=" * 60)
    print("JxlForge Converter release packager")
    print("=" * 60)
    version = read_version()
    print("[INFO] Version: %s (from jxlforge/__init__.py)" % version)

    check_dist()
    print("[INFO] Source: %s (%s)" % (SRC_DIR, human(source_size())))

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = "JxlForge-Converter_v%s_win64" % version
    zip_path = os.path.join(OUT_DIR, stem + ".zip")
    sz_path = os.path.join(OUT_DIR, stem + ".7z")

    made = []
    try:
        n = make_zip(zip_path)
        if n is None:
            print("[OK ] ZIP done -> %s (%s)" % (zip_path, human(os.path.getsize(zip_path))))
        else:
            print("[OK ] ZIP done: %d files -> %s (%s)"
                  % (n, zip_path, human(os.path.getsize(zip_path))))
        made.append(zip_path)
    except Exception as e:
        print("[FAIL] ZIP failed: %r" % (e,))

    try:
        if make_7z(sz_path):
            print("[OK ] 7Z  done -> %s (%s)" % (sz_path, human(os.path.getsize(sz_path))))
            made.append(sz_path)
        else:
            print("[SKIP] 7Z not created: no 7-Zip and no py7zr installed.")
            print("       Enable either one:")
            print("       - 7-Zip: winget install 7zip.7zip")
            print("       - lib  : pip install py7zr")
    except Exception as e:
        print("[FAIL] 7Z failed: %r" % (e,))

    print("-" * 60)
    if made:
        print("Done, %d package(s) created:" % len(made))
        for p in made:
            print("  " + p)
    else:
        print("No package was created.")
    print("-" * 60)
    return 0 if made else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception as exc:
        print("[FAIL] %s" % exc)
        rc = 1
    # Only pause when run directly; the .bat launcher passes --no-pause and
    # shows its own prompt, so double-clicking does not need two key presses.
    if not NO_PAUSE:
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass
    sys.exit(rc)
