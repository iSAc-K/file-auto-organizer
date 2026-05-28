# QA-Only Report: Windows 文件整理助手 v2.2

Date: 2026-05-28
Mode: Report-only QA, no fixes applied
Target: `C:\Users\kt\Desktop\文件夹整理\Windows文件整理助手-v2.2`
Application type: Windows desktop GUI, not a web application

## Summary

Health score: 97/100

The v2.2 packaged GUI starts successfully, required release files are present, source syntax checks pass, unit tests pass, config validation passes, name recognition passes, and dry-run safety behavior was verified on a temporary directory.

One low-severity packaging hygiene issue was found: the release folder currently contains `launcher_settings.json`, which is a runtime settings file and is not part of the documented release surface.

## Evidence

Commands run:

- `Get-ChildItem -Force 'Windows文件整理助手-v2.2'`
- `Get-Content -Path 'Windows文件整理助手-v2.2\VERSION.txt' -Encoding UTF8`
- `python -m py_compile file_helper.py launcher_gui.py launcher_core.py`
- `python -m unittest tests/test_launcher_core.py tests/test_file_helper_core.py -v`
- `python file_helper.py check-config --config config.yaml`
- `python file_helper.py test-name "0507-WZY-钢片军牌钥匙扣-13单18个" --config config.yaml`
- Temporary-root `python file_helper.py --root <temp> --config config.yaml --dry-run`
- `Start-Process Windows文件整理助手-v2.2\Windows文件整理助手.exe` smoke check

Observed results:

- Version file reports `2.2`.
- Required release files exist: EXE, `_internal`, `file_helper.py`, `launcher_core.py`, `config.yaml`, `README.md`, `VERSION.txt`.
- `py_compile` exited successfully.
- Unit tests: 14/14 passed.
- Config check passed with 16 categories.
- Test-name detected date `0507`, category `钢片军牌钥匙扣`, quantity `13单18个`.
- Dry-run temp check: `source_exists=True`, `target_exists=False`, `run_log_exists=False`.
- Packaged EXE smoke check: `exe_started=True`.

## Findings

### LOW-001: Runtime settings file is present in the packaged release folder

Severity: Low
Category: Packaging hygiene

Observed:

`Windows文件整理助手-v2.2\launcher_settings.json` exists in the release folder.

Why this matters:

`launcher_settings.json` is a local runtime settings file. The project rules and README describe it as generated local state, not a required shipped file. Including it can leak local absolute paths or make the packaged app appear preconfigured from the build machine.

Repro:

1. Open `C:\Users\kt\Desktop\文件夹整理\Windows文件整理助手-v2.2`.
2. Observe `launcher_settings.json` beside the EXE and source-side companion files.

Expected:

The release folder should contain the EXE, `_internal`, `file_helper.py`, `launcher_core.py`, `config.yaml`, `README.md`, and `VERSION.txt`, without generated local settings.

Actual:

The required files are present, and `launcher_settings.json` is also present.

## Non-Issues Verified

- GUI startup did not immediately crash in the packaged EXE smoke check.
- Dry-run did not move, rename, compress, delete, or create undo run logs.
- CLI safety-oriented tests around `--yes`, dry-run, apply, archive, and undo command generation passed.
- No real business directory apply/archive/undo was executed.

## Notes

No browser screenshots were produced because this is a Windows desktop GUI package, not a web application target. This QA run adapted `/qa-only` to packaged desktop verification and did not modify application source code or release contents.
