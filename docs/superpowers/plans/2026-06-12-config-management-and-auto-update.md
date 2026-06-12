# Config Management And Auto Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GUI-managed user category overrides and confirmation-based GitHub Release updates without overwriting user data.

**Architecture:** Keep `config.default.yaml` as the complete official configuration and store only user differences in `user_config.yaml`. A focused configuration module builds and validates the effective config; a focused update module checks metadata, downloads and verifies packages; a separate updater process performs allow-listed replacement and rollback.

**Tech Stack:** Python 3, CustomTkinter/Tkinter, PyYAML with the existing fallback parser, `urllib`, `zipfile`, `hashlib`, `unittest`, PyInstaller.

---

### Task 1: Layered configuration model

**Files:**
- Create: `config_manager.py`
- Create: `tests/test_config_manager.py`
- Modify: `file_helper.py`
- Rename: `config.yaml` to `config.default.yaml`

- [ ] Write failing tests for user-added categories, official keyword additions, disabled categories/keywords, merge overrides, ordering, conflict diagnostics, batch parsing, and atomic save.
- [ ] Run `python -m unittest tests.test_config_manager -v` and confirm failures are caused by the missing module.
- [ ] Implement typed editor-state helpers, user-difference loading/saving, deterministic effective-config merge, and conflict validation.
- [ ] Make `file_helper.py` load `config.default.yaml` plus adjacent `user_config.yaml` by default while preserving explicit `--config` behavior.
- [ ] Run configuration tests and the complete suite.

### Task 2: GUI configuration management page

**Files:**
- Modify: `launcher_core.py`
- Modify: `launcher_gui.py`
- Modify: `tests/test_launcher_core.py`

- [ ] Write failing core tests for default config paths, editor dirty-state helpers, category movement, and batch keyword parsing.
- [ ] Add a left-navigation “配置管理” page with category list, enable switches, up/down and drag ordering, category details, merge switch, per-keyword switches, single/batch add, delete, conflict display, and manual save.
- [ ] Add save/discard/cancel handling when leaving the page or closing the app.
- [ ] Keep organizer command generation and all filesystem organization behavior outside the GUI.
- [ ] Run GUI core tests, syntax checks, and a source launch smoke test.

### Task 3: Update metadata, download, and verification

**Files:**
- Create: `update_manager.py`
- Create: `tests/test_update_manager.py`
- Modify: `launcher_gui.py`

- [ ] Write failing tests for semantic version comparison, metadata parsing, SHA-256 verification, and update file preserve rules.
- [ ] Implement timeout-bounded GitHub metadata fetching and download to a temporary directory.
- [ ] Add non-blocking startup checks and an “立即更新 / 稍后提醒” dialog.
- [ ] Block installation while organizer PowerShell is active and never block normal startup on network failure.
- [ ] Run update tests and the complete suite.

### Task 4: Separate updater and rollback

**Files:**
- Create: `updater.py`
- Create: `tests/test_updater.py`
- Modify: `Windows文件整理助手.spec`

- [ ] Write failing tests for allow-listed extraction, preserved files, backup restoration, and zip-slip rejection.
- [ ] Implement wait-for-parent, backup, safe extraction, allow-listed replacement, rollback, and restart.
- [ ] Package the updater as a separate windowed executable and include it beside the launcher.
- [ ] Run updater tests against disposable directories.

### Task 5: Migration, documentation, and release synchronization

**Files:**
- Modify: `README.md`
- Modify: `VERSION.txt`
- Modify: `tools/check_release_sync.py`
- Modify: `tests/test_check_release_sync.py`
- Modify: `Windows文件整理助手-v2.3/` release folder when present

- [ ] Document official/user config separation, GUI editing, conflict rules, writable-folder requirement, update behavior, and rollback.
- [ ] Add a conservative migration path that never guesses differences when an official baseline is unavailable.
- [ ] Update release synchronization checks for `config.default.yaml`, `user_config.yaml`, updater files, README, and version.
- [ ] Run `python -m py_compile file_helper.py launcher_core.py launcher_gui.py config_manager.py update_manager.py updater.py`.
- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run CLI `--help`, `check-config`, and `test-name` checks.
- [ ] Rebuild the foldered distribution and inspect the release folder contents.
