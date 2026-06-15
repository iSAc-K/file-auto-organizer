# Windows 文件自动化整理脚本

这是一个 Windows 上使用的 Python 文件整理工具。它用于批量处理压缩包和外层文件夹：识别外层名称中的日期、产品类型、单数、个数，然后按配置规则重命名或合并。

第一版 MVP 优先保证：

- 默认使用预览模式（`--dry-run`）
- 执行模式（`--apply`）前必须输入大写 `YES`
- 不删除原始压缩包
- 不删除任何文件
- 不覆盖已有目标目录
- 合并后保留来源序号文件夹
- 官方规则从 `config.default.yaml` 读取，用户修改保存在 `user_config.yaml`
- 所有计划和执行结果写入 `rename_log.csv`

## 安装准备

1. 安装 Python 3。
2. 推荐安装 PyYAML：

```powershell
python -m pip install PyYAML
```

如果没有 PyYAML，脚本也内置了一个简单 YAML 解析器，可以读取本项目默认配置。

3. 如果要从源码运行现代化图形启动器，需要安装 CustomTkinter：

```powershell
python -m pip install customtkinter
```

仅从源码运行现代化图形启动器时需要手动安装 `customtkinter`。如果发布文件夹中的 EXE 已使用当前现代化启动器重新打包，才会自带界面依赖；源码更新不等于 EXE 已重建，如果本次没有重新打包，应以源码运行或等待新版发布文件夹为准。

4. 安装 WinRAR。

脚本优先查找并使用 `WinRAR.exe` 处理 zip、rar、7z。只有处理 rar 且找不到 WinRAR.exe 时，才尝试 `UnRAR.exe`。找不到可用解压工具时，会跳过当前压缩包并写入日志。

## 运行方式

只预览，不做真实修改：

```powershell
python file_helper.py --root "D:\待整理文件" --dry-run
```

真实执行：

```powershell
python file_helper.py --root "D:\待整理文件" --apply
```

如果没有写 `--dry-run`、`--apply` 或 `--undo-last`，默认进入 dry-run 模式。这三个参数互斥。

`--apply` 会先打印完整计划，必须输入大写 `YES` 才会执行。输入其他内容会取消执行。

如果已经在图形启动器里确认，也可以由启动器追加 `--yes` 跳过命令行输入；手动命令不写 `--yes` 时仍然要求输入大写 `YES`。

执行模式会按这个顺序执行：解压需要处理的压缩包、重命名或合并最终文件夹。压缩是可选功能，只有同时使用 `--apply --archive` 才会把最终文件夹单独压缩成同名 `.zip`。

撤销最近一次可撤销的执行：

```powershell
python file_helper.py --root "D:\待整理文件" --undo-last
```

`--undo-last` 会先打印撤销计划，必须输入大写 `YES` 才会执行。它只读取 `organizer_run_log.json` 中明确记录的绝对路径操作，不根据文件名猜测，不覆盖已有路径，不删除非空目录。

参数限制：

- `--archive` 只允许和 `--apply` 一起使用。
- `--dry-run --archive` 会报错：`--archive 只在 --apply 模式下生效`。
- `--undo-last --archive` 会报错：`--undo-last 模式不支持 --archive`。
- `--yes` 只允许和 `--apply` 或 `--undo-last` 一起使用。
- `--dry-run --yes` 会报错：`--yes 不能用于 --dry-run`。
- 单独使用 `--yes` 会报错：`--yes 只能与 --apply 或 --undo-last 一起使用`。

## 配置产品归类和排序

官方产品排序在 `config.default.yaml` 的 `category_priority` 中配置。普通用户应在 GUI 的“配置管理”页面调整顺序，不要直接修改官方配置。

```yaml
category_priority:
  - 军牌钥匙扣
  - 军牌项链
  - 钢片军牌钥匙扣
```

产品识别规则在 `categories` 中配置：

```yaml
categories:
  旋转钥匙扣:
    keywords:
      - 旋转钥匙扣
      - 旋转照片钥匙扣
    merge_enabled: true
```

