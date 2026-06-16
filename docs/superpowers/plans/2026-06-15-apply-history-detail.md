# Apply History Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only GUI page that shows the latest 100 `apply` runs for the currently selected root, with per-final-folder recognition details and source-item expansion.

**Architecture:** Extend each existing `organizer_run_log.json` run with a versioned `history_snapshot` while keeping `operations` as the undo source of truth. Build immutable history view models in `launcher_core.py`, then render them in a new `launcher_gui.py` page without duplicating organizer business rules. Prune complete apply runs at write time so history visibility and undo eligibility always expire together.

**Tech Stack:** Python 3, dataclasses, JSON, pathlib, unittest, CustomTkinter, Tkinter `ttk.Treeview`, PyInstaller onedir packaging.

---

## File Structure

- Modify `file_helper.py`: create and update history snapshots, persist result state with undo operations, and prune apply runs.
- Modify `launcher_core.py`: define immutable history view models and read/validate `organizer_run_log.json`.
- Modify `launcher_gui.py`: add the navigation entry, two-pane history page, record selection, and expandable result rows.
- Modify `tests/test_file_helper_core.py`: cover snapshot aggregation, result state, pruning, and undo eligibility.
- Modify `tests/test_launcher_core.py`: cover history parsing, compatibility, status mapping, empty state, and damaged logs.
- Modify `tests/test_launcher_gui_smoke.py`: cover page navigation, reload behavior, initial selection, and expanded details.
- Modify `README.md`: document the history page and 100-run retention/undo boundary.
- Modify `VERSION.txt`: bump the user-facing feature version and release metadata.
- Update the foldered release directory if present at implementation time; do not add release output to the source commit.

### Task 1: Define History Snapshot Aggregation

**Files:**
- Modify: `file_helper.py:140-203`
- Modify: `file_helper.py:547-625`
- Test: `tests/test_file_helper_core.py:94-240`

- [ ] **Step 1: Write failing tests for single and merged snapshots**

Add imports for the new helpers, then add:

```python
def test_build_history_snapshot_aggregates_final_groups(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mkdir(root / "0501 军牌钥匙扣 1单2个")
        mkdir(root / "0505 军牌钥匙扣 2单3个")
        _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")

        snapshot = build_history_snapshot(groups)

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(len(snapshot["results"]), 1)
        result = snapshot["results"][0]
        self.assertEqual(result["final_name"], groups[0].final_name)
        self.assertEqual(result["target_path"], str(groups[0].target_path.resolve()))
        self.assertEqual(result["date"], "0501-0505")
        self.assertEqual(result["category"], "军牌钥匙扣")
        self.assertEqual(result["orders"], 3)
        self.assertEqual(result["quantity"], 5)
        self.assertEqual(result["matched_keywords"], ["军牌钥匙扣"])
        self.assertTrue(result["merged"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["error_reason"], "")
        self.assertEqual(
            [item["original_name"] for item in result["source_items"]],
            ["0501 军牌钥匙扣 1单2个", "0505 军牌钥匙扣 2单3个"],
        )


def test_build_history_snapshot_marks_single_group_not_merged(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mkdir(root / "0507 军牌项链 1单2个")
        _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")

        result = build_history_snapshot(groups)["results"][0]

        self.assertFalse(result["merged"])
        self.assertEqual(result["orders"], 1)
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["source_items"][0]["source_type"], "folder")
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_file_helper_core.FileHelperCoreTests.test_build_history_snapshot_aggregates_final_groups tests.test_file_helper_core.FileHelperCoreTests.test_build_history_snapshot_marks_single_group_not_merged -v
```

Expected: both tests fail because `build_history_snapshot` is not defined.

- [ ] **Step 3: Implement the snapshot helpers**

Add constants and helpers near the run-log functions:

```python
HISTORY_SCHEMA_VERSION = 1
MAX_APPLY_RUNS = 100


def history_result_id(index: int) -> str:
    return f"result-{index + 1}"


def build_history_source_item(item: WorkItem) -> Dict[str, str]:
    source_path = item.archive_path if item.source_type == "archive" and item.archive_path else item.current_path
    return {
        "original_name": item.original_name,
        "source_type": item.source_type,
        "source_path": absolute_text(source_path),
    }


def build_history_snapshot(groups: List[PlanGroup]) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for index, group in enumerate(groups):
        keywords: List[str] = []
        for item in group.items:
            keyword = item.detection.matched_keyword
            if keyword and keyword not in keywords:
                keywords.append(keyword)
        results.append(
            {
                "result_id": history_result_id(index),
                "final_name": group.final_name,
                "target_path": absolute_text(group.target_path),
                "source_items": [build_history_source_item(item) for item in group.items],
                "merged": bool(group.is_merge and len(group.items) > 1),
                "date": group.date_label,
                "category": group.category,
                "orders": group.orders,
                "quantity": group.quantity,
                "matched_keywords": keywords,
                "status": "pending",
                "error_reason": "",
            }
        )
    return {"schema_version": HISTORY_SCHEMA_VERSION, "results": results}
```

- [ ] **Step 4: Run the snapshot tests**

Run the command from Step 2.

Expected: both tests pass.

- [ ] **Step 5: Commit the aggregation layer**

```powershell
git add file_helper.py tests/test_file_helper_core.py
git commit -m "feat: build apply history snapshots"
```

### Task 2: Persist Snapshot State and Prune Complete Runs

