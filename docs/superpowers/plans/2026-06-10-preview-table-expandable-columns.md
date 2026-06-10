# Preview Table Expandable Columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every scan-preview table column a stable default width and let users click data cells to independently expand or collapse columns based on the longest visible value, capped at 600 pixels.

**Architecture:** Put deterministic width calculation and expanded-column state transitions in `launcher_core.py` so they can be tested without opening a GUI. Keep Treeview hit testing, font measurement, column updates, and scan-time reset in `launcher_gui.py`. Do not alter preview generation or organizer business logic.

**Tech Stack:** Python 3, Tkinter/ttk Treeview, CustomTkinter, `tkinter.font`, `unittest`, PyInstaller.

---

## File Structure

- Modify `launcher_core.py`: define preview column widths, maximum width, padding, and pure width/state helpers.
- Modify `launcher_gui.py`: render all columns with fixed widths, handle cell clicks, measure text, toggle independent expanded columns, and reset widths before each scan.
- Modify `tests/test_launcher_core.py`: unit tests for width limits and independent expanded-column state.
- Modify `README.md`: document the click-to-expand table interaction.
- Verify `file_helper.py`: no behavior changes; syntax and full tests only.
- Rebuild `dist\Windows文件整理助手\` and sync `Windows文件整理助手-v2.3\`: packaged GUI must match source.

### Task 1: Add Testable Column Width Rules

**Files:**
- Modify: `tests/test_launcher_core.py`
- Modify: `launcher_core.py`

- [ ] **Step 1: Add failing tests for fixed widths and expanded width calculation**

Import the new symbols in `tests/test_launcher_core.py`:

```python
from launcher_core import (
    PREVIEW_COLUMN_WIDTHS,
    PREVIEW_COLUMN_MAX_WIDTH,
    preview_expanded_width,
)
```

Add:

```python
    def test_preview_column_widths_match_confirmed_defaults(self):
        self.assertEqual(
            PREVIEW_COLUMN_WIDTHS,
            {
                "序号": 48,
                "原文件夹": 210,
                "识别日期": 82,
                "识别品类": 118,
                "命中关键词": 130,
                "单量": 60,
                "数量": 60,
                "动作": 72,
                "目标名称": 220,
                "状态": 78,
                "原因": 240,
            },
        )

    def test_preview_expanded_width_keeps_default_for_short_text(self):
        self.assertEqual(preview_expanded_width(120, [40, 80]), 120)

    def test_preview_expanded_width_adds_padding_for_long_text(self):
        self.assertEqual(preview_expanded_width(120, [80, 300]), 324)

    def test_preview_expanded_width_caps_at_600_pixels(self):
        self.assertEqual(preview_expanded_width(120, [700]), PREVIEW_COLUMN_MAX_WIDTH)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest `
  tests.test_launcher_core.LauncherCoreTests.test_preview_column_widths_match_confirmed_defaults `
  tests.test_launcher_core.LauncherCoreTests.test_preview_expanded_width_keeps_default_for_short_text `
  tests.test_launcher_core.LauncherCoreTests.test_preview_expanded_width_adds_padding_for_long_text `
  tests.test_launcher_core.LauncherCoreTests.test_preview_expanded_width_caps_at_600_pixels `
  -v
```

Expected: import errors because the constants and helper do not exist.

- [ ] **Step 3: Implement the width constants and pure helper**

Add to `launcher_core.py`:

```python
PREVIEW_COLUMN_WIDTHS: dict[str, int] = {
    "序号": 48,
    "原文件夹": 210,
    "识别日期": 82,
    "识别品类": 118,
    "命中关键词": 130,
    "单量": 60,
    "数量": 60,
    "动作": 72,
    "目标名称": 220,
    "状态": 78,
    "原因": 240,
}
PREVIEW_COLUMN_MAX_WIDTH = 600
PREVIEW_COLUMN_PADDING = 24


def preview_expanded_width(default_width: int, measured_widths: list[int]) -> int:
    longest = max(measured_widths, default=0)
    return min(
        PREVIEW_COLUMN_MAX_WIDTH,
        max(default_width, longest + PREVIEW_COLUMN_PADDING),
    )
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 command again.

Expected: all four tests pass.

- [ ] **Step 5: Commit the pure width rules**

```powershell
git add launcher_core.py tests/test_launcher_core.py
git commit -m "test: define preview table column width rules"
```

### Task 2: Implement Independent Column Expansion

**Files:**
- Modify: `tests/test_launcher_core.py`
- Modify: `launcher_core.py`
- Modify: `launcher_gui.py`

- [ ] **Step 1: Add failing tests for independent toggle state**

Import:

```python
from launcher_core import toggle_preview_column
```

Add:

```python
    def test_toggle_preview_column_allows_multiple_expanded_columns(self):
        expanded: set[str] = set()
        self.assertEqual(toggle_preview_column(expanded, "原因"), {"原因"})
        self.assertEqual(
            toggle_preview_column({"原因"}, "原文件夹"),
            {"原因", "原文件夹"},
        )

    def test_toggle_preview_column_collapses_only_clicked_column(self):
        self.assertEqual(
            toggle_preview_column({"原因", "原文件夹"}, "原因"),
            {"原文件夹"},
        )