`keywords` 使用包含匹配；只要最外层文件夹名中包含关键词就算命中。命中多个关键词时，使用长度最长的关键词，并返回它所属的标准品类。`merge_enabled: true` 表示同类产品可以合并；`false` 表示即使同类也保持单独命名。

如果只是已有品类的新叫法，只需要把关键词追加到对应品类的 `keywords` 下。

## 强制不合并

`do_not_merge_keywords` 中的关键词会让项目强制不参与合并，例如：

```yaml
do_not_merge_keywords:
  - 样品
  - 返工
  - 异常
```

命中这些关键词的文件夹仍会正常识别、排序和分配序号，只是不会自动合并。

## 命名模板

最终外层文件夹模板：

```yaml
naming:
  single_keep_original: true
  single_template: "{seq}-{clean_original_name}"
  merged_template: "{seq_range}-{date}-{category}-{orders}单-{quantity}个"
```

支持占位符：

- `{seq}`：单个序号
- `{seq_range}`：合并序号范围，例如 `1~3`
- `{date}`：日期，例如 `0507` 或 `0501-0505`
- `{category}`：标准品类
- `{orders}`：单数
- `{quantity}`：个数
- `{original_name}`：原始外层文件夹名
- `{clean_original_name}`：删除末尾 Windows 重复编号后的原始外层文件夹名，例如去掉 `(1)`
- `{custom_text}`：自定义文字
- `{merge_name}`：自定义合并组名称

单个不合并文件夹默认只在清理后原名最前面加序号，不重构为标准品类命名。合并文件夹默认使用 `序号范围-日期-标准品类-单量-数量`，同一天显示单个日期，多日期显示最小日期到最大日期。

内部来源文件夹模板：

```yaml
inner_folder_naming:
  template: "{seq}-{original_name}"
```

## 识别规则

日期、产品品类、单量、数量、强制不合并关键词都只从最外层文件夹名识别。脚本不会读取内层文件名、内层子文件夹名、Excel/CSV 文件名、普通文件名或内部相对路径参与识别和统计；内层目录只作为文件内容保留和移动。

日期支持：

- `0507`
- `05-07`
- `05.07`
- `5.7`
- `4.26`
- `2026-05-07`
- `2026.05.07`
- `20260507`

识别后统一为 `MMDD`。多个日期会取最早和最晚，例如 `0501-0505`。

数量支持：

- `12单18个`
- `12单18件`
- `12单 18个`
- `12单-18个`
- `12单，18个`
- `12单,18个`
- `12单_18件`
- `12 单 18 个`
- `12 单 18 件`
- `12订单18个`
- `12订单18件`
- `共12单18个`

数量识别来源固定为最外层文件夹名：

```yaml
quantity_detection:
  source: outer_folder_name_only
```

只有同时识别到单数和个数才算合法数量来源。如果最外层文件夹名没有单量和数量，会使用 `fallback`；不会进入内部文件名补识别。

## 序号规则

默认从 1 开始给所有可处理外层文件夹分配序号。分配前会先完成产品识别，再按 `category_priority` 排序。

排序规则：

- 已识别且在 `category_priority` 中的品类按配置顺序排序。
- 同一个品类下多个文件夹按 `sequence.sort_by` 排序。
- 已识别但不在 `category_priority` 中的品类排在优先级品类之后，仍可按标准品类合并。
- 未识别品类排在最后。

`sequence.sort_by` 支持同品类内部排序：

- `name`
- `created_time`
- `modified_time`
- `custom_priority`

合并后出现 `1~2`、`3~5`，表示最终文件夹来自这些真实序号范围。合并后内部保留 `1-...`、`2-...` 来源文件夹，方便追溯。

## 整理后压缩

执行时加上 `--archive` 后，每个最终文件夹都会在同一目录下生成一个同名 `.zip`：

```text
1~5-军牌钥匙扣-40单-50个\
1~5-军牌钥匙扣-40单-50个.zip
6~7-军牌项链-9单-13个\
6~7-军牌项链-9单-13个.zip
```