**Files:**
- Modify: `file_helper.py:547-625`
- Modify: `file_helper.py:1727-1830`
- Modify: `file_helper.py:2060-2105`
- Test: `tests/test_file_helper_core.py:180-340`

- [ ] **Step 1: Write failing tests for run creation, result updates, and pruning**

Add:

```python
def test_create_apply_run_persists_snapshot_and_mode(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mkdir(root / "0507 军牌项链 1单2个")
        _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")

        _data, run = create_apply_run(root, root / RUN_LOG_NAME, groups)

        self.assertEqual(run["mode"], "apply")
        self.assertEqual(run["history_snapshot"], build_history_snapshot(groups))


def test_update_history_result_persists_status_and_reason(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mkdir(root / "0507 军牌项链 1单2个")
        _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")
        data, run = create_apply_run(root, root / RUN_LOG_NAME, groups)

        update_history_result(
            root / RUN_LOG_NAME,
            data,
            run,
            "result-1",
            "skipped",
            "目标目录已存在。",
        )

        saved = load_run_log(root / RUN_LOG_NAME)["runs"][0]["history_snapshot"]["results"][0]
        self.assertEqual(saved["status"], "skipped")
        self.assertEqual(saved["error_reason"], "目标目录已存在。")


def test_run_log_keeps_only_latest_one_hundred_apply_runs(self):
    with tempfile.TemporaryDirectory() as tmp:
        run_log_path = Path(tmp) / RUN_LOG_NAME
        runs = [
            {
                "run_id": f"run-{index}",
                "root": str(Path(tmp).resolve()),
                "time": f"2026-06-15 00:{index:02d}:00",
                "status": "success",
                "operations": [{"action": "move", "source_before": "a", "target_after": "b"}],
            }
            for index in range(101)
        ]

        safe_write_run_log(run_log_path, {"runs": runs})

        saved = load_run_log(run_log_path)["runs"]
        self.assertEqual(len(saved), 100)
        self.assertEqual(saved[0]["run_id"], "run-1")
        self.assertEqual(saved[-1]["run_id"], "run-100")
        self.assertFalse(any(run["run_id"] == "run-0" for run in saved))
```

- [ ] **Step 2: Run the new persistence tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_file_helper_core.FileHelperCoreTests.test_create_apply_run_persists_snapshot_and_mode tests.test_file_helper_core.FileHelperCoreTests.test_update_history_result_persists_status_and_reason tests.test_file_helper_core.FileHelperCoreTests.test_run_log_keeps_only_latest_one_hundred_apply_runs -v
```

Expected: failures for the old `create_apply_run` signature, missing `update_history_result`, and missing pruning.

- [ ] **Step 3: Add pruning and result lookup helpers**

Implement:

```python
def is_apply_run(run: Any) -> bool:
    return isinstance(run, dict) and run.get("mode", "apply") == "apply"


def prune_apply_runs(data: Dict[str, Any], limit: int = MAX_APPLY_RUNS) -> None:
    runs = data.get("runs", [])
    apply_indexes = [index for index, run in enumerate(runs) if is_apply_run(run)]
    remove_count = max(0, len(apply_indexes) - limit)
    remove_indexes = set(apply_indexes[:remove_count])
    data["runs"] = [run for index, run in enumerate(runs) if index not in remove_indexes]


def find_history_result(run: Dict[str, Any], result_id: str) -> Dict[str, Any]:
    snapshot = run.get("history_snapshot", {})
    for result in snapshot.get("results", []):
        if isinstance(result, dict) and result.get("result_id") == result_id:
            return result
    raise ValueError(f"历史结果不存在：{result_id}")


class RunLogWriteError(RuntimeError):
    pass
```

Call `prune_apply_runs(data)` at the start of `safe_write_run_log` before JSON serialization.
Wrap filesystem write/replace failures in `RunLogWriteError`:

```python
def safe_write_run_log(run_log_path: Path, data: Dict[str, Any]) -> None:
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = run_log_path.with_name(RUN_LOG_TMP_NAME)
    prune_apply_runs(data)
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, run_log_path)
    except OSError as exc:
        raise RunLogWriteError(f"无法安全写入运行日志：{exc}") from exc
