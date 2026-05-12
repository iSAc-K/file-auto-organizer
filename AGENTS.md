# AGENTS.md

This file is for Codex and other coding agents working in this repository.
It records the project-specific rules that are easy to forget when the chat
gets long. Follow it before changing code, docs, config, packaging, or release
artifacts.

## Project Overview

This repository is a Windows file and folder organizer for batch processing
customer/order folders and archives. The tool reads a root directory, detects
date, product category, order count, and quantity from top-level names, then
previews or applies sorting, renaming, merging, undo, and optional zip
compression.

The project is intentionally conservative because it touches real user files:

- Dry-run is the default behavior.
- Real changes require `--apply` and confirmation.
- Manual CLI confirmation must require uppercase `YES`.
- GUI-mediated execution may append `--yes` only after its own confirmation
  dialog.
- Never delete original source folders or original archives as part of normal
  organization.
- Never overwrite existing target folders or existing same-name zip files.
- Preserve logs and provenance so the user can audit what happened.

Default user-facing language for this project is Chinese. Keep GUI labels,
dialogs, README usage text, and normal explanations in Chinese unless the user
explicitly asks otherwise. Keep actual filenames, CLI flags, and code symbols
literal.

## Repository Map

Important files:

- `file_helper.py`: the core implementation. This is the source of truth for
  detection, planning, merge grouping, sequence numbering, archive handling,
  apply/undo confirmation, compression, and logging.
- `config.yaml`: user-editable business rules. Categories, keywords, merge
  flags, naming templates, conflict behavior, already-processed patterns, and
  fallback values belong here whenever possible.
- `launcher_gui.py`: a thin Tkinter launcher. It should select paths, build
  commands, show confirmation dialogs, copy commands, and launch PowerShell.
  It must not duplicate folder classification, merge, rename, compression, or
  archive business logic.
- `README.md`: Chinese user documentation. Keep it aligned with real CLI and
  GUI behavior.
- `VERSION.txt`: release metadata for the foldered Windows app.
- `rename_log.example.csv`: committed example header/schema only.
- `Windows文件整理助手-v1.1/`: foldered distributable if present. It may contain
  adjacent editable `file_helper.py`, `config.yaml`, `README.md`, `VERSION.txt`,
  `_internal/`, and the EXE. Do not assume it is committed.
- `Windows文件整理助手-v1.0.zip` / `.rar`: release assets. Do not commit or edit
  release archives unless the user explicitly asks for packaging/release work.

Generated/runtime files that should not be committed:

- `rename_log.csv`
- `organizer_run_log.json`
- `organizer_run_log.json.tmp`
- `launcher_settings.json`
- `__pycache__/`
- `build/`
- `dist/`
- temporary test folders

## Current Workspace Caution

The user may have uncommitted changes. Always inspect before editing:

```powershell
git status --short
git diff --stat
```

If files are already modified, treat those changes as user work unless you
created them in the current turn. Do not revert them. If your task touches the
same file, read the relevant diff and work with the existing changes.

Use `rg` or `rg --files` first for searches when available.

## Hard Safety Rules

Never weaken these rules without explicit user approval:

- Default mode is dry-run.
- `--apply` prints the plan before doing real work.
- Manual `--apply` requires uppercase `YES`.
- Manual `--undo-last` requires uppercase `YES`.
- `--yes` is only valid for confirmed `--apply` or `--undo-last`.
- `--yes` must be rejected for `--dry-run`.
- `--archive` is only valid with `--apply`.
- `--undo-last --archive` is invalid.
- No normal workflow deletes original source folders.
- No normal workflow deletes original archives.
- Existing target folders are skipped, not overwritten.
- Existing same-name zip files are skipped and logged, not overwritten.
- Undo uses `organizer_run_log.json`; it must not guess operations from names.
- Undo must not overwrite an existing `source_before` path.
- Undo removes only recorded empty created directories.
- First-version undo does not delete created zip archives; it only reports them.