压缩规则：

- 只有 `--apply --archive` 会真正压缩。
- 不加 `--archive` 时不会压缩，也不会显示压缩执行计划。
- 每个最终文件夹单独压缩。
- zip 保存在最终文件夹所在目录。
- zip 名称等于最终文件夹名加 `.zip`。
- 不删除原文件夹。
- 不删除原始压缩包。
- 如果同名 zip 已存在，默认跳过，不覆盖，并写入日志。
- 成功创建的 zip 会记录到 `organizer_run_log.json` 的 `archive_create`；第一版撤销只提示它是本次生成产物，不自动删除。

## 一键撤销

真实执行后，root 目录会生成机器撤销日志：

```text
organizer_run_log.json
```

安全规则：

- dry-run 不写 `organizer_run_log.json`。
- apply 输入 `YES` 后创建 run，初始状态为 `running`。
- 每成功一步就立即安全写入一次日志，先写 `organizer_run_log.json.tmp`，再替换正式文件。
- run 状态可能是 `success`、`partial` 或 `failed`；`--undo-last` 只撤销 `success` 和 `partial` 且 `undone != true` 的最近一次 run。
- 撤销完成后会写回 `undone`、`undo_time`、`undo_status`，避免重复撤销同一次 run。
- 如果 `organizer_run_log.json` 损坏，撤销会停止，写入 `rename_log.csv` 的 `undo_error`，不移动任何文件。
- `move` 会按记录从 `target_after` 移回 `source_before`；如果 `source_before` 已存在则跳过，不覆盖。
- `create_dir` 只会删除本次记录创建且已经为空的目录；目录非空会跳过。
- `archive_create` 第一版不会删除 zip，只在撤销计划中提示。

## 执行历史

GUI 左侧提供只读的“执行历史”入口，只显示当前所选 root 的 apply 记录，不显示 dry-run 或 undo 记录。

- 左侧列表按最新到最旧显示执行时间、root 和状态。
- 右侧按最终结果文件夹显示目标、来源数、是否合并、日期、品类、单量、数量和命中关键词。
- 展开结果可以查看来源，以及失败或跳过原因。
- 旧版本生成的记录如果缺少新增字段，GUI 会提示“详情不完整”，不会补猜缺失内容。
- 状态仍为 `running` 的记录会显示“执行中断”；GUI 只展示日志中已经写入的内容，不根据当前磁盘状态猜测执行结果。
- 执行历史不提供搜索、删除、路径打开或从历史记录发起撤销。撤销仍只通过“撤销上次”处理最近一次可撤销的 apply。

## 日志

人类查看日志为 root 目录下的：

```text
rename_log.csv
```

机器撤销日志为 root 目录下的：

```text
organizer_run_log.json
```

`organizer_run_log.json` 最多保留最近 100 次 apply。写入第 101 次 apply 时，会删除最旧的完整 run 及其 `operations`；被删除的 run 不再显示在执行历史中，也不能再通过 `--undo-last` 撤销。`rename_log.csv` 不裁剪，继续保留完整的人类审计记录。

这些运行产物不会提交到 GitHub。仓库里只保留 `rename_log.example.csv` 作为表头示例。

预览模式的日志动作都以 `plan_` 开头，例如 `plan_extract`、`plan_scan`、`plan_merge`、`plan_rename`、`plan_skip`。执行模式阶段才会使用真实动作名称，例如 `extract`、`move`、`merge`、`rename`、`zip`、`skip`。撤销会写入 `plan_undo`、`undo_move`、`undo_remove_empty_dir`、`undo_skip`、`undo_error`。

真实执行和撤销会额外写一行 `confirm` 日志，在 `error_message` 中记录 `confirmation_method=cli_yes` 或 `confirmation_method=arg_yes`，方便区分是命令行手动输入还是参数确认。

## 整理报告

每次 `--dry-run` 或 `--apply` 结束后，会在本次 root 目录生成：

```text
整理报告.xlsx
```

