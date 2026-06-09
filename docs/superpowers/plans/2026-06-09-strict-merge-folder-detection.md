# Strict Merge Folder Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent date-prefixed source folders such as `06-03-HYX-NP图片项链-6单-13个` from being skipped as generated merge folders, while preserving `1~3-0603-...` as the only supported range-style generated folder format.

**Architecture:** Keep the fix inside the existing scan and detection helpers in `file_helper.py`. Tighten generated-folder regexes so hyphen ranges are not accepted, then remove the date pre-processing that truncates hyphenated dates. Protect both changes with focused unit tests before validating the real target directory in dry-run mode.

**Tech Stack:** Python 3, standard-library `re`, `unittest`, PowerShell.

---

## File Structure

- Modify `file_helper.py`: generated merge-folder patterns and outer-name date detection.
- Modify `tests/test_file_helper_core.py`: focused regression tests for generated-folder and date behavior.
- Read only `config.yaml`: use the current real category configuration during final dry-run verification.
- Read/write runtime audit only `C:\Users\kt\Desktop\0603 - 副本\rename_log.csv`: final dry-run appends preview rows but does not move or rename folders.

### Task 1: Lock Down Generated Merge-Folder Syntax

**Files:**
- Modify: `tests/test_file_helper_core.py`
- Modify: `file_helper.py:42-45`

- [ ] **Step 1: Add failing generated-folder regression tests**

Add these methods to `FileHelperCliCoreTests` in `tests/test_file_helper_core.py`:

```python
    def test_hyphenated_date_source_is_not_generated_merge_folder(self):
        self.assertFalse(
            file_helper.is_generated_merge_folder_name(
                "06-03-HYX-NP图片项链-6单-13个"
            )
        )

    def test_tilde_ranges_are_generated_merge_folders(self):
        self.assertTrue(
            file_helper.is_generated_merge_folder_name(
                "1~3-0603-NP图片项链-10单-20个"
            )
        )
        self.assertTrue(
            file_helper.is_generated_merge_folder_name(
                "1～3-0603-NP图片项链-10单-20个"
            )
        )

    def test_hyphen_range_is_not_generated_merge_folder(self):
        self.assertFalse(
            file_helper.is_generated_merge_folder_name(
                "1-3-0603-NP图片项链-10单-20个"
            )
        )
```

- [ ] **Step 2: Run the focused tests and verify the old behavior fails**

Run:

```powershell
python -m unittest `
  tests.test_file_helper_core.FileHelperCliCoreTests.test_hyphenated_date_source_is_not_generated_merge_folder `
  tests.test_file_helper_core.FileHelperCliCoreTests.test_tilde_ranges_are_generated_merge_folders `
  tests.test_file_helper_core.FileHelperCliCoreTests.test_hyphen_range_is_not_generated_merge_folder `
  -v
```

Expected:

- `test_hyphenated_date_source_is_not_generated_merge_folder` fails because the current regex treats `06-03` as a sequence range.
- `test_hyphen_range_is_not_generated_merge_folder` fails because the current regex accepts `1-3`.
- The two tilde assertions pass.

- [ ] **Step 3: Remove hyphen range support from generated-folder regexes**

Replace `GENERATED_MERGE_FOLDER_PATTERNS` in `file_helper.py` with:

```python
GENERATED_MERGE_FOLDER_PATTERNS = [
    re.compile(r"^\d+(?:[~～]\d+|\+\d+(?:\+\d+)*)-(?:\d{4}|未知日期|\d{4}-\d{4})-.+-\d+单-\d+个$"),
    re.compile(r"^\d+(?:[~～]\d+|\+\d+(?:\+\d+)*)-.+-\d+单-\d+个$"),
]
```

Do not modify `config.yaml` or broaden `already_processed.patterns`.

- [ ] **Step 4: Run the focused generated-folder tests**

Run the command from Step 2 again.

Expected: all three tests pass.

- [ ] **Step 5: Commit the generated-folder fix**

Before committing, inspect:

```powershell
git diff -- file_helper.py tests/test_file_helper_core.py
```

Stage only the two task files:

```powershell
git add file_helper.py tests/test_file_helper_core.py
git commit -m "fix: require tilde for merge folder ranges"
```

Do not stage the user's existing GUI, README, version, or package-lock changes.

### Task 2: Preserve Hyphenated Dates During Detection

**Files:**
- Modify: `tests/test_file_helper_core.py`
- Modify: `file_helper.py:750-770`

- [ ] **Step 1: Add failing date-detection regression tests**

Add these methods to `FileHelperCliCoreTests`:

```python
    def test_hyphenated_date_at_start_is_detected(self):
        dates, label, sources = file_helper.detect_dates(
            "06-03-HYX-NP图片项链-6单-13个",
            "未知日期",
        )

        self.assertEqual(dates, ["0603"])
        self.assertEqual(label, "0603")
        self.assertEqual(sources, ["outer_folder_name_only"])

    def test_sequence_prefix_does_not_hide_compact_date(self):
        dates, label, sources = file_helper.detect_dates(
            "10-0603-产品名-1单-1个",
            "未知日期",
        )

        self.assertEqual(dates, ["0603"])
        self.assertEqual(label, "0603")
        self.assertEqual(sources, ["outer_folder_name_only"])
```