For any change that could move, rename, delete, compress, or overwrite real
files, preserve the preview-first and confirmation-first model.

## Core Behavior Boundaries

### Detection

The current README states that date, category, order count, quantity, and
do-not-merge keywords are detected from the outer/top-level folder name. Do
not silently re-expand detection into internal filenames, Excel/CSV filenames,
or internal relative paths unless the user explicitly requests a behavior
change and docs/tests are updated together.

Date examples supported by the current docs include:

- `0507`
- `05-07`
- `05.07`
- `5.7`
- `4.26`
- `2026-05-07`
- `2026.05.07`
- `20260507`

Dates normalize to `MMDD`. Multiple dates render as a range such as
`0501-0505`; missing date uses the configured unknown-date fallback.

Quantity is valid only when both order count and item count are recognized.
Incomplete strings such as an order count without quantity should not be
treated as final totals. The current config uses:

```yaml
quantity_detection:
  source: outer_folder_name_only
```

### Categories And Merging

Keep category detection and merge permission separate:

- `keywords` detect the product category.
- `merge_enabled: true` only allows same-category merge.
- `merge_enabled: false` keeps matching items separate.
- `do_not_merge_keywords` forbids auto-merge only.
- `do_not_merge_keywords` must not block sorting, sequencing, or numbering.
- Similar-looking folder names do not merge by themselves if no category
  keyword matches.

If a product is not recognized, add or adjust `config.yaml` categories and
keywords rather than hard-coding product names in Python.

When multiple keywords match, preserve the longest-keyword-wins behavior unless
the user asks for a different rule.

### Sequencing

The intended behavior is to assign sequence numbers to all processable top-level
items, including items that will not merge. Category priority affects sort
order, not whether a product is recognized.

Single non-merged folders should keep the cleaned original name with only the
sequence prefix. Merged folders should use the configured merged template and
retain date context.

Do not use broad already-processed patterns that accidentally skip real incoming
folders. Already-processed detection must stay narrow and auditable.

### Naming

Naming rules belong in `config.yaml`, not hard-coded into Python. Current
important templates:

```yaml
naming:
  single_keep_original: true
  single_template: "{seq}-{clean_original_name}"
  merged_template: "{seq_range}-{date}-{category}-{orders}单-{quantity}个"

inner_folder_naming:
  template: "{seq}-{original_name}"
```

Supported placeholders are documented in `README.md`. If changing placeholders,
update validation, rendering, and docs together.

`clean_windows_duplicate_suffix()` strips Windows duplicate suffixes such as
`(1)` and `(10)` from the end of original names for cleaner single-folder
output. Preserve this unless the user asks otherwise.

## Archive Handling

Tool priority is deliberate:

- Prefer `WinRAR.exe` for archive handling.
- Only for `.rar`, if WinRAR is missing, try `UnRAR.exe`.
- Do not use UnRAR for `.zip` or `.7z`.
- Use Python `zipfile` where appropriate for zip listing/creation.

If archive support changes, verify both preview behavior and apply behavior.
Archive errors should be logged and should skip only the affected item when
possible.

## Compression Rules

Post-organization compression is a separate phase after apply has completed
classification, sorting, merging, and naming.

Rules:

- Only `--apply --archive` creates zip files.
- Dry-run must preview compression plans only if the CLI behavior explicitly
  supports that mode; current documented behavior rejects `--dry-run --archive`.
- Do not create zip files during planning.
- Zip each final folder separately.
- Final zip path is the final folder path plus `.zip` in the same parent.
- Existing zip path means skip and log conflict.
- The zip should include the final folder as the top-level entry so structure is
  preserved when extracted.
- Originals remain in place.

## Logging And Undo

Human audit log:

- `rename_log.csv`

Machine undo log:

- `organizer_run_log.json`

Dry-run log actions should use `plan_` names such as:

- `plan_extract`
- `plan_scan`
- `plan_merge`
- `plan_rename`
- `plan_skip`

Apply log actions should use real action names such as:

- `extract`
- `move`
- `merge`
- `rename`
- `zip`
- `skip`

Undo log actions include:

- `plan_undo`
- `undo_move`
- `undo_remove_empty_dir`
- `undo_skip`
- `undo_error`

Confirmed apply/undo should log a `confirm` row with confirmation provenance,
for example `confirmation_method=cli_yes` or `confirmation_method=arg_yes`.

If `organizer_run_log.json` is damaged, undo must stop and log an error. It
must not infer moves from folder names.

## Launcher GUI Rules

`launcher_gui.py` must stay a launcher, not a second implementation of
organization logic.

Allowed responsibilities:

- Choose Python command.
- Choose `file_helper.py`.
- Choose root folder.
- Choose optional `config.yaml`.
- Choose mode: dry-run, apply, undo-last.
- Enable compression only for apply.
- Show command preview.
- Copy ordinary commands.
- Show confirmation dialogs for apply and undo.
- Append one-shot `--yes` only after GUI confirmation.
- Launch PowerShell using the generated command.
- In frozen mode, resolve companion files from the EXE directory.

Forbidden responsibilities:

- Reimplement category detection.
- Reimplement quantity parsing.
- Reimplement merge grouping.
- Reimplement renaming.
- Reimplement compression decisions.
- Reimplement undo mechanics.

The validated PowerShell launch shape is:

```powershell
powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -Command <generated_command>
```

If a Python executable path contains spaces, invoke it with PowerShell's call
operator:

```powershell
& "C:\Path With Spaces\python.exe" ...
```

The GUI may save simple launcher settings if that behavior is already present,
but do not expand it into hidden history, analytics, or user-file content.

## Commands

Preview:

```powershell
python file_helper.py --root "D:\待整理文件" --dry-run
```

Apply:

```powershell
python file_helper.py --root "D:\待整理文件" --apply
```

Apply and compress final folders:

```powershell
python file_helper.py --root "D:\待整理文件" --apply --archive
```

Undo last apply:

```powershell
python file_helper.py --root "D:\待整理文件" --undo-last
```

Run launcher from source:

```powershell
py launcher_gui.py
```

Basic syntax check:

```powershell
python -m py_compile file_helper.py launcher_gui.py
```

If Python command availability is uncertain on Windows, check:

```powershell
py --version
python --version
```

PyYAML is recommended, but the script includes a fallback parser for the default
config shape:

```powershell
python -m pip install PyYAML
```

## Testing Guidance

Prefer targeted temporary roots under a clearly named temp/test directory. Avoid
running apply on the user's real work directory unless the user explicitly asks
and the plan has been previewed.

For behavior changes:

- Run `python -m py_compile file_helper.py launcher_gui.py`.
- Run dry-run against a small test root.
- If apply behavior changed, run apply against a disposable test root.
- If undo changed, verify undo on a disposable apply run.
- If compression changed, verify `--apply --archive` and conflict handling for
  an existing same-name zip.
- If GUI command generation changed, test command strings and at least launch
  the GUI source when practical.
- If packaged behavior matters, verify the foldered release copy as well as the
  source files.

When testing Chinese filenames in PowerShell, be careful with console encoding.
If output looks garbled, verify file contents with:

```powershell
Get-Content -Encoding UTF8 <path>
```

Prefer Unicode-safe test setup, saved test files, or ASCII temporary names if
PowerShell quoting/encoding gets unstable.

## Config Validation Guidance

When the user says they changed `config.yaml`, inspect the real file and the
real parse path first. Do not answer only from theory.

Common YAML issue:

- Category keys under `categories:` must be aligned as siblings.
- Bad indentation can nest one category under another and break parsing.

If `python -c "import yaml"` fails, that may only mean PyYAML is missing. Use
the project's actual loading path or install PyYAML if appropriate.