dry-run 报告显示计划结果，apply 报告显示实际结果。报告字段包括原始路径、目标路径、识别日期、识别品类、命中关键词、单量、数量、是否合并、是否跳过、跳过原因、是否压缩、压缩状态、undo 支持和备注。

报告中的跳过原因会尽量细分，例如 `未识别品类`、`未识别日期`、`未识别单量`、`未识别数量`、`命中禁止合并关键词`、`已处理格式`、`目标冲突`、`压缩包冲突`。报告生成失败时会打印警告并写入日志，不会回滚或破坏已经完成的整理操作。

## 出错排查

先看命令行输出，再打开 `rename_log.csv`。常见问题：

- 找不到 WinRAR：安装 WinRAR 或把 WinRAR.exe 加入 PATH。
- 配置文件格式错误：检查缩进和冒号。
- 模板未知占位符：只使用 README 中列出的占位符。
- 目标目录已存在：第一版默认跳过，需要人工确认后再处理。
- 同名 zip 已存在：默认跳过，不覆盖；需要人工确认后再处理旧 zip。
- 撤销日志损坏：不会执行撤销移动，请先人工备份现场，再根据 `rename_log.csv` 和现有目录检查。
- 已整理判断默认只跳过合并目录；单个加序号目录无法和原始日期批次名安全区分，所以不会自动跳过。
- 压缩包损坏或文件被占用：关闭占用程序后重新运行预览模式。

正式执行前建议先备份当天待整理目录。

## 发布包同步检查

如果源码里的 `file_helper.py`、`config_manager.py`、`config.default.yaml`、`README.md` 或 `VERSION.txt` 改过，但发布文件夹没有同步，EXE 旁边的外置脚本和配置可能仍是旧版本。发布前可以运行检查命令：

```powershell
python tools/check_release_sync.py --release ".\Windows文件整理助手-v1.2"
```

这个命令只检查，不会复制、覆盖或修改发布文件夹。检查结果会显示每个文件是 `一致`、`缺失` 或 `不一致`。全部一致时返回码为 `0`；只要有缺失或不一致，返回码为 `1`。

## 规则验证与诊断

检查最终配置是否能正常读取、模板是否有效、关键词是否重复、关键词是否存在包含关系、`category_priority` 是否和品类列表一致：

```powershell
python file_helper.py check-config --config config.default.yaml
```

`check-config` 只检查配置，不会修改配置或移动任何文件。输出会区分 `[OK]`、`[WARN]`、`[ERROR]`；警告不一定导致失败，存在错误时返回码为 `1`。

测试单个文件夹名或压缩包外层名的识别结果：

```powershell
python file_helper.py test-name "0507-WZY-钢片军牌钥匙扣-13单18个" --config config.default.yaml
```

`test-name` 只分析输入名称，不扫描目录、不移动文件、不重命名、不压缩、不生成 undo 日志。它会显示识别日期、品类、命中关键词、单量、数量、禁止合并关键词、排序优先级、是否已处理格式、建议输出名和诊断信息。

关键词包含关系通常是 `[WARN]`，因为当前项目采用最长关键词优先；例如同时存在 `钥匙扣` 和 `钢片军牌钥匙扣` 时，会优先匹配更长的 `钢片军牌钥匙扣`。完全重复关键词需要人工检查，尤其是同一个关键词出现在多个品类中时。

## v2.3 GUI 增强

从源码启动 GUI：

```powershell
py launcher_gui.py
```

v2.3 的图形启动器仍然只是启动器和预览界面，不重写 `file_helper.py` 里的分类、合并、重命名、压缩或撤销规则。真实整理和真实撤销仍然只能在用户明确确认后，由 GUI 追加一次性 `--yes` 执行。

## v2.4 配置管理与自动更新

发布目录中的配置分为两层：

- `config.default.yaml`：官方完整配置，软件升级时可以更新。
- `user_config.yaml`：用户差异配置，由 GUI 的“配置管理”页面保存，升级时不会覆盖。

配置管理页面支持：