- [ ] **Step 2: Run the focused date tests and verify the first test fails**

Run:

```powershell
python -m unittest `
  tests.test_file_helper_core.FileHelperCliCoreTests.test_hyphenated_date_at_start_is_detected `
  tests.test_file_helper_core.FileHelperCliCoreTests.test_sequence_prefix_does_not_hide_compact_date `
  -v
```

Expected:

- `test_hyphenated_date_at_start_is_detected` fails because current pre-processing removes `06-`.
- `test_sequence_prefix_does_not_hide_compact_date` passes or continues to pass.

- [ ] **Step 3: Detect dates from the complete outer folder name**

In `detect_dates()` remove:

```python
search_text = re.sub(r"^\d{1,3}\s*[-_]\s*", "", original_name, count=1)
```

Change the pattern loop from:

```python
for pattern in patterns:
    for match in pattern.finditer(search_text):
```

to:

```python
for pattern in patterns:
    for match in pattern.finditer(original_name):
```

Do not change the supported date patterns or normalization behavior.

- [ ] **Step 4: Run the focused date tests**

Run the command from Step 2 again.

Expected: both tests pass.

- [ ] **Step 5: Commit the date fix**

Inspect and stage only the relevant files:

```powershell
git diff -- file_helper.py tests/test_file_helper_core.py
git add file_helper.py tests/test_file_helper_core.py
git commit -m "fix: preserve hyphenated dates during detection"
```

### Task 3: Run Full Local Verification

**Files:**
- Verify: `file_helper.py`
- Verify: `launcher_gui.py`
- Verify: `launcher_core.py`
- Verify: `tests/test_file_helper_core.py`
- Verify: `tests/test_launcher_core.py`

- [ ] **Step 1: Run syntax checks**

Run:

```powershell
python -m py_compile file_helper.py launcher_gui.py launcher_core.py
```

Expected: exit code `0` with no output.

- [ ] **Step 2: Run both test modules**

Run:

```powershell
python -m unittest tests/test_file_helper_core.py tests/test_launcher_core.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Run config validation**

Run:

```powershell
python file_helper.py check-config --config config.yaml
```

Expected output includes:

```text
配置检查通过
```

- [ ] **Step 4: Check representative names through the public diagnostic command**

Run:

```powershell
python file_helper.py test-name "06-03-HYX-NP图片项链-6单-13个" --config config.yaml
python file_helper.py test-name "06-03-CSY-银翅膀图片项链-26单-33个" --config config.yaml
```

Expected:

- First command reports date `0603`, category `NP图片项链`, quantity `6单13个`, and merge allowed.
- Second command reports date `0603`, category `翅膀图片项链`, quantity `26单33个`, and merge allowed.

### Task 4: Verify the Real Target in Dry-Run Mode

**Files:**
- Read: `C:\Users\kt\Desktop\0603 - 副本\`
- Runtime append: `C:\Users\kt\Desktop\0603 - 副本\rename_log.csv`

- [ ] **Step 1: Confirm the two source folders still exist at the top level**

Run:

```powershell
Get-ChildItem -LiteralPath "C:\Users\kt\Desktop\0603 - 副本" -Directory |
    Where-Object {
        $_.Name -in @(
            "06-03-HYX-NP图片项链-6单-13个",
            "06-03-CSY-银翅膀图片项链-26单-33个"
        )
    } |
    Select-Object -ExpandProperty Name
```

Expected: both names are printed.

- [ ] **Step 2: Run dry-run only**

Run:

```powershell
python file_helper.py `
  --root "C:\Users\kt\Desktop\0603 - 副本" `
  --config config.yaml `
  --dry-run
```

Expected:

- No file moves, renames, merges, extraction, compression, or deletion.
- `06-03-HYX-NP图片项链-6单-13个` is not listed under skipped items.
- `06-03-CSY-银翅膀图片项链-26单-33个` is not listed under skipped items.
- HYX joins the `NP图片项链` merge group.
- CSY joins the `翅膀图片项链` merge group.
- Both report date `0603`.

- [ ] **Step 3: Confirm no apply run was added**

Record the latest run count before and after dry-run:

```powershell
$log = Get-Content -LiteralPath "C:\Users\kt\Desktop\0603 - 副本\organizer_run_log.json" -Raw -Encoding UTF8 |
    ConvertFrom-Json
$log.runs.Count
```

Expected: the count is unchanged because dry-run does not create an apply run.

- [ ] **Step 4: Inspect final diff and working-tree scope**

Run:

```powershell
git diff -- file_helper.py tests/test_file_helper_core.py
git status --short
```

Expected:

- The implementation diff is limited to generated-folder patterns, date detection, and focused tests.
- Existing user changes in `README.md`, `VERSION.txt`, `launcher_core.py`, `launcher_gui.py`, `tests/test_launcher_core.py`, and `package-lock.json` remain untouched.
- No `--apply` command has been run.

## Out of Scope

- Do not add `四图热转印钥匙扣` keywords in this implementation.
- Do not change GUI behavior or rebuild the EXE.
- Do not execute real organization on `C:\Users\kt\Desktop\0603 - 副本`.
- Do not modify confirmation, undo, archive, overwrite, or deletion behavior.
