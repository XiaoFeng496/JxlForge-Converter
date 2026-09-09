# JxlForge Converter

![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg) ![Platform](https://img.shields.io/badge/platform-Windows-blue) ![Python](https://img.shields.io/badge/python-3.10%2B-blue)

A **PySide6**-based GUI for [libjxl](https://github.com/libjxl/libjxl), making JPEG XL encoding / decoding a visual desktop tool.

[简体中文](README.md) | [繁體中文](README_TW.md) | **English**

> All code in this project is written in natural language with AI assistance.

> **Platform**: Pre-built binaries and testing are currently available only for **Windows**. The codebase is built on cross-platform PySide6 and cjxl/djxl, so it *should* run on macOS / Linux in principle, but it has not been packaged or verified for those platforms. On those platforms, install the dependencies and run `python main.py`.

---

## Features

- **Input formats (encode to JXL)**: PNG / APNG / GIF / JPEG / JPE / JFIF / EXR / PPM / PGM / PFM / PAM / PGX / PBM / JXL / WebP / AVIF / BMP / TIFF / ICO / HEIC / HEIF
  - WebP / AVIF / BMP / TIFF / ICO / HEIC / HEIF are decoded to a temporary PNG by Pillow before being passed to cjxl; ICC profiles are preserved (HEIC/HEIF requires the optional pi-heif plugin; see Dependencies).
  - All other formats are passed directly to cjxl.
  - EXR is floating-point HDR; only header metadata is parsed, pixel thumbnails are not rendered.
- **Encode (→ JXL)**
  - Lossy (distance) / lossless (including JPEG lossless recompression `--lossless_jpeg=1`)
  - Advanced options: `effort` 1–10, `--faster_decoding` 0–4, custom commands
- **Decode (JXL → PNG/PNM)** and basic info view (jxlinfo)
- **Action pipeline**: move up / move down / remove, two-line per-action layout, batch conversion
- **Dual-queue scheduling**: tasks are split by pixel count into "large" and "small" images — very large images use all cores one at a time, while the remaining small images run in parallel with leftover cores. No idle cores and no one task dragging down the whole batch. The threshold is auto-calibrated on first launch.
- **Preserve source timestamps**: modification time + creation time
- **Five-tab design**: Input / Action / Output / Status / Settings; persistent bottom buttons Convert / Stop / Close
- **Language**: Simplified Chinese / Traditional Chinese / English (takes effect after restart)
- **Theme**: Follow system / Light / Dark
- **Widget style**: Native and variants / Fusion
- **Persistent settings**: window position, output directory, filename template, custom history, theme, etc. are stored via QSettings (ini)

---

## ⚠️ Dependency: libjxl command-line tools must be installed separately

This program **does not ship the cjxl / djxl / jxlinfo engines**. Choose your own libjxl version:

1. Install libjxl from the [official libjxl releases](https://github.com/libjxl/libjxl) (recommended: **v0.12.0**, tested in this environment).
2. Add the directory containing `cjxl.exe` / `djxl.exe` / `jxlinfo.exe` to your system **PATH**.
3. Restart this program. The Settings tab will show cjxl/djxl as "Found"; if missing, a popup will guide you before conversion and when previewing JXL.

> This design lets you pick any libjxl version instead of being tied to a bundled one.

---

## Download & Run (Release Build)

1. Download `JxlForge-Converter_vX.X.X_win64.zip` from [Releases](../../releases) and extract it anywhere.
2. Install libjxl and add it to PATH.
3. Run `JxlForge Converter\JxlForge Converter.exe`.

- The release package is not code-signed. On first run Windows SmartScreen may show "Windows protected your PC"; click "More info" → "Run anyway". Some antivirus tools may falsely flag PyInstaller-packaged executables; this is normal.

---

## Project Layout

```
JxlForge-Converter/
├── main.py              # Development entry point (imports jxlforge.__main__:run)
├── run.bat             # One-click development launch (pythonw, no console)
├── run.pyw             # Console-free launch entry point (Python Launcher)
├── setup.py            # Environment / libjxl detection and install script
├── install_deps.sh     # Cross-platform dependency installer (macOS / Linux / WSL)
├── requirements.txt    # Python dependencies
├── packaging/          # Packaging config
│   ├── JxlForge Converter.spec   # PyInstaller spec (Tier2 trimmed build)
│   ├── build_dist.bat           # One-click build + self-test gate
│   └── _launch_app.py           # Production entry point (calls run())
├── jxlforge/           # Main package
│   ├── __main__.py     # Entry run() and --selftest
│   ├── main_window.py  # Main window and all interactions
│   ├── converter.py    # cjxl / djxl / jxlinfo invocation and argument building
│   ├── processor.py    # Conversion task processing
│   ├── calibrate.py    # Power calibration
│   ├── power.py        # Power status
│   ├── formats.py      # Format decoding (PFM/PAM/PGX/PBM → PPM transit, etc.)
│   ├── combo_switch.py / no_flicker_combo.py  # Custom flicker-free combo box
│   ├── i18n.py         # i18n loader
│   └── i18n/           # Translation dictionaries (en_US.json / zh_TW.json)
├── tools/              # Tests, probes, and scripts
│   ├── run_tests.py    # Regression test entry point
│   ├── test_*.py       # Headless regression tests
│   ├── probe_*.py      # Behavior / performance probes
│   ├── extract_i18n.py / classify_i18n.py / check_i18n_coverage.py / i18n_wrap_source.py / apply_i18n_batch.py  # i18n toolchain
│   └── test_packaged_e2e.py  # Packaged build E2E self-test driver
├── docs/
│   ├── design-decisions.md   # Design decision records
│   └── i18n-workflow.md      # i18n workflow
├── LICENSE             # Full GPL v3 text
└── README_EN.md
```

---

## License

**GPL v3** (see [LICENSE](LICENSE)).

---

## Dependencies

- [libjxl](https://github.com/libjxl/libjxl) (cjxl/djxl/jxlinfo, supplied by the user at runtime, BSD-3-Clause)
- [PySide6](https://doc.qt.io/qtforpython/) (Qt 6 bindings, LGPL v3)
- [Pillow](https://python-pillow.org/) (MIT)
- [pi-heif](https://github.com/bigcat88/pillow_heif/tree/master/pi-heif) (**optional but strongly recommended**: enables HEIC/HEIF input; without it only that format is unavailable. A decoder-only trimmed-down fork of pillow-heif, BSD-3-Clause)
- Python 3.12 (PSF License)

---

## Known Limitations

- libjxl engine is not bundled (by design; see above).
- No code signing.
- JPEG XR (.jxr / HD Photo) is not supported: Pillow is not built with the corresponding decoder, adding the dependency has low benefit, so it is not included for now.
- Multi-page TIFF only converts page 0 (remaining pages are ignored).
- This is the first release of my first project; feedback via Issues is welcome.

---

## Acknowledgements

- Layout and design ideas are partly inspired by [XnConvert](https://www.xnconvert.com/); this project is not affiliated with or endorsed by XnConvert.
- This program is a graphical wrapper for [libjxl](https://github.com/libjxl/libjxl); the core codec capability comes from the libjxl project.

---

## Contributing

Issues / PRs are welcome. For development, keep the headless regression tests in `tools/` green (they run on the PySide6 offscreen platform).

### For Developers (Build from Source)

```bat
git clone <your-repo-url> JxlForge-Converter
cd JxlForge-Converter
pip install -r requirements.txt
python main.py          REM Or double-click run.bat / run.pyw for console-free launch
```

Build a release package:

```bat
packaging\build_dist.bat
```

> Build output goes to `JxlForge-Build/`, which sits **next to** the repo directory, not inside git. The exact path is automatically derived from the repo location by `packaging/build_dist.bat` (e.g. repo at `D:\x\JxlForge-Converter` → output at `D:\x\JxlForge-Build`). The Python interpreter is also auto-detected (PATH → `py -3` → `E:\Python\Python312`), so no fixed drive letter is required.

It will: ① safely move the old `dist` (to avoid triggering the safe-delete guard) ② build with the spec (which already bundles `jxlforge/i18n/*.json` as data) ③ run `exe --selftest`; missing resources will fail loudly.

The release build includes a `selftest`: run `JxlForge Converter.exe --selftest` from the command line to verify i18n resources and the end-to-end conversion pipeline (transcoding test cases run only when libjxl is on PATH; otherwise they are marked SKIP).