Keep new business rules in `config.yaml` whenever possible:

- Product names and aliases.
- Merge permissions.
- Naming formats.
- Conflict policy.
- Already-processed patterns.
- Fallback values.
- Sequence and sort settings.

Do not hard-code product-specific rules in `file_helper.py` unless there is no
reasonable config representation and the user explicitly approves it.

## Packaging And Release Rules

Preferred Windows distribution is foldered PyInstaller `--onedir`, not
single-file `--onefile`.

Validated build shape:

```powershell
py -m PyInstaller --noconfirm --onedir --windowed --name "Windows文件整理助手" launcher_gui.py
```

Release folder should contain:

- `Windows文件整理助手.exe`
- `_internal\`
- `file_helper.py`
- `config.yaml`
- `README.md`
- `VERSION.txt`

The EXE launcher depends on adjacent external `file_helper.py` and `config.yaml`.
This is intentional so rules and core logic can be updated without rebuilding
the binary when appropriate.

If updating a release folder without rebuilding the EXE, state that clearly.
Sync external files into the release folder if the user expects packaged
behavior to change.

Normal source commits should include source/config/docs such as:

- `.gitignore`
- `README.md`
- `VERSION.txt`
- `config.yaml`
- `file_helper.py`
- `launcher_gui.py`
- `AGENTS.md`

Normal source commits should not include foldered release output or release zip
assets unless the user explicitly asks.

Release archives should be published through GitHub Releases rather than mixed
into ordinary source commits when possible.

## GitHub / Release Context

Known repo context from prior work may be useful but should be verified before
publishing:

- GitHub repo: `https://github.com/iSAc-K/file-auto-organizer.git`
- Default publish branch used before: `main`
- Release tag used before: `v1.0`

Before pushing, creating tags, uploading assets, or using `gh`, verify current
remote, branch, and auth state in the current session.

Do not continue upload/publish automation if the user says they already finished
or asks you to stop.

## Documentation Rules

Keep README practical and user-facing:

- Explain exact commands.
- Explain dry-run before apply.
- Explain GUI confirmation and `--yes`.
- Explain undo limits.
- Explain that `config.yaml` is editable.
- Explain WinRAR/UnRAR requirements.
- Explain logs and known skip/conflict cases.

Avoid developer-only prose in user instructions. Use plain Chinese for normal
Windows users.

If behavior changes, update README in the same change. Do not let docs describe
old safety behavior.

## Agent Workflow Checklist

Before edits:

1. Read `git status --short`.
2. Read relevant diffs if files are modified.
3. Identify whether the task touches core logic, GUI, config, docs, package, or
   release assets.
4. For risky changes, explain the modification plan before editing unless the
   user has already explicitly approved implementation.
5. Preserve user changes.

During edits:

1. Keep core organizer behavior in `file_helper.py`.
2. Keep launcher behavior in `launcher_gui.py`.
3. Keep business rules in `config.yaml` where possible.
4. Keep docs in Chinese unless asked otherwise.
5. Do not add broad rewrites or unrelated refactors.

Before final response:

1. Run the smallest meaningful verification.
2. Report exact commands run.
3. Report files changed.
4. Say clearly if packaged release files were or were not updated.
5. Say clearly if tests could not be run.

## Things To Be Extra Careful About

- Do not confuse classification with merging.
- Do not let `do_not_merge_keywords` block sequence numbering.
- Do not broaden already-processed patterns casually.
- Do not make `launcher_gui.py` a second organizer.
- Do not remove uppercase `YES` from manual CLI apply/undo.
- Do not make `--yes` generally available.
- Do not overwrite existing output folders or zip files.
- Do not delete originals.
- Do not assume release EXE behavior changed unless adjacent files were synced
  or the EXE was rebuilt.
- Do not trust stale memory for GitHub auth, branch, or release state; verify.

