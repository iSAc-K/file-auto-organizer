# Windows 文件自动化整理脚本

这是一个 Windows 上使用的 Python 命令行文件整理工具。它用于批量处理压缩包和外层文件夹：识别内部文件名或子文件夹名中的日期、产品类型、单数、个数，然后按 `config.yaml` 中的规则重命名或合并。

第一版 MVP 优先保证：

- 默认 dry-run 预览
- apply 前必须输入大写 `YES`
- 不删除原始压缩包
- 不删除任何文件
- 不覆盖已有目标目录
- 合并后保留来源序号文件夹
- 所有规则尽量从 `config.yaml` 读取
- 所有计划和执行结果写入 `rename_log.csv`

## 安装准备

1. 安装 Python 3。
2. 推荐安装 PyYAML：

```powershell
python -m pip install PyYAML
```

如果没有 PyYAML，脚本也内置了一个简单 YAML 解析器，可以读取本项目默认配置。

3. 安装 WinRAR。

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

如果没有写 `--dry-run` 或 `--apply`，默认进入 dry-run 模式。

`--apply` 会先打印完整计划，必须输入大写 `YES` 才会执行。输入其他内容会取消执行。

## 配置产品归类

产品规则在 `config.yaml` 的 `categories` 中配置：

```yaml
categories:
  钥匙扣:
    keywords:
      - 雕刻钥匙扣
      - 旋转钥匙扣
      - keychain
    merge_enabled: true
```

`keywords` 用于识别产品。`merge_enabled: true` 表示同类产品可以合并；`false` 表示即使同类也保持单独命名。

## 强制不合并

`do_not_merge_keywords` 中的关键词会让项目强制不参与合并，例如：

```yaml
do_not_merge_keywords:
  - 样品
  - 返工
  - 补发
```

## 命名模板

最终外层文件夹模板：

```yaml
naming:
  single_template: "{seq}-{date}-{category}-{orders}单-{quantity}个"
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
- `{custom_text}`：自定义文字
- `{merge_name}`：自定义合并组名称

内部来源文件夹模板：

```yaml
inner_folder_naming:
  template: "{seq}-{original_name}"
```

## 识别规则

日期支持：

- `0507`
- `05-07`
- `05.07`
- `2026-05-07`
- `2026.05.07`
- `20260507`

识别后统一为 `MMDD`。多个日期会取最早和最晚，例如 `0501-0505`。

数量支持：

- `12单18个`
- `12单 18个`
- `12单-18个`
- `12 单 18 个`
- `12订单18个`
- `12 orders 18 pcs`
- `12 order 18 pieces`
- `共12单18个`

数量识别优先级：Excel/CSV 文件名 > 子文件夹名 > 普通文件名。同一最高优先级中多个不同来源会累加。

## 序号规则

默认从 1 开始给所有可处理外层文件夹分配序号。

`sequence.sort_by` 支持：

- `name`
- `created_time`
- `modified_time`
- `custom_priority`

合并后出现 `1~2`、`3~5`，表示最终文件夹来自这些真实序号范围。合并后内部保留 `1-...`、`2-...` 来源文件夹，方便追溯。

## 日志

真实运行日志为 root 目录下的：

```text
rename_log.csv
```

这个文件不会提交到 GitHub。仓库里只保留 `rename_log.example.csv` 作为表头示例。

dry-run 的 action 都以 `plan_` 开头，例如 `plan_extract`、`plan_scan`、`plan_merge`、`plan_rename`、`plan_skip`。apply 阶段才会使用真实动作名称，例如 `extract`、`move`、`merge`、`rename`、`skip`。

## 出错排查

先看命令行输出，再打开 `rename_log.csv`。常见问题：

- 找不到 WinRAR：安装 WinRAR 或把 WinRAR.exe 加入 PATH。
- config.yaml 格式错误：检查缩进和冒号。
- 模板未知占位符：只使用 README 中列出的占位符。
- 目标目录已存在：第一版默认跳过，需要人工确认后再处理。
- 压缩包损坏或文件被占用：关闭占用程序后重新运行 dry-run。

正式 apply 前建议先备份当天待整理目录。
