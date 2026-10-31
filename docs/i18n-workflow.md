# i18n 工作流

面向「以后还要加语言 / 加功能」的维护说明。目标是把翻译成本从
「攒几个月做一次大批量」摊薄成「每次改完顺手补 1-2 条」。

---

## 一、三条命令

| 想做什么 | 命令 |
|---|---|
| 还差多少没翻？先翻哪条？ | `python tools/check_i18n_coverage.py --top 20` |
| 改动后有没有弄坏东西？ | `python tools/run_tests.py` |
| 只跑 i18n 相关 | `python tools/run_tests.py i18n` |

`check_i18n_coverage.py` 会**自动重跑抽取与分级**再统计，不会因为源码
行号变了而给出过期结论。想快一点查字典可加 `--no-refresh`。

CI / 提交前当闸门用：

```
python tools/check_i18n_coverage.py --min 80    # 低于 80% 退出码 1
```

---

## 二、加一门新语言：一步

往 `jxlforge/i18n/` 丢一个 `<代码>.json`：

```json
{
  "_language_name": "日本語",
  "调整大小": "リサイズ",
  "旋转": "回転"
}
```

就这些。语言清单、下拉项、下拉显示名、覆盖率统计全部从目录自动派生，
**不需要改任何 .py**。契约由 `tools/test_i18n_add_language.py` 锁住。

要点：

- `_language_name` 是**自称名**（endonym）。英文界面上「日本語」仍显示
  「日本語」，译成 "Japanese" 反而让人认不出。
- 下划线开头的键是元信息，不参与翻译，也不会污染覆盖率。
- **中文不建字典**。源码里写的就是中文，查不到 `t()` 直接返回原文，
  所以 zh_CN 不需要维护一份「自己翻自己」的字典，也就不会跟源码漂移。
- 缺译文不会崩，只是那条退回中文。覆盖率 30% 也能正常用英文界面。

---

## 三、日常加功能时：顺手补

```
写完新功能（中文文案照写）
    ↓
python tools/check_i18n_coverage.py --top 20
    ↓
照着「最该先翻的 N 条」补进 i18n/*.json（按出现次数排过序）
    ↓
python tools/run_tests.py
```

**不要攒。** 一次补 2 条和一次补 422 条，总工作量差不多，
但前者的心智负担和出错率都低一个量级。

---

## 四、改了源码之后

⚠️ **改完 `jxlforge/*.py` 必须重跑抽取**：

```
python tools/check_i18n_coverage.py     # 它会自动重跑，不用手动
```

原因：阶段 0 的清单 `i18n_work/strings_raw.json` 存的是**行号**。
源码一行不动地平移几行，结构分析就会**静默失准**——不报错，
只是悄悄判错。症状是分级测试报「**判错**」（报「缺失」才是源码真删了）。

---

## 五、两条判定铁律

### 1. 看数据流，不看语法

判「该不该翻」的唯一标准是：**这串文字最终显示给人看，还是被程序当标识符用**。

- 显示给人看 → 翻
- 当标识符用（进 QSettings、参与 `==` 比较、当字典 key 查表）→ **不能裸翻**

后者翻译会让配置文件失配或功能失效。

### 2. 判「是不是 ID」看同源 + 持久化，不看「中文当没当 key」

`sub_order` / `sub_pos` 里中文也当 key，但它们是**纯运行时布局表**——
查坐标用 `sub_pos[sg]`、当标题用 `QGroupBox(sg)`，是**同一个变量**，
翻译后两边同步变化，查表不会断；而且不写 QSettings。→ 可翻。

反之，动作类型 / 水印位置 / 视图模式 / 冲突策略这类**持久化枚举**的中文旧值曾直接写进 ini → 不能裸翻。
0.4.0 起它们已改为英文 ID 落盘、中文只作显示名，见第六节。

---

## 六、持久化枚举：ID 与显示名分离（schema v2）