```

- [ ] **Step 2: Run the toggle tests and verify RED**

Run:

```powershell
python -m unittest `
  tests.test_launcher_core.LauncherCoreTests.test_toggle_preview_column_allows_multiple_expanded_columns `
  tests.test_launcher_core.LauncherCoreTests.test_toggle_preview_column_collapses_only_clicked_column `
  -v
```

Expected: import error because `toggle_preview_column` does not exist.

- [ ] **Step 3: Implement immutable toggle behavior**

Add to `launcher_core.py`:

```python
def toggle_preview_column(expanded_columns: set[str], column: str) -> set[str]:
    updated = set(expanded_columns)
    if column in updated:
        updated.remove(column)
    else:
        updated.add(column)
    return updated
```

- [ ] **Step 4: Run the toggle tests and verify GREEN**

Run the Step 2 command again.

Expected: both tests pass.

- [ ] **Step 5: Configure the GUI state and fixed default widths**

In `launcher_gui.py`, import:

```python
import tkinter.font as tkfont

from launcher_core import (
    PREVIEW_COLUMN_WIDTHS,
    preview_expanded_width,
    toggle_preview_column,
)
```

In `LauncherGui.__init__`, add:

```python
self.preview_expanded_columns: set[str] = set()
```

Replace the local `widths` dictionary with `PREVIEW_COLUMN_WIDTHS` and configure every column with:

```python
self.preview_table.column(
    column,
    width=PREVIEW_COLUMN_WIDTHS[column],
    minwidth=45,
    stretch=False,
)
```

Bind the table click:

```python
self.preview_table.bind("<Button-1>", self.on_preview_table_click, add="+")
```

- [ ] **Step 6: Implement cell-only click handling and width measurement**

Add these methods to `LauncherGui`:

```python
def on_preview_table_click(self, event: tk.Event) -> None:
    if self.preview_table is None:
        return
    if self.preview_table.identify_region(event.x, event.y) != "cell":
        return
    column_id = self.preview_table.identify_column(event.x)
    if not column_id:
        return
    try:
        column_index = int(column_id.removeprefix("#")) - 1
        column = str(self.preview_table["columns"][column_index])
    except (ValueError, IndexError):
        return
    self.toggle_preview_column_width(column)


def preview_column_measured_widths(self, column: str) -> list[int]:
    if self.preview_table is None:
        return []
    font_name = str(ttk.Style(self.root).lookup("Treeview", "font") or "TkDefaultFont")
    font = tkfont.nametofont(font_name)
    column_index = list(self.preview_table["columns"]).index(column)
    texts = [column]
    for item_id in self.preview_table.get_children():
        values = self.preview_table.item(item_id, "values")
        if column_index < len(values):
            texts.append(str(values[column_index]))
    return [font.measure(text) for text in texts]


def toggle_preview_column_width(self, column: str) -> None:
    if self.preview_table is None or column not in PREVIEW_COLUMN_WIDTHS:
        return
    self.preview_expanded_columns = toggle_preview_column(
        self.preview_expanded_columns,
        column,
    )
    if column in self.preview_expanded_columns:
        width = preview_expanded_width(
            PREVIEW_COLUMN_WIDTHS[column],
            self.preview_column_measured_widths(column),
        )
    else:
        width = PREVIEW_COLUMN_WIDTHS[column]
    self.preview_table.column(column, width=width, stretch=False)
```

If the platform returns a font configuration that `nametofont()` cannot resolve, use `tkfont.Font(font=...)` as a narrowly scoped fallback; do not estimate width from character count.

- [ ] **Step 7: Reset all columns before each scan**

Add:

```python
def reset_preview_column_widths(self) -> None:
    self.preview_expanded_columns.clear()
    if self.preview_table is None:
        return
    for column, width in PREVIEW_COLUMN_WIDTHS.items():
        self.preview_table.column(column, width=width, stretch=False)
```

At the start of `clear_preview_table()` call:

```python
self.reset_preview_column_widths()
```

This ensures every new scan starts collapsed even when the prior scan had several expanded columns.

- [ ] **Step 8: Run launcher tests and syntax checks**

Run:

```powershell
python -m py_compile launcher_gui.py launcher_core.py file_helper.py
python -m unittest tests/test_launcher_core.py -v
```