```

- [ ] **Step 4: Change run creation and add a persisted status update**

Change the signature and body:

```python
def create_apply_run(
    root: Path,
    run_log_path: Path,
    groups: List[PlanGroup],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data = load_run_log(run_log_path)
    run = {
        "run_id": run_id_text(),
        "mode": "apply",
        "root": absolute_text(root),
        "time": now_text(),
        "status": "running",
        "undone": False,
        "undo_time": "",
        "undo_status": "",
        "operations": [],
        "history_snapshot": build_history_snapshot(groups),
    }
    data["runs"].append(run)
    safe_write_run_log(run_log_path, data)
    return data, run


def update_history_result(
    run_log_path: Path,
    data: Dict[str, Any],
    run: Dict[str, Any],
    result_id: str,
    status: str,
    error_reason: str = "",
) -> None:
    result = find_history_result(run, result_id)
    result["status"] = status
    result["error_reason"] = error_reason
    safe_write_run_log(run_log_path, data)
```

Update existing test call sites to pass `groups` to `create_apply_run`.

- [ ] **Step 5: Run the persistence tests**

Run the command from Step 2.

Expected: all three tests pass.

- [ ] **Step 6: Write failing tests for apply result states**

Add:

```python
def test_apply_plan_marks_existing_target_skipped_in_history(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mkdir(root / "0507 军牌项链 1单2个")
        _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")
        mkdir(groups[0].target_path)
        data, run = create_apply_run(root, root / RUN_LOG_NAME, groups)

        completed, failures = apply_plan(groups, root / "rename_log.csv", root / RUN_LOG_NAME, data, run)

        result = load_run_log(root / RUN_LOG_NAME)["runs"][0]["history_snapshot"]["results"][0]
        self.assertEqual(completed, [])
        self.assertEqual(failures, 0)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("目标目录已存在", result["error_reason"])


def test_apply_plan_marks_completed_group_success_in_history(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mkdir(root / "0507 军牌项链 1单2个")
        _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")
        data, run = create_apply_run(root, root / RUN_LOG_NAME, groups)

        completed, failures = apply_plan(groups, root / "rename_log.csv", root / RUN_LOG_NAME, data, run)

        result = load_run_log(root / RUN_LOG_NAME)["runs"][0]["history_snapshot"]["results"][0]
        self.assertEqual(failures, 0)
        self.assertEqual(completed, groups)
        self.assertEqual(result["status"], "success")
```

- [ ] **Step 7: Run the apply-state tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_file_helper_core.FileHelperCoreTests.test_apply_plan_marks_existing_target_skipped_in_history tests.test_file_helper_core.FileHelperCoreTests.test_apply_plan_marks_completed_group_success_in_history -v
```

Expected: result states remain `pending`.

- [ ] **Step 8: Persist each final group state**

In `apply_plan`, enumerate groups and derive `result_id = history_result_id(index)`. Apply these updates:

```python
if group.target_path.exists():
    reason = "目标目录已存在，按 conflict.target_exists=skip 跳过。"
    for item in group.items:
        log_item(log_path, "apply", "skip", item, group=group, status="skipped", error_message=reason)
    update_history_result(run_log_path, run_log_data, run, result_id, "skipped", reason)
    print(f"跳过：目标目录已存在 -> {group.target_path}")
    continue
```

After a merge or rename completes:

```python
update_history_result(run_log_path, run_log_data, run, result_id, "success")
```

In the ordinary exception branch:

```python
reason = str(exc)
update_history_result(run_log_path, run_log_data, run, result_id, "failed", reason)
```

Add this branch before the ordinary exception branch so log persistence failures are not mistaken for source-file failures:

```python
except RunLogWriteError:
    raise
except Exception as exc:
    failed_count += 1
    reason = str(exc)
    for item in group.items:
        log_item(log_path, "apply", "skip", item, group=group, status="failed", error_message=reason)
    update_history_result(run_log_path, run_log_data, run, result_id, "failed", reason)
    print(f"错误：{group.final_name} 执行失败，已跳过。原因：{reason}")
```

- [ ] **Step 9: Update main and run the focused core tests**

Change:

```python
run_log_data, run = create_apply_run(root, run_log_path, groups)
```

Wrap `apply_plan`, compression, report generation, and final status persistence:

```python
try:
    completed_groups, failed_count = apply_plan(groups, log_path, run_log_path, run_log_data, run)
    compression_status: Dict[str, str] = {}
    if args.archive:
        failed_count += compress_groups(
            completed_groups,
            log_path,
            run_log_path,
            run_log_data,
            run,
            compression_status,
        )
except RunLogWriteError as exc:
    write_log(
        log_path,
        {
            "time": now_text(),
            "mode": "apply",
            "action": "skip",
            "status": "failed",
            "error_message": str(exc),
        },
    )
    print(f"错误：{exc}")
    print("已停止后续整理，避免产生无法记录撤销信息的操作。")
    return 1
```

Keep report generation after this block. Wrap the final `update_run_status` in the same error reporting pattern and return `1` if it cannot be saved.

Run:

```powershell
python -m unittest tests.test_file_helper_core -v
```

Expected: all file-helper tests pass.

- [ ] **Step 10: Commit persisted history**

```powershell
git add file_helper.py tests/test_file_helper_core.py
git commit -m "feat: persist and prune apply history"
```

### Task 3: Add Typed History Reader Models

**Files:**
- Modify: `launcher_core.py:1-115`
- Modify: `launcher_core.py:409-475`
- Test: `tests/test_launcher_core.py:37-430`

- [ ] **Step 1: Write failing tests for complete, legacy, empty, and damaged logs**

Add:

```python
def test_load_apply_history_builds_complete_view_models(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_log = root / "organizer_run_log.json"
        run_log.write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "run_id": "run-1",
                            "mode": "apply",
                            "root": str(root.resolve()),
                            "time": "2026-06-15 12:00:00",
                            "status": "partial",
                            "history_snapshot": {
                                "schema_version": 1,
                                "results": [
                                    {
                                        "result_id": "result-1",
                                        "final_name": "1-0507-军牌项链-1单-2个",
                                        "target_path": str(root / "1-0507-军牌项链-1单-2个"),
                                        "source_items": [
                                            {
                                                "original_name": "0507 军牌项链 1单2个",
                                                "source_type": "folder",
                                                "source_path": str(root / "0507 军牌项链 1单2个"),
                                            }
                                        ],
                                        "merged": False,
                                        "date": "0507",
                                        "category": "军牌项链",
                                        "orders": 1,
                                        "quantity": 2,
                                        "matched_keywords": ["军牌项链"],
                                        "status": "success",
                                        "error_reason": "",
                                    }
                                ],
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        state = load_apply_history(root)

        self.assertEqual(state.error, "")
        self.assertEqual(len(state.runs), 1)
        self.assertEqual(state.runs[0].status_text, "部分成功")
        self.assertTrue(state.runs[0].has_complete_details)
        self.assertEqual(state.runs[0].results[0].matched_keywords, ("军牌项链",))


def test_load_apply_history_marks_legacy_and_interrupted_runs(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "organizer_run_log.json").write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "run_id": "legacy",
                            "root": str(root.resolve()),
                            "time": "2026-06-15 11:00:00",
                            "status": "running",
                            "operations": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        state = load_apply_history(root)

        self.assertEqual(state.runs[0].status_text, "执行中断")
        self.assertFalse(state.runs[0].has_complete_details)


def test_load_apply_history_returns_empty_state_without_creating_log(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        state = load_apply_history(root)

        self.assertEqual(state.runs, ())
        self.assertEqual(state.error, "")
        self.assertFalse((root / "organizer_run_log.json").exists())


def test_load_apply_history_reports_damaged_log_with_absolute_path(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_log = root / "organizer_run_log.json"
        run_log.write_text("{broken", encoding="utf-8")

        state = load_apply_history(root)

        self.assertEqual(state.runs, ())
        self.assertIn(str(run_log.resolve()), state.error)
        self.assertIn("无法读取", state.error)
```

- [ ] **Step 2: Run the history-reader tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_launcher_core.LauncherCoreTests.test_load_apply_history_builds_complete_view_models tests.test_launcher_core.LauncherCoreTests.test_load_apply_history_marks_legacy_and_interrupted_runs tests.test_launcher_core.LauncherCoreTests.test_load_apply_history_returns_empty_state_without_creating_log tests.test_launcher_core.LauncherCoreTests.test_load_apply_history_reports_damaged_log_with_absolute_path -v
```

Expected: failures because the history dataclasses and `load_apply_history` do not exist.

- [ ] **Step 3: Add immutable view models**

Add:

```python
@dataclass(frozen=True)
class HistorySourceItem:
    original_name: str
    source_type: str
    source_path: str


@dataclass(frozen=True)
class HistoryResult:
    result_id: str
    final_name: str
    target_path: str
    source_items: tuple[HistorySourceItem, ...]
    merged: bool
    date: str
    category: str
    orders: int
    quantity: int
    matched_keywords: tuple[str, ...]
    status: str
    status_text: str
    error_reason: str


@dataclass(frozen=True)
class HistoryRun:
    run_id: str
    time: str
    root: str
    status: str
    status_text: str
    has_complete_details: bool
    results: tuple[HistoryResult, ...]


@dataclass(frozen=True)
class ApplyHistoryState:
    runs: tuple[HistoryRun, ...]
    error: str = ""
```

- [ ] **Step 4: Implement strict read-only parsing**

Add status maps and parsing functions:

```python
RUN_STATUS_TEXT = {
    "success": "成功",
    "partial": "部分成功",
    "failed": "失败",
    "running": "执行中断",
}
RESULT_STATUS_TEXT = {
    "success": "成功",
    "skipped": "跳过",
    "failed": "失败",
    "pending": "执行中断",
}
EMPTY_HISTORY_TEXT = "暂无执行历史，完成一次执行整理后会显示在这里"
LEGACY_HISTORY_TEXT = "旧版记录，详情不完整"


def load_apply_history(root_path: str | Path) -> ApplyHistoryState:
    root = Path(root_path).expanduser().resolve()
    run_log = root / "organizer_run_log.json"
    if not run_log.exists():
        return ApplyHistoryState(())
    try:
        with run_log.open("r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
        runs = data.get("runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            raise ValueError("runs 必须是列表")
        parsed = tuple(
            parse_history_run(run)
            for run in reversed(runs)
            if isinstance(run, dict) and run.get("mode", "apply") == "apply"
        )
        return ApplyHistoryState(parsed)
    except Exception as exc:
        return ApplyHistoryState((), f"organizer_run_log.json 无法读取：{run_log}\n{exc}")
```

Add the parsing helpers:

```python
def parse_history_source_item(raw: Any) -> HistorySourceItem:
    if not isinstance(raw, dict):
        raise ValueError("source_items 项必须是对象")
    return HistorySourceItem(
        original_name=str(raw.get("original_name", "")),
        source_type=str(raw.get("source_type", "")),
        source_path=str(raw.get("source_path", "")),
    )


def parse_history_result(raw: Any) -> HistoryResult:
    if not isinstance(raw, dict):
        raise ValueError("history result 必须是对象")
    source_items = raw.get("source_items", [])
    matched_keywords = raw.get("matched_keywords", [])
    if not isinstance(source_items, list):
        raise ValueError("source_items 必须是列表")
    if not isinstance(matched_keywords, list):
        raise ValueError("matched_keywords 必须是列表")
    status = str(raw.get("status", "pending"))
    return HistoryResult(
        result_id=str(raw.get("result_id", "")),
        final_name=str(raw.get("final_name", "")),
        target_path=str(raw.get("target_path", "")),
        source_items=tuple(parse_history_source_item(item) for item in source_items),
        merged=bool(raw.get("merged", False)),
        date=str(raw.get("date", "")),
        category=str(raw.get("category", "")),
        orders=int(raw.get("orders", 0) or 0),
        quantity=int(raw.get("quantity", 0) or 0),
        matched_keywords=tuple(str(value) for value in matched_keywords),
        status=status,
        status_text=RESULT_STATUS_TEXT.get(status, status or "未知"),
        error_reason=str(raw.get("error_reason", "")),
    )


def parse_history_run(raw: Dict[str, Any]) -> HistoryRun:
    status = str(raw.get("status", ""))
    snapshot = raw.get("history_snapshot")
    has_complete_details = (
        isinstance(snapshot, dict)
        and snapshot.get("schema_version") == 1
        and isinstance(snapshot.get("results"), list)
    )
    results = (
        tuple(parse_history_result(result) for result in snapshot["results"])
        if has_complete_details
        else ()
    )
    return HistoryRun(
        run_id=str(raw.get("run_id", "")),
        time=str(raw.get("time", "")),
        root=str(raw.get("root", "")),
        status=status,
        status_text=RUN_STATUS_TEXT.get(status, status or "未知"),
        has_complete_details=has_complete_details,
        results=results,
    )
```

A missing or unsupported `history_snapshot.schema_version` produces `has_complete_details=False` and an empty `results` tuple rather than guessed details.

- [ ] **Step 5: Run all launcher-core tests**

Run:

```powershell
python -m unittest tests.test_launcher_core -v
```

Expected: all launcher-core tests pass.

- [ ] **Step 6: Commit the reader layer**

```powershell
git add launcher_core.py tests/test_launcher_core.py
git commit -m "feat: read apply history for launcher"
```

### Task 4: Build the History Navigation and Empty States

**Files:**
- Modify: `launcher_gui.py:15-115`
- Modify: `launcher_gui.py:202-465`
- Test: `tests/test_launcher_gui_smoke.py:16-115`

- [ ] **Step 1: Write failing GUI smoke tests for navigation and reload**

Add:

```python
def test_history_page_starts_without_selected_run(self):
    empty_state = ApplyHistoryState(())
    with patch("launcher_gui.load_apply_history", return_value=empty_state) as load_history:
        self.run_action(self.gui.show_history_page)

    load_history.assert_called_once_with(self.gui.root_path.get())
    self.assertEqual(self.gui.active_page, "history")
    self.assertEqual(self.gui.history_detail_message.cget("text"), "请选择一条执行记录")
    self.assertEqual(self.gui.history_run_list.selection(), ())


def test_history_page_reloads_every_time_it_is_entered(self):
    with patch("launcher_gui.load_apply_history", return_value=ApplyHistoryState(())) as load_history:
        self.run_action(self.gui.show_history_page)
        self.run_action(lambda: self.gui.show_task_page("dry-run"))
        self.run_action(self.gui.show_history_page)

    self.assertEqual(load_history.call_count, 2)


def test_history_page_shows_empty_state(self):
    with patch("launcher_gui.load_apply_history", return_value=ApplyHistoryState(())):
        self.run_action(self.gui.show_history_page)

    self.assertEqual(
        self.gui.history_detail_message.cget("text"),
        "暂无执行历史，完成一次执行整理后会显示在这里",
    )
```

- [ ] **Step 2: Run the GUI tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_launcher_gui_smoke.LauncherGuiSmokeTests.test_history_page_starts_without_selected_run tests.test_launcher_gui_smoke.LauncherGuiSmokeTests.test_history_page_reloads_every_time_it_is_entered tests.test_launcher_gui_smoke.LauncherGuiSmokeTests.test_history_page_shows_empty_state -v
```

Expected: failures because the history page and widgets do not exist.

- [ ] **Step 3: Add imports and navigation state**

Import:

```python
from launcher_core import (
    ApplyHistoryState,
    EMPTY_HISTORY_TEXT,
    HistoryRun,
    LEGACY_HISTORY_TEXT,
    load_apply_history,
)
```

In `LauncherGui.__init__`, add:

```python
self.history_state = ApplyHistoryState(())
self.history_runs_by_item: dict[str, HistoryRun] = {}
```

- [ ] **Step 4: Add the navigation button and page container**

Place a new sidebar button between configuration and the flexible safety area:

```python
self.history_nav_button = ctk.CTkButton(
    sidebar,
    text="执行历史\n查看实际整理结果",
    command=self.show_history_page,
    anchor="w",
    height=58,
    fg_color="#1B2630",
    hover_color="#24313C",
    text_color="#DCE6EE",
    corner_radius=8,
    font=ctk.CTkFont(size=13, weight="bold"),
)
```

Adjust sidebar row numbers without changing the bottom update button behavior. Call `_build_history_page(body)` after `_build_config_page(body)`.

- [ ] **Step 5: Implement page switching and initial rendering**

Add:

```python
def show_history_page(self) -> None:
    if self.active_page == "config" and not self.confirm_discard_config_changes():
        return
    self.active_page = "history"
    self.task_center.grid_remove()
    self.task_right.grid_remove()
    self.action_bar.grid_remove()
    self.config_page.grid_remove()
    self.history_page.grid()
    self._set_navigation_colors("history")
    self.reload_history_page()


def reload_history_page(self) -> None:
    root_value = clean_path_value(self.root_path.get())
    if not root_value:
        self.render_history_state(ApplyHistoryState(()))
        return
    self.render_history_state(load_apply_history(root_value))
```

Extract repeated button-color changes from `show_task_page` and `show_config_page` into `_set_navigation_colors(active_page)` so task, config, and history buttons remain mutually exclusive.

- [ ] **Step 6: Build the two-pane skeleton**

Create `_build_history_page` with:

- A page-level title `执行历史`.
- A left `ttk.Treeview` with columns `time`, `root`, `status`.
- A right detail frame containing `history_detail_message`.
- No default selection after `render_history_state`.
- Empty state text when `state.runs` is empty and `state.error` is empty.
- Error text when `state.error` is non-empty.

Use the selected root only; do not search other directories.

- [ ] **Step 7: Run the focused GUI tests**

Run the command from Step 2.

Expected: all three tests pass.

- [ ] **Step 8: Commit the history page shell**

```powershell
git add launcher_gui.py tests/test_launcher_gui_smoke.py
git commit -m "feat: add apply history page"
```

### Task 5: Render Run Details and Expand Source Items

**Files:**
- Modify: `launcher_gui.py:418-800`
- Test: `tests/test_launcher_gui_smoke.py`

- [ ] **Step 1: Write failing GUI tests for complete and legacy records**

Add a helper fixture:

```python
def history_state_fixture(self) -> ApplyHistoryState:
    source = HistorySourceItem("0507 军牌项链 1单2个", "folder", r"C:\root\0507 军牌项链 1单2个")
    result = HistoryResult(
        result_id="result-1",
        final_name="1-0507-军牌项链-1单-2个",
        target_path=r"C:\root\1-0507-军牌项链-1单-2个",
        source_items=(source,),
        merged=False,
        date="0507",
        category="军牌项链",
        orders=1,
        quantity=2,
        matched_keywords=("军牌项链",),
        status="skipped",
        status_text="跳过",
        error_reason="目标目录已存在。",
    )
    run = HistoryRun(
        run_id="run-1",
        time="2026-06-15 12:00:00",
        root=r"C:\root",
        status="partial",
        status_text="部分成功",
        has_complete_details=True,
        results=(result,),
    )
    return ApplyHistoryState((run,))
```

Add tests:

```python
def test_selecting_history_run_renders_result_summary(self):
    state = self.history_state_fixture()
    with patch("launcher_gui.load_apply_history", return_value=state):
        self.run_action(self.gui.show_history_page)
        run_item = self.gui.history_run_list.get_children()[0]
        self.gui.history_run_list.selection_set(run_item)
        self.run_action(self.gui.on_history_run_selected)

    result_item = self.gui.history_result_table.get_children()[0]
    values = self.gui.history_result_table.item(result_item, "values")
    self.assertIn("1-0507-军牌项链-1单-2个", values)
    self.assertIn("军牌项链", values)
    self.assertIn("跳过", values)


def test_expanding_history_result_shows_source_and_reason(self):
    state = self.history_state_fixture()
    with patch("launcher_gui.load_apply_history", return_value=state):
        self.run_action(self.gui.show_history_page)
        run_item = self.gui.history_run_list.get_children()[0]
        self.gui.history_run_list.selection_set(run_item)
        self.run_action(self.gui.on_history_run_selected)

    result_item = self.gui.history_result_table.get_children()[0]
    children = self.gui.history_result_table.get_children(result_item)
    child_text = " ".join(str(self.gui.history_result_table.item(child, "text")) for child in children)
    self.assertIn("0507 军牌项链 1单2个", child_text)
    self.assertIn("目标目录已存在。", child_text)


def test_selecting_legacy_history_run_shows_incomplete_message(self):
    legacy = HistoryRun("legacy", "2026-06-15 11:00:00", r"C:\root", "success", "成功", False, ())
    with patch("launcher_gui.load_apply_history", return_value=ApplyHistoryState((legacy,))):
        self.run_action(self.gui.show_history_page)
        run_item = self.gui.history_run_list.get_children()[0]
        self.gui.history_run_list.selection_set(run_item)
        self.run_action(self.gui.on_history_run_selected)

    self.assertEqual(self.gui.history_detail_message.cget("text"), "旧版记录，详情不完整")
```

- [ ] **Step 2: Run the detail tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_launcher_gui_smoke.LauncherGuiSmokeTests.test_selecting_history_run_renders_result_summary tests.test_launcher_gui_smoke.LauncherGuiSmokeTests.test_expanding_history_result_shows_source_and_reason tests.test_launcher_gui_smoke.LauncherGuiSmokeTests.test_selecting_legacy_history_run_shows_incomplete_message -v
```

Expected: failures because result widgets and selection handlers do not exist.

- [ ] **Step 3: Add the result Treeview**

Create `history_result_table` with these headings:

```python
(
    "最终文件夹",
    "目标路径",
    "来源数",
    "合并",
    "状态",
    "日期",
    "品类",
    "单量",
    "数量",
    "命中关键词",
)
```

Use fixed compact widths and horizontal/vertical scrollbars. Bind the run list:

```python
self.history_run_list.bind("<<TreeviewSelect>>", self.on_history_run_selected)
```

- [ ] **Step 4: Implement run selection**

Add:

```python
def on_history_run_selected(self, _event: object = None) -> None:
    selection = self.history_run_list.selection()
    if not selection:
        return
    run = self.history_runs_by_item.get(selection[0])
    if run is None:
        return
    self.clear_history_results()
    if not run.has_complete_details:
        self.history_detail_message.configure(text=LEGACY_HISTORY_TEXT)
        return
    self.history_detail_message.configure(text="")
    for result in run.results:
        self.insert_history_result(result)
```

- [ ] **Step 5: Implement expandable child rows**

Insert each final result as a parent row. Insert source items as children using the first text column:

```python
for source in result.source_items:
    self.history_result_table.insert(
        parent_id,
        "end",
        text=f"来源：{source.original_name}",
        values=(source.source_path, source.source_type, "", "", "", "", "", "", "", ""),
    )
if result.error_reason:
    self.history_result_table.insert(
        parent_id,
        "end",
        text=f"原因：{result.error_reason}",
        values=("", "", "", "", "", "", "", "", "", ""),
    )
```

Keep parent rows collapsed by default. This satisfies “原因仅展开后显示” without adding a permanent reason column.

- [ ] **Step 6: Run all GUI smoke tests**

Run:

```powershell
python -m unittest tests.test_launcher_gui_smoke -v
```

Expected: all GUI smoke tests pass.

- [ ] **Step 7: Commit detail rendering**

```powershell
git add launcher_gui.py tests/test_launcher_gui_smoke.py
git commit -m "feat: show apply history details"
```

### Task 6: Document Retention and Bump Version

**Files:**
- Modify: `README.md:234-286`
- Modify: `README.md:331-435`
- Modify: `VERSION.txt:1-3`

- [ ] **Step 1: Update README history and retention documentation**

Add a section after “一键撤销”:

```markdown
## 执行历史

GUI 左侧的“执行历史”只显示当前所选 root 下的实际 `apply` 记录，不显示 dry-run 或 undo-last。

- 左侧按最新到最旧显示执行时间、root 和整体状态。
- 选择记录后，右侧按最终结果文件夹显示目标路径、来源数量、合并状态、识别日期、品类、单量、数量和命中关键词。
- 展开最终结果可查看原始来源项；跳过或失败原因只在展开内容中显示。
- 旧版没有详情快照的记录仍显示摘要，但会提示“旧版记录，详情不完整”。
- `running` 残留记录显示为“执行中断”，程序不会根据当前磁盘内容猜测结果。

`organizer_run_log.json` 最多保留最近 100 次 `apply`。写入新记录后会自动删除最旧的完整 run，包括其撤销操作，因此被删除的记录不能再撤销。`rename_log.csv` 不受该限制，继续保留人工审计记录。
```

Update GUI usage bullets to mention the new navigation entry and clarify that the history page is read-only.

- [ ] **Step 2: Bump the feature version**

Update `VERSION.txt` to:

```text
v2.5.0
release_date: 2026-06-15
name: Windows文件整理助手
```

- [ ] **Step 3: Verify docs and metadata**

Run:

```powershell
Select-String -Path README.md -Pattern "执行历史","最近 100 次","不能再撤销"
Get-Content -Encoding UTF8 VERSION.txt
```

Expected: all three README phrases appear and the version is `v2.5.0`.

- [ ] **Step 4: Commit docs and version**

```powershell
git add README.md VERSION.txt
git commit -m "docs: describe apply history retention"
```

### Task 7: Run End-to-End Verification

**Files:**
- Verify: `file_helper.py`
- Verify: `launcher_core.py`
- Verify: `launcher_gui.py`
- Verify: `tests/`

- [ ] **Step 1: Run syntax checks**

Run:

```powershell
python -m py_compile file_helper.py launcher_core.py launcher_gui.py
```

Expected: exit code `0` with no output.

- [ ] **Step 2: Run the complete unit test suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 3: Exercise a disposable apply and inspect its history snapshot**

Create a disposable root inside the repository:

```powershell
$smokeRoot = (Join-Path (Get-Location) "tmp-history-smoke")
if (-not ([IO.Path]::GetFullPath($smokeRoot)).StartsWith([IO.Path]::GetFullPath((Get-Location).Path))) {
    throw "Smoke root escaped the workspace"
}
New-Item -ItemType Directory -Path $smokeRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $smokeRoot "0501 军牌钥匙扣 1单2个") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $smokeRoot "0505 军牌钥匙扣 2单3个") -Force | Out-Null
python file_helper.py --root ".\tmp-history-smoke" --config ".\config.default.yaml" --apply --yes
```

Expected:

- The apply completes without prompting.
- `tmp-history-smoke\organizer_run_log.json` contains one run with `mode: "apply"`.
- The run contains `history_snapshot.schema_version: 1`.
- The result is `success`, contains two source items, and has `merged: true`.

Inspect the saved snapshot:

```powershell
$history = Get-Content -Raw -Encoding UTF8 (Join-Path $smokeRoot "organizer_run_log.json") | ConvertFrom-Json
$history.runs[-1] | ConvertTo-Json -Depth 8
```

After verification, re-check the path and remove only this disposable root:

```powershell
$resolvedSmokeRoot = [IO.Path]::GetFullPath($smokeRoot)
$resolvedWorkspace = [IO.Path]::GetFullPath((Get-Location).Path)
if (-not $resolvedSmokeRoot.StartsWith($resolvedWorkspace) -or (Split-Path $resolvedSmokeRoot -Leaf) -ne "tmp-history-smoke") {
    throw "Refusing to remove unexpected path: $resolvedSmokeRoot"
}
Remove-Item -LiteralPath $resolvedSmokeRoot -Recurse -Force
```

- [ ] **Step 4: Verify undo still uses the retained run**

Create a separate disposable root, run apply, then run undo:

```powershell
$undoSmokeRoot = (Join-Path (Get-Location) "tmp-history-undo-smoke")
if (-not ([IO.Path]::GetFullPath($undoSmokeRoot)).StartsWith([IO.Path]::GetFullPath((Get-Location).Path))) {
    throw "Undo smoke root escaped the workspace"
}
New-Item -ItemType Directory -Path $undoSmokeRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $undoSmokeRoot "0507 军牌项链 1单2个") -Force | Out-Null
python file_helper.py --root ".\tmp-history-undo-smoke" --config ".\config.default.yaml" --apply --yes
python file_helper.py --root ".\tmp-history-undo-smoke" --undo-last --yes
```

Expected:

- Undo completes using `operations`.
- The run receives `undone: true`.
- History snapshot fields remain present.

Remove only the verified disposable root afterward:

```powershell
$resolvedUndoRoot = [IO.Path]::GetFullPath($undoSmokeRoot)
$resolvedWorkspace = [IO.Path]::GetFullPath((Get-Location).Path)
if (-not $resolvedUndoRoot.StartsWith($resolvedWorkspace) -or (Split-Path $resolvedUndoRoot -Leaf) -ne "tmp-history-undo-smoke") {
    throw "Refusing to remove unexpected path: $resolvedUndoRoot"
}
Remove-Item -LiteralPath $resolvedUndoRoot -Recurse -Force
```

- [ ] **Step 5: Perform a source GUI smoke launch**

Run:

```powershell
py launcher_gui.py
```

Manually verify:

- “执行历史” appears in the left navigation.
- Entering it with no root selected shows the empty state.
- Selecting a root with a valid run log reloads the list.
- No run is selected automatically.
- Selecting a complete run shows final-result rows.
- Expanding a row shows sources and any reason.

Close the GUI after verification.

- [ ] **Step 6: Check the final diff and working tree**

Run:

```powershell
git diff --check
git status --short
git log --oneline -8
```

Expected:

- `git diff --check` reports no whitespace errors.
- Only known pre-existing untracked files remain.
- The feature commits are present in order.

### Task 8: Refresh the Foldered Windows Deliverable

**Files:**
- Build from: `launcher_gui.py`
- Sync external files: `file_helper.py`, `config_manager.py`, `config.default.yaml`, `README.md`, `VERSION.txt`
- Verify with: `tools/check_release_sync.py`

- [ ] **Step 1: Detect the existing release directory**

Run:

```powershell
$release = Get-ChildItem -Directory -Filter "Windows文件整理助手-v*" | Sort-Object Name | Select-Object -Last 1
if ($null -eq $release) {
    Write-Output "NO_RELEASE_DIRECTORY"
} else {
    $release.FullName
}
```

Expected: identify the current foldered release directory. If none exists, record that packaging cannot be refreshed in this checkout and do not create a guessed release-directory name.

- [ ] **Step 2: Rebuild the onedir application when a release directory exists**

Run:

```powershell
py -m PyInstaller --noconfirm --onedir --windowed --name "Windows文件整理助手" launcher_gui.py
```

Expected: `dist\Windows文件整理助手\Windows文件整理助手.exe` and `_internal\` are created.

- [ ] **Step 3: Replace the release application and sync editable companions**

After resolving and verifying that the target is the detected release directory inside the repository:

```powershell
$workspace = [IO.Path]::GetFullPath((Get-Location).Path)
$releasePath = [IO.Path]::GetFullPath($release.FullName)
$buildPath = [IO.Path]::GetFullPath((Join-Path (Get-Location) "dist\Windows文件整理助手"))
if (-not $releasePath.StartsWith($workspace) -or -not $buildPath.StartsWith($workspace)) {
    throw "Release or build path escaped the workspace"
}
if (-not (Test-Path -LiteralPath (Join-Path $buildPath "Windows文件整理助手.exe"))) {
    throw "Built EXE is missing"
}
$releaseInternal = Join-Path $releasePath "_internal"
if (Test-Path -LiteralPath $releaseInternal) {
    Remove-Item -LiteralPath $releaseInternal -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $buildPath "_internal") -Destination $releaseInternal -Recurse
Copy-Item -LiteralPath (Join-Path $buildPath "Windows文件整理助手.exe") -Destination (Join-Path $releasePath "Windows文件整理助手.exe") -Force
Copy-Item -LiteralPath ".\file_helper.py" -Destination (Join-Path $releasePath "file_helper.py") -Force
Copy-Item -LiteralPath ".\config_manager.py" -Destination (Join-Path $releasePath "config_manager.py") -Force
Copy-Item -LiteralPath ".\config.default.yaml" -Destination (Join-Path $releasePath "config.default.yaml") -Force
Copy-Item -LiteralPath ".\README.md" -Destination (Join-Path $releasePath "README.md") -Force
Copy-Item -LiteralPath ".\VERSION.txt" -Destination (Join-Path $releasePath "VERSION.txt") -Force
```

These commands replace only the built application and named external companions. They do not touch `user_config.yaml`, launcher settings, logs, or other user-created files.

- [ ] **Step 4: Verify release synchronization**

Run:

```powershell
python tools/check_release_sync.py --release $releasePath
```

Expected: all checked external files report `一致` and the command exits `0`.

- [ ] **Step 5: Launch the packaged EXE**

Run:

```powershell
Start-Process -FilePath (Join-Path $releasePath "Windows文件整理助手.exe")
```

Expected:

- The GUI opens.
- The version displays `v2.5.0`.
- “执行历史” is present.
- The packaged app can read a disposable root’s run log.

- [ ] **Step 6: Report packaging status without committing release output**

Do not add `dist/`, `build/`, the foldered release directory, or release archives to the source commit unless the user explicitly requests release publishing. Report whether the packaged deliverable was refreshed and verified.