- 新增自定义品类，以及给官方品类补充关键词。
- 逐条输入或使用英文逗号、中文逗号、换行批量导入关键词。
- 使用绿色/灰色滑动开关启用或停用品类、关键词和同品类合并。
- 使用“上移”“下移”或鼠标拖动调整官方与自定义品类的统一顺序。
- 停用官方品类或关键词；官方项目不能删除，自定义项目可以删除。
- 手动保存。离开页面或关闭软件时，有未保存修改会提示保存、放弃或取消。

同一启用关键词不能同时属于两个启用品类。发生冲突时配置不会保存，并显示冲突关键词和品类。停用的品类或关键词不参与冲突检查。

软件启动后会异步检查 GitHub Releases。网络请求临时失败时会自动重试，连续失败会记录到软件目录下的 `update_check.log`。发现新版时由用户选择是否立即更新，不会静默安装。下载包必须通过 SHA-256 校验；整理任务运行期间不能安装更新。

自动检查和左侧底部的 `检查更新` 按钮共用同一个更新窗口。检查中、已是最新版、发现新版本和检查失败都在窗口内显示；检查失败后可直接点击 `重新检查`。

下载更新时会显示进度百分比、已下载/总大小、平均下载速度和预计剩余时间。速度与剩余时间每秒刷新一次。下载和 SHA-256 校验阶段可以点击 `停止更新`；停止后会删除临时 ZIP，不修改任何程序文件，重新开始时从 0% 下载。

更新进行时主窗口会被锁定，不能执行整理、修改配置或关闭主程序。进入准备安装后不能再停止或关闭更新窗口。独立的 `updater.exe` 会显示等待主程序退出、备份、安装、回滚和重新启动状态；安装失败时会自动恢复原版本，若自动恢复也失败，窗口会显示错误和备份目录，便于人工处理。

更新器保留：

- `user_config.yaml`
- `launcher_settings.json`
- `rename_log.csv`
- `organizer_run_log.json`
- `launcher_run_output.log`

软件必须放在当前用户有写入权限的文件夹中，否则无法保存用户配置或执行自动更新。网络不可用不会影响正常整理。

旧版手工修改过的 `config.yaml` 不会被自动猜测或覆盖。如果没有对应旧版官方基准，程序不会冒险自动迁移；应在新版“配置管理”页面重新添加用户品类和关键词，并保留旧文件作为核对依据。

新增功能：

- `扫描预览`：复用 dry-run 计划生成表格，显示序号、原文件夹、识别日期、品类、数量、动作、目标名称、状态和原因，不修改文件。
- 扫描预览表格的所有列使用固定宽度，超出列宽的内容会被隐藏；点击任意数据单元格可将该列临时展开到最长内容所需宽度（最大 600px），再次点击同列恢复固定宽度。支持同时展开多列，可使用底部横向滚动条查看；重新扫描时所有列会恢复固定宽度。
- `打开报告`：打开最近一次生成的 `整理报告.xlsx`。
- `预览撤销`：只检查 `organizer_run_log.json` 并生成普通撤销命令，不追加 `--yes`。
- `撤销上次整理`：确认后才启动 `--undo-last --yes`。
- `执行历史`：左侧只读入口，只查看当前所选 root 的 apply 记录；不提供搜索、删除、路径打开或从历史撤销。
- 主操作栏固定在窗口底部，只有中间内容区域滚动。

## Windows exe 使用方式