Expected: syntax succeeds and all launcher tests pass.

- [ ] **Step 9: Commit the GUI interaction**

```powershell
git add launcher_gui.py launcher_core.py tests/test_launcher_core.py
git commit -m "feat: add expandable preview table columns"
```

### Task 3: Document and Verify the User Interaction

**Files:**
- Modify: `README.md`
- Verify: `launcher_gui.py`
- Verify: `launcher_core.py`
- Verify: `file_helper.py`
- Verify: `tests/test_launcher_core.py`
- Verify: `tests/test_file_helper_core.py`

- [ ] **Step 1: Update the Chinese GUI documentation**

Add to the `v2.3 GUI 增强` section in `README.md`:

```markdown
- 扫描预览表格默认使用固定列宽。点击任意数据单元格会按该列所有行中的最长内容展开该列，最大宽度为 600px；再次点击该列会恢复默认宽度。多列可以同时展开，超出窗口的部分通过表格底部横向滚动条查看。重新扫描会恢复所有默认列宽。
```

- [ ] **Step 2: Run the full test suite**

Run:

```powershell
python -m py_compile file_helper.py launcher_gui.py launcher_core.py
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 3: Launch the source GUI for manual verification**

Run:

```powershell
py launcher_gui.py
```

Verify:

1. Initial widths match the confirmed table.
2. No column stretches automatically.
3. Clicking a data cell expands only its column using the longest content.
4. Expanded width never exceeds 600px.
5. Multiple columns remain expanded.
6. Clicking one expanded column collapses only that column.
7. Clicking a heading, separator, or blank region does nothing.
8. Horizontal scrolling remains functional.
9. Running scan preview again resets all columns.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md
git commit -m "docs: explain expandable preview columns"
```

### Task 4: Rebuild and Sync the Foldered EXE

**Files:**
- Build: `dist\Windows文件整理助手\`
- Sync: `Windows文件整理助手-v2.3\`
- Verify adjacent: `file_helper.py`, `config.yaml`, `README.md`, `VERSION.txt`

- [ ] **Step 1: Confirm packaging dependencies and stop a running release process**

Run:

```powershell
py -m PyInstaller --version
Get-Process | Where-Object { $_.ProcessName -eq "Windows文件整理助手" }
```

If the release EXE is running, stop that exact process before copying:

```powershell
Get-Process -Name "Windows文件整理助手" | Stop-Process
```

- [ ] **Step 2: Rebuild the foldered launcher**

Run:

```powershell
py -m PyInstaller `
  --noconfirm `
  --onedir `
  --windowed `
  --collect-all customtkinter `
  --name "Windows文件整理助手" `
  launcher_gui.py
```

Expected:

```text
dist\Windows文件整理助手\Windows文件整理助手.exe
dist\Windows文件整理助手\_internal\
```

- [ ] **Step 3: Sync editable companion files into dist**

Run:

```powershell
Copy-Item -Force file_helper.py "dist\Windows文件整理助手\file_helper.py"
Copy-Item -Force config.yaml "dist\Windows文件整理助手\config.yaml"
Copy-Item -Force README.md "dist\Windows文件整理助手\README.md"
Copy-Item -Force VERSION.txt "dist\Windows文件整理助手\VERSION.txt"
```

- [ ] **Step 4: Sync the rebuilt dist tree into the v2.3 release folder**

Before copying, verify the resolved source and target:

```powershell
$source = (Resolve-Path "dist\Windows文件整理助手").Path
$target = (Resolve-Path "Windows文件整理助手-v2.3").Path
$source
$target
```

Copy the rebuilt contents:

```powershell
Copy-Item -Path "dist\Windows文件整理助手\*" `
  -Destination "Windows文件整理助手-v2.3" `
  -Recurse `
  -Force
```

- [ ] **Step 5: Verify release contents and source sync**

Run:

```powershell
Get-ChildItem "Windows文件整理助手-v2.3"
python tools/check_release_sync.py --release ".\Windows文件整理助手-v2.3"
```

Expected:

- `Windows文件整理助手.exe` exists.
- `_internal\` exists.
- `file_helper.py`, `config.yaml`, `README.md`, and `VERSION.txt` exist.
- Release sync check returns exit code `0`.

- [ ] **Step 6: Smoke-test the packaged launcher**

Run:

```powershell
Start-Process -FilePath ".\Windows文件整理助手-v2.3\Windows文件整理助手.exe"
```

Verify the packaged GUI starts and the same column expand/collapse behavior works.

- [ ] **Step 7: Confirm Git scope**

Run:

```powershell
git status --short
```

Expected:

- Source and documentation changes are committed.
- `build\`, `dist\`, and `Windows文件整理助手-v2.3\` remain outside normal source staging.
- Existing untracked `package-lock.json` remains untouched.
