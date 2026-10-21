# 《毛泽东文集》论文引文检索 Skill

根据论文题目、摘要、论点或关键词，在使用者自己的《毛泽东文集》第 1—8 卷 PDF 中寻找相关原话，回读上下文，并核实卷次、篇名和页码。

仓库只提供 Skill 指令与本地检索工具。使用者自行准备 PDF、建立索引；PDF、提取的全文、索引及个人文件路径不随仓库分发。

## 能做什么

- 跨卷搜索关键词，或限定某一卷，寻找与论文问题相关的候选段落。
- 读取完整页面及相邻页面，渲染原 PDF 页图供核对。
- 提供原话、出处、与论点的关联及必要的历史语境。
- 区分已核实引文与待核候选，保留证据不足或不支持原论点的结果。

检索工具按中文二字索引和字面词命中排序，**不是语义搜索**。代理需要拆解论文问题、扩展历史用语、多轮检索并阅读原文。它不能保证找出所有相关内容，也不能仅凭文字提取结果认证引文。

## 安装

需要 Python 3.9 或更高版本。以下示例使用 `python3`；Windows 若使用 `python`，将命令中的 `python3` 替换为 `python`。所有命令都在本 Skill 文件夹中执行。

1. 解压下载的文件，将包含 `SKILL.md` 的文件夹命名为 `mao-wenji-research`。
2. 将它放入支持的 Skill 目录，例如 Codex 的 `~/.agents/skills/mao-wenji-research/`。也可根据所用代理的要求选择对应的 Skill 目录。
3. 在终端进入该文件夹，安装依赖：

   ```bash
   python3 -m pip install -r requirements.txt
   ```

   唯一的第三方依赖是 PyMuPDF，用于 PDF 文字提取和页图渲染。需要隔离依赖时，可先创建并激活 Python 虚拟环境；代理执行脚本时也须使用装有依赖的解释器。

4. 用自己的 PDF 建立索引：

   ```bash
   python3 scripts/rebuild_index.py --source-dir "自己的PDF目录"
   python3 scripts/retrieve.py --status
   ```

5. 开启一轮新对话，让代理发现 Skill。例如：

   > 用 mao-wenji-research，为论文主题“调查研究与认识方法”寻找相关原话，附上下文和准确出处。

### PDF 的命名与卷次

默认要求第 1—8 卷齐全，每卷对应一个 PDF。文件名可包含 `第1卷`、`卷一`、`volume1` 或 `vol1`，也可直接命名为 `1.pdf`，以此类推。中文卷数支持“一”至“八”。建议使用 `毛泽东文集第1卷.pdf` 这样的明确名称。

如果文件名不能明确识别卷次，或目录中存在同卷多个版本，使用映射文件指定要用的 PDF。新建 `local-manifest.json`，路径相对于这个 JSON 文件所在的目录：

```json
{
  "volumes": [
    {"volume": 1, "path": "PDF/文集上册.pdf"},
    {"volume": 2, "path": "PDF/文集第二册.pdf"}
  ]
}
```

这个例子只有两卷，需显式允许部分卷：

```bash
python3 scripts/rebuild_index.py --manifest "local-manifest.json" --allow-partial
```

完整使用时，在映射中补齐 1—8 卷，并去掉 `--allow-partial`。目录扫描也支持 `--allow-partial`。查询和论文引用必须以实际已索引的卷次为范围，不能把部分卷结果称为全八卷检索。

### 本地数据放在哪里

索引、源文件清单和书签目录都生成在使用者的本地数据目录中，默认位置为：

| 系统 | 默认数据目录 |
| --- | --- |
| macOS | `~/Library/Application Support/mao-wenji-research` |
| Linux | `$XDG_DATA_HOME/mao-wenji-research`；未设置时为 `~/.local/share/mao-wenji-research` |
| Windows | `%LOCALAPPDATA%\mao-wenji-research` |

位置选择顺序为：命令的 `--data-dir` 参数、`MAO_WENJI_DATA_DIR` 环境变量、系统默认目录。自定义位置的例子：

```bash
python3 scripts/rebuild_index.py --source-dir "自己的PDF目录" --data-dir "自己的本地数据目录"
python3 scripts/retrieve.py --status --data-dir "自己的本地数据目录"
```

建立和查询索引应使用同一个数据目录。需要代理持续使用自定义位置时，告知代理该位置，或在代理运行环境中设置 `MAO_WENJI_DATA_DIR`。原 PDF 不会被复制到 Skill 文件夹，之后仍需保留；移动或替换 PDF 后重新建立索引。

## 检索与核对示例

按词检索：

```bash
python3 scripts/retrieve.py 调查 研究 实际 --limit 8
python3 scripts/retrieve.py 群众 领导 --volume 7 --limit 6
```

读取第 7 卷 PDF 第 30 页及前后各一页：

```bash
python3 scripts/retrieve.py --page 7:30 --context 1
```

渲染该页，随后用图片查看工具打开生成的图片核对：

```bash
python3 scripts/retrieve.py --page 7:30 --render "work/page.png"
```

查看 PDF 书签目录或索引状态：

```bash
python3 scripts/retrieve.py --catalogue --volume 7
python3 scripts/retrieve.py --status
```

PDF 页序从 1 开始，**不等于书上印刷的页码**。书签用于导航，可能包含小节或不准确的标题，不能直接当作已认证的文章篇名。

## 引文的质量边界

- 输出可用于论文的引文前，代理必须查看原 PDF 页图，核对字形、标点、跨页断句、正文身份及上下文；没有完成核对的结果只能作为“待核候选”。
- 印刷页码从页图读取。未核实则明确写“PDF 第 N 页，印刷页码待核”，不能用统一偏移量推算。篇名、文章日期应与正文核对；出版社、版次和出版年应与本卷版权页核对。
- 文字层可能混入页眉、脚注或识别错误。本工具不自动进行 OCR；扫描 PDF 或文字层缺失的 PDF 需要先自行 OCR，再建立索引。即使有文字层，也不能代替原页校核。
- 语料由使用者提供，版本、完整性和文字质量可能不同。检索未命中不等于原书没有相关论述。引用时保留历史语境，区分作者正文、编者注和引述他人的话。
- 不凭模型记忆补写原话、篇名、日期或页码，也不把不同段落拼成连续引文。详细方法见 [核对指南](references/research-guide.md)。

## 上传 GitHub

新建仓库后，把本文件夹中的源文件上传，使仓库根目录直接出现 `SKILL.md`。应上传的内容为：

```text
SKILL.md
README.md
requirements.txt
.gitignore
agents/
references/
scripts/
tests/
```

不要上传原始 PDF、全文提取结果、SQLite 索引、本地生成的源文件清单、书签目录、页图或含个人路径的映射文件。这些材料应留在自己的电脑上；其他使用者用自己的 PDF 重新建立索引。上传前也应检查压缩包和临时文件是否夹带这些内容。

## 本地自测

安装依赖后可运行合成PDF测试，不需要提供文集原文：

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖新建索引、卷过滤、页面上下文、原页渲染、缺卷/重复卷拒绝、失败时保留旧索引，以及自定义数据目录。

本通用版未擅自添加开源许可证。若准备公开授权他人复用代码，请先确定适用的许可证；原书材料的使用与分发权限需另行确认。