1. 解压或打开实际发布文件夹，例如 `Windows文件整理助手-v2.4`。
2. 双击 `Windows文件整理助手.exe`。
3. `file_helper.py 路径` 默认会自动指向 exe 同目录下的 `file_helper.py`。
4. 配置路径默认会自动指向 exe 同目录下的 `config.default.yaml`。
5. 在 `要处理的文件夹路径` 中选择要处理的 root 文件夹。
6. 先使用默认选中的 `预览模式（--dry-run）` 并运行，确认计划不会做真实修改。
7. 确认 dry-run 结果无误后，再切换到 `执行整理（--apply）` 运行。
8. 执行整理点击运行时会先弹窗确认；确认后启动器会通过一次性 `--yes` 执行。
9. `撤销上次（--undo-last）` 可以撤销最近一次 apply，并同样会先弹窗确认后通过一次性 `--yes` 执行。
10. 左侧 `执行历史` 可以只读查看当前所选 root 的 apply 记录，不显示 dry-run 或 undo，也不能从历史记录发起撤销。
11. `organizer_run_log.json` 和 `rename_log.csv` 会生成在 root 目录中。
12. 用户品类与关键词通过“配置管理”保存到 `user_config.yaml`。
13. 不要删除 `file_helper.py`、`config_manager.py` 和 `config.default.yaml`，否则 exe 无法正常调用核心整理脚本和配置。

## 图形启动器 launcher_gui.py

`launcher_gui.py` 是一个独立的 Windows 图形启动器，只负责选择路径、生成命令、复制命令，并通过隐藏的后台 PowerShell 调用 `file_helper.py`。它不包含文件整理、分类、合并、重命名或压缩的核心逻辑。

新版启动器使用现代化 CustomTkinter 界面：左侧是模式导航和只读的 `执行历史` 入口，右侧是整理任务工作区或当前所选 root 的历史详情。模式导航用于切换 `预览模式`、`执行整理`、`撤销上次`；执行历史不提供搜索、删除、路径打开或从历史撤销。

运行启动器：

```powershell
py launcher_gui.py
```

使用方式：

- 在 `file_helper.py 路径` 中选择本项目里的 `file_helper.py`。
- 在 `要处理的文件夹路径` 中选择需要整理的根目录，也就是命令行里的 `--root`。
- 配置路径可以选择其他完整配置；通常保持默认的 `config.default.yaml`，程序会自动叠加同目录 `user_config.yaml`。
- 路径旁边会显示状态提示：`已找到` 表示路径存在，`未选择` 表示还没有填写，`路径不存在` 表示当前填写内容无效。
- `预览模式` 对应 `--dry-run`，默认选中，只生成和执行预览命令，不做真实修改。
- `执行整理` 对应 `--apply`，会执行真实整理；点击运行时会先弹出确认框，确认后启动器才自动追加一次性 `--yes`，PowerShell 不再等待输入大写 `YES`。
- `撤销上次` 对应 `--undo-last`，会生成撤销命令，不使用 `config.yaml`，不带 `--config`、`--dry-run`、`--apply` 或 `--archive`，并会禁用压缩选项。
- `执行历史` 只读取当前所选 root 的 apply 记录，左侧按最新到最旧显示时间、root 和状态，右侧显示最终结果文件夹及可展开详情；不显示 dry-run 或 undo。
- `整理完成后压缩最终文件夹` 只会在执行模式中追加 `--archive`。
- `处理完成后打开结果目录` 会在脚本正常退出后用 PowerShell `Start-Process` 打开 root；脚本失败时不会自动打开。
- 点击 `生成命令` 可以在预览框查看普通 PowerShell 命令；普通命令预览永远不带 `--yes`。
- 点击 `复制命令` 会重新生成普通命令并复制到剪贴板；复制命令永远不带一次性确认用的 `--yes`。
- 点击 `后台运行` 时，dry-run 会直接在后台运行；apply 和 undo-last 会先弹确认框，确认后才追加 `--yes` 并执行。启动器不会显示 PowerShell 窗口，输出会追加写入启动器同目录的 `launcher_run_output.log`，任务结束后会弹窗提示成功或失败。
- 点击 `保存设置` 会把当前路径和选项保存到 `launcher_gui.py` 同目录的 `launcher_settings.json`。
- 点击 `清空已保存路径` 会删除或重置保存的路径和选项。

启动器只会记住 Python 命令、`file_helper.py` 路径、root 路径、config 路径、运行模式、压缩勾选状态和打开结果目录勾选状态。`launcher_settings.json` 只保存这些路径和 UI 选项，不保存客户文件内容；如果 JSON 损坏，启动器会弹窗提示并使用默认值。
