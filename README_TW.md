# JxlForge Converter

![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg) ![Platform](https://img.shields.io/badge/platform-Windows-blue) ![Python](https://img.shields.io/badge/python-3.10%2B-blue)

一個基於 **PySide6** 的 [libjxl](https://github.com/libjxl/libjxl) GUI，把 JPEG XL 的編碼 / 解碼做成可視化的桌面操作工具。

[簡體中文](README.md) | **繁體中文** | [English](README_EN.md)

> 本專案全部程式碼均以自然語言形式在 AI 輔助下完成。

> **平台**：目前僅提供 **Windows** 的打包與測試。程式碼層面基於跨平台的 PySide6 與 cjxl/djxl，理論可在 macOS / Linux 執行，但未經打包與驗證；相關平台請自行安裝依賴後執行 `python main.py`。

---

## 功能特性

- **支援輸入格式（編碼為 JXL）**：PNG / APNG / GIF / JPEG / JPE / JFIF / EXR / PPM / PGM / PFM / PAM / PGX / PBM / JXL / WebP / AVIF / BMP / TIFF / ICO / HEIC / HEIF
  - WebP / AVIF / BMP / TIFF / ICO / HEIC / HEIF 經 Pillow 中轉解碼為暫時 PNG 後交給 cjxl，保留 ICC 配置（HEIC/HEIF 需先裝 pi-heif 外掛，見依賴）
  - 其餘格式由 cjxl 原生直轉
  - EXR 為浮點 HDR，僅解析頭部後設資料、不渲染像素縮圖
- **編碼（→ JXL）**
  - 有損（距離 distance）/ 無損（含 JPEG 無損重編碼 `--lossless_jpeg=1`）
  - 壓縮等級 `effort` 1–10、更快解碼等級 `--faster_decoding` 0–4 等進階參數
  - 支援自訂命令
- **解碼（JXL → PNG/PNM）** 與基本資訊檢視（jxlinfo）
- **動作操作鏈**：上移 / 下移 / 移除、單動作雙行版面、批次轉換
- **雙佇列並行排程**：按像素數把任務分為「大圖 / 小圖」——超大圖獨佔全部核心逐個處理，其餘小圖並行利用剩餘核心，整批轉換不空轉、互不拖慢；判定閾值首次啟動自動校正，無需手動設定。
- **保留來源檔案時間戳**：修改時間 + 建立時間
- **五標籤頁設計**：輸入 / 動作 / 輸出 / 狀態 / 設定；底部常駐 轉換 / 停止 / 關閉
- **語言**：簡體中文 / 繁體中文 / English（重啟生效）
- **主題**：跟隨系統 / 淺色 / 深色
- **控制項樣式**：原生及其變體 / Fusion
- **設定持久化**：視窗位置、輸出目錄、檔名範本、自訂歷史、主題等設定存於 QSettings（ini）

---

## ⚠️ 依賴：需自行安裝 libjxl 命令列工具

本程式**不附帶 cjxl / djxl / jxlinfo 引擎**，由你自行選擇 libjxl 版本：

1. 從 [libjxl 官方發布頁](https://github.com/libjxl/libjxl) 安裝 libjxl（建議 **v0.12.0**，本版在此環境下測試通過）。
2. 把 `cjxl.exe` / `djxl.exe` / `jxlinfo.exe` 所在目錄加入系統 **PATH**。
3. 重啟本程式。設定頁會顯示 cjxl/djxl「已找到」；缺失時轉換前會彈窗提示、預覽 JXL 時也會引導。

> 這樣設計是為了讓你自由選用任意 libjxl 版本，不受 GUI 打包版本束縛。

---

## 下載與執行（發布版）

1. 前往 [Releases 最新版](../../releases/latest)，下載 `JxlForge-Converter_v<版本號>_win64.zip` 並解壓到任意目錄。
2. 裝好 libjxl 並加入 PATH。
3. 執行 `JxlForge Converter\JxlForge Converter.exe`。

- 發布包未做程式碼簽名，首次執行 Windows SmartScreen 可能提示「Windows 已保護你的電腦」，點擊「仍要執行」即可；部分防毒軟體對 PyInstaller 打包的程式可能誤報，屬正常現象。

---

## 目錄結構

```
JxlForge-Converter/
├── main.py              # 開發態啟動入口（import jxlforge.__main__:run）
├── run.bat             # 開發態一鍵啟動（pythonw 無控制台）
├── run.pyw             # 無控制台啟動入口（Python Launcher 載入）
├── setup.py            # 環境與 libjxl 檢測 / 安裝腳本
├── install_deps.sh     # 跨平台（macOS / Linux / WSL）依賴安裝腳本
├── requirements.txt    # Python 依賴清單
├── packaging/          # 打包與發布設定
│   ├── JxlForge Converter.spec   # PyInstaller 打包規格（Tier2 精簡版，隨包帶 LICENSE 與 i18n 字典）
│   ├── build_dist.bat           # 一鍵打包 + 自檢門禁（英文輸出，建置過程即時串流輸出）
│   ├── build_dist_zh.bat/.py    # 同上，中文輸出版
│   ├── make_release.bat/.py     # dist → 發布壓縮檔 ZIP + 7Z（英文輸出）
│   ├── make_release_zh.bat/.py  # 同上，中文輸出版
│   └── _launch_app.py           # 生產打包入口（呼叫 run()）
├── jxlforge/           # 主程式套件
│   ├── __main__.py     # 入口 run()、--selftest 自檢
│   ├── main_window.py  # 主視窗與全部互動邏輯
│   ├── converter.py    # cjxl / djxl / jxlinfo 呼叫與參數建構
│   ├── processor.py    # 轉換任務處理
│   ├── calibrate.py    # 功耗校正
│   ├── power.py        # 電源狀態
│   ├── formats.py      # 格式解碼實作（PFM/PAM/PGX/PBM → PPM 等中轉解碼）
│   ├── combo_switch.py / no_flicker_combo.py  # 自繪無閃爍下拉控制項
│   ├── i18n.py         # 國際化載入
│   └── i18n/           # 翻譯字典（en_US.json / zh_TW.json）
├── tools/              # 測試、探針與腳本
│   ├── run_tests.py    # 回歸測試入口
│   ├── test_*.py       # headless 回歸測試
│   ├── probe_*.py      # 行為 / 效能探針
│   ├── extract_i18n.py / classify_i18n.py / check_i18n_coverage.py / i18n_wrap_source.py / apply_i18n_batch.py  # i18n 工具鏈
│   └── test_packaged_e2e.py  # 打包產品端到端自檢驅動
├── docs/
│   ├── design-decisions.md   # 設計決策紀錄
│   └── i18n-workflow.md      # i18n 工作流程
├── LICENSE             # GPL v3 全文
└── README_TW.md
```

---

## 授權條款

**GPL v3**（詳見 [LICENSE](LICENSE)）。

---

## 依賴

- [libjxl](https://github.com/libjxl/libjxl)（cjxl/djxl/jxlinfo，執行時由使用者自備，BSD-3-Clause）
- [PySide6](https://doc.qt.io/qtforpython/)（Qt 6 綁定，LGPL v3）
- [Pillow](https://python-pillow.org/)（MIT）
- [pi-heif](https://github.com/bigcat88/pillow_heif/tree/master/pi-heif)（**可選，但強烈推薦**：用於支援 HEIC/HEIF 輸入；未裝僅該格式不可用。pillow-heif 的解碼專用精簡版，BSD-3-Clause）
- Python 3.12（PSF License）

---

## 已知限制

- 未自帶 libjxl 引擎（設計如此，見上文）。
- 未做程式碼簽名。
- 暫不支援 JPEG XR（.jxr / HD Photo）：Pillow 目前未編譯對應解碼器，需引入額外依賴且收益低，暫不納入。
- 多頁 TIFF 僅轉換第 0 頁（其餘頁被忽略）。
- 本人首個專案的首次發布版本，歡迎在 Issues 回饋問題。

---

## 致謝

- 介面佈局與設計思路部分參考了 [XnConvert](https://www.xnconvert.com/)，特此致謝；本專案與 XnConvert 無任何隸屬或背書關係。
- 本程式為 [libjxl](https://github.com/libjxl/libjxl) 的圖形封裝，核心編解碼能力來自 libjxl 專案。

---

## 貢獻

歡迎提 Issue / PR。開發態請保持 `tools/` 下 headless 回歸測試綠燈（PySide6 offscreen 平台執行）。

### 面向開發者（從原始碼建置）

```bat
git clone <your-repo-url> JxlForge-Converter
cd JxlForge-Converter
pip install -r requirements.txt
python main.py          REM 或雙擊 run.bat/run.pyw（推薦）無控制台啟動
```

打包發布版（英文 / 中文輸出二選一）：

```bat
packaging\build_dist.bat        REM 英文輸出
packaging\build_dist_zh.bat     REM 中文輸出
```

> 打包產物輸出到倉庫外、與倉庫**同級**的 `JxlForge-Build/`（具體位置由 `packaging/build_dist.bat` 按倉庫實際所在磁碟/路徑自動推算，例如倉庫在 `D:\x\JxlForge-Converter` 則落到 `D:\x\JxlForge-Build`），不進 git。Python 解釋器同樣自動解析（`E:\Python\Python312\python.exe` → `py` → PATH 上的 `python`），無需固定磁碟。

它會：① 安全移開舊 dist（避免觸發安全守衛）② 用 spec 打包（已把 `jxlforge/i18n/*.json` 與 LICENSE 作為資料帶進包）③ 跑 `exe --selftest` 自檢，漏打資源會紅字報警。

打包成發布壓縮檔（須先完成上一步）：

```bat
packaging\make_release.bat      REM 英文輸出
packaging\make_release_zh.bat   REM 中文輸出
```

輸出到 `JxlForge-Build\release\`，檔名自動帶版本號 `JxlForge-Converter_v<版本>_win64.zip` / `.7z`（版本號從 `jxlforge/__init__.py` 讀取，不用手改）。打包前有門禁：dist 或 exe 缺失即中止，`selftest_report.txt` 非 PASS 會警告。

發布包內建 `selftest` 自檢：命令列 `JxlForge Converter.exe --selftest` 可驗證 i18n 資源與端到端轉碼管線（僅當 libjxl 在 PATH 時執行轉碼用例，缺失則標記 SKIP）。