动作类型 / 水印位置 / 视图模式 / 冲突策略这四类枚举，原先用中文同时充当
**显示名与内部 ID**（被字面量比较 40+ 处，还会写进 ini）。
后果是：改一句文案，旧配置就失配；漏改一处分组表，窗口直接建不出来。

0.4.0 起改为双轨：

| 角色 | 取值 | 举例 | 用途 |
|---|---|---|---|
| ID | 英文稳定串 | `resize` / `bottom_right` / `details` / `rename` | 存配置、参与比较、查表 |
| 显示名 | 中文 | 调整大小 / 右下 / 详细信息 / 重命名 | 界面文本 + i18n 键 |

- **单一真源**是 `*_SPECS` 表，其余常量都从表派生：
  `processor.ACTION_SPECS`、`processor.WATERMARK_POSITION_SPECS`、
  `main_window.VIEW_MODE_SPECS`、`main_window.ON_EXIST_SPECS`。
- **Qt 写法**：下拉一律 `addItem(t(中文label), 英文id)`，取值用 `currentData()`；
  `QAction` 必须 `setData(英文id)`，否则 `data()` 返回 `None`。
- **中文仍是字典的键**，所以 `i18n/*.json` 写法不变；英文 ID 不进字典
  （由 `probe_i18n_leaks.py` 守 0 泄漏）。
- **迁移幂等**：`migrate_legacy_actions()` 须满足
  `migrate(migrate(x)) == migrate(x)`。
- **迁移表是冻结快照**：`LEGACY_ACTION_TYPE_MAP` / `LEGACY_WATERMARK_POSITION_MAP` /
  `LEGACY_VIEW_MODE_MAP` / `LEGACY_ON_EXIST_MAP` 只收 v1 真正落过盘的中文串，
  **永不追加新动作**；改显示名时**不要动它们**。
- 契约由 `tools/test_i18n_id_separation.py`（59 项）与
  `tools/test_on_exist_persist.py`（24 项）锁住。

⚠️ 两个容易踩的坑：

1. **方向单一**：只允许「ID → 显示名」呈现，不允许「显示名 → ID」持久化。
   反查只用于读取历史配置，写端永远写 ID。
2. **写端绝不写译文**：`currentData() or currentText()` 这种兜底会把当前语言的
   显示名写进 ini，制造读取端认不出的脏值。取不到 ID 就告警并写默认值；
   读取端认不出历史值时必须 `warn`，静默回退会表现为「设置自己变了」。

---

## 七、工具清单

| 文件 | 作用 |
|---|---|
| `tools/extract_i18n.py` | 阶段 0：AST 扫描，抽出所有中文字面量 + 坐标 |
| `tools/classify_i18n.py` | 阶段 1：自动分级（翻 / 不翻 + 置信度），生成复核页 |
| `tools/check_i18n_coverage.py` | **日常用这个**：覆盖率闸门 |
| `tools/run_tests.py` | 批量跑测试 |
| `jxlforge/i18n.py` | 运行时：`t()` / `set_language()` |
| `jxlforge/i18n/*.json` | 各语言字典 |
| `tools/i18n_work/review.html` | 分级复核页（可筛选 / 改判，理由写成人话） |

产物在 `tools/i18n_work/`：`strings_raw.json`（抽取）、
`strings_classified.json`（分级）、`coverage.json`（覆盖率，可跨时间比进度）。

---

## 八、已知边界

- **切语言要重启生效**。这是刻意的：实时刷新所有控件 + 重渲染已输出日志，
  两套机制都要给每个控件留句柄并缓存原文，复杂度和出错面都大得多。
- **英文比中文长 30–60%**。设置页「常规」区双列已卡临界，加新语言后要查裁切。
- **offscreen 平台在 Windows 下默认 0 个字体**，截图探针
  （`tools/probe_i18n_en_ui.py`）手动载入了系统字体，字体与真机不完全一致，
  **只能看文字内容，不能拿来验收排版**。
- 跨行隐式拼接的字符串（35 条）回填后会丢排版，需要人工处理。
