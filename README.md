# OpenNavicat

目标是 **1:1 还原本机 Navicat 的界面和完整数据库工具功能**，运行环境为
**Windows、Linux、macOS**。排除产品账号、产品登录、订阅、激活及云账号协作；
数据库认证、数据库用户/权限和云数据库连接仍属于完整工具范围。使用
Python + Qt Widgets（PySide6），数据库连接、SQL 和结果均在本机处理。
项目从本机 Navicat Premium 的功能资源调查开始，以独立实现逐步覆盖完整的
数据库管理工作流。

下面是一段 OpenNavicat 实际运行演示：浏览示例表、编写并执行查询、查看结果、编辑表结构和
检查 SQL 预览。连接名、客户、邮箱、日期、订单及 SQL 均来自隔离生成的演示工作区，
不读取本机真实连接或业务数据库。演示对应当前 0.4.0 源码；公开安装版本以 Release 为准。

![OpenNavicat 示例数据工作流演示](docs/assets/opennavicat-demo.gif)

最新可用里程碑 **[v0.4.0](https://github.com/shubolab/opennavicat/releases/tag/v0.4.0)**
已发布为早期预发行版本，提供三个操作系统、两种架构的六份安装包。SQLite、PostgreSQL、MySQL、MariaDB
已实现驱动与核心工作流，使用隔离服务进行真实读写和界面集成验证。完整 Navicat Premium 能力仍在持续实现中，
详细范围见 [功能路线图](docs/roadmap.md)。

| 安装包 | amd64 / Intel、x64 | arm64 / Apple Silicon、Windows ARM |
| --- | --- | --- |
| macOS | [DMG](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/OpenNavicat-0.4.0-macos-amd64.dmg) | [DMG](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/OpenNavicat-0.4.0-macos-arm64.dmg) |
| Windows | [Setup EXE](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/OpenNavicat-0.4.0-windows-amd64-setup.exe) | [Setup EXE](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/OpenNavicat-0.4.0-windows-arm64-setup.exe) |
| Linux（Debian/Ubuntu） | [DEB](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/OpenNavicat-0.4.0-linux-amd64.deb) | [DEB](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/OpenNavicat-0.4.0-linux-arm64.deb) |

[SHA256SUMS](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/SHA256SUMS)
和 [构建与安装验证清单](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/build-manifests.json)
随版本发布。当前安装包没有发布者签名或 Apple 公证；Windows ARM64 安装引导器使用 x86，
安装后的应用、Python、Qt 及原生库为 ARM64。实际系统验证范围和功能边界见
[0.4 验证记录](docs/validation-0.4.md)。

首个可用版本交付后曾按要求暂停。用户更新本机 Navicat 后已于 2026-11-10 明确恢复；
当前参考为 **Navicat Premium 18.0.5 build 321**，已重新实际打开并验证新建表、修改表、
SQL 预览、查询和多结果工作流。继续原定完整功能和 1:1 UI 目标，
旧 16.1.1 记录保留为历史证据。新版运行观察见
[18 版实际界面与行为记录](docs/ui-reference-18-observations.md)。

0.1 的自定义界面没有满足还原要求。0.2 开始实际打开参考应用，以同一个本地样例库
观察并重建主工具栏、分类导航、对象列表、信息面板、查询/结果区和连接对话框。
已实现的局部工作流不等于完整还原；[完整对齐矩阵](docs/parity-matrix.md) 分开记录
UI、工具能力和平台验收，所有未通过的门禁都保留在目标内。

## 运行

需要 Python 3.11 或更新版本；开发与打包建议使用 Python 3.13。

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m opennavicat
```

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m opennavicat
```

安装后也可使用 `opennavicat` 命令。`scripts/run.sh` 与 `scripts/run.ps1`
提供快捷启动；首次运行且虚拟环境不存在时会创建环境并安装项目依赖。
项目提供公共 PyPI 依赖锁文件：安装 uv 后可用 `uv sync --frozen --extra dev`，
再用 `uv run opennavicat` 启动。当前 Python / Qt 版本的系统要求仍适用。

Linux 的桌面运行需要系统图形环境和 Qt 运行库。Ubuntu 24.04 示例：

```bash
sudo apt-get install libegl1 libopengl0 libxcb-cursor0 libxkbcommon-x11-0 libglib2.0-0t64 fonts-noto-cjk
```

## 工作流

0.2 默认跟随系统明暗主题。`--theme dark` / `--theme light` 可固定主题用于对照；
左侧搜索移至底部，数据库下按表/视图/查询/备份分组，中间“对象”页显示真实对象元数据，
右侧信息面板由“查看”开关控制。SQLite “有索引”列按独立索引计算，不把 rowid 主键
误当作独立索引。查询页使用消息/摘要/结果切换，连接窗口提供独立的异步测试连接。

实际参考步骤和当前渲染边界见 [原版界面观察](docs/ui-reference-observations.md)。
Linux 中文渲染使用系统 CJK 字体；构建环境提供 Noto Sans CJK，应用没有捆绑整套字体。

0.3 新增 PostgreSQL、MySQL、MariaDB 的 SSH 密码/私钥/组合身份验证、主机密钥确认、
压缩和实际 TCP 转发；TLS 支持自定义 CA、客户端证书/密钥、密钥口令、CRL 及模式选择。
SSH 转发后仍校验数据库原始主机名。首次未知 SSH 主机需要核对指纹；密钥变化拒绝连接。
这些连接能力与完整 Navicat 工具/UI 对齐是分别跟踪的验收范围。

- 创建并保存 SQLite、PostgreSQL、MySQL 或 MariaDB 连接，然后在连接树打开连接。
- 在连接树浏览数据库、schema、表和视图；打开表查看分页数据与结构信息。
- 新建 SQL 标签页，打开/保存 SQL 文件，执行语句或选区，查看独立结果集。
- 对有主键的表编辑记录、插入和删除，使用提交/回滚控制事务。
- SQLite 使用独立表设计标签创建或修改表，编辑字段、索引、外键、唯一键、触发器和选项，
  查看更改/DDL，再通过保存确认执行。其它三个已接入驱动目前提供只读结构和 SQL 建表入口。
- 导入 CSV / JSON 数据，导出当前结果；收藏查询并通过历史记录重新打开 SQL。
- 使用 SQLite 备份保留可独立打开的数据库快照。
- 点击 **模型** 从当前 schema 的真实表和外键生成关系图；可拖动、缩放，
  导出 PNG / SVG。关系图反映读取时的结构，调整布局不会修改数据库。
- 打开源表后使用 **其它 → 数据同步**，选择已连接的目标表，
  预览插入、修改和目标独有记录，再明确应用。目标独有记录默认保留；
  删除需要单独勾选。修改和删除检查原始行值以防覆盖并发更新，整批写入按一个事务提交。

界面提供可直接打开的示例 SQLite 工作区，便于在无需数据库服务的情况下体验。
结果集有行数上限，导出的内容以实际已加载的结果为准。表数据写入依赖主键定位；
无主键和视图应通过 SQL 显式处理。连接的实际权限由数据库服务器管理。
DDL 的完整程度取决于数据库方言；PostgreSQL 表定义由元数据重建，未包含所有者、
权限、存储参数和独立索引。远程驱动的索引/外键面板目前可查看元数据，结构修改通过 SQL 执行。
SQLite 表设计器保留未编辑的原始约束、CHECK、索引和触发器；复杂表达式/部分索引只读保留，
无法安全解释的结构拒绝修改。保存期间检查 schema 是否变化，重建失败整体回滚。
原版某些修改会留下旧表副本；当前实现采用原地 ALTER 或事务重建，这项行为差异仍在对齐清单中。
数据比较当前要求相同列名和主键，每表最多 10,000 行，超过上限直接拒绝比较；
MySQL/MariaDB 目标必须是 InnoDB。它同步预览中列出的行，不锁定源表，
也不会覆盖预览后被修改的无差异行。结构变化需要重新比较。

## 数据和密码

连接配置与查询收藏保存到当前操作系统的 Qt 应用数据目录，文件为
`workspace.json`；查询历史存于同目录的 `history.sqlite3`。
可通过 `OPENNAVICAT_DATA_DIR` 指向隔离目录，用于开发、测试或便携式数据存储。

密码默认只在本次进程中保留。启用保存密码后使用操作系统 keyring/keychain；
如果系统密钥服务不可用，不会自动退回明文文件。配置文件不保存数据库密码、SSH 密码、
SSH 私钥口令和 TLS 客户端密钥口令；各项有独立的保存选择。
SQL 历史和编辑器草稿会保留用户输入的 SQL；其中可能包含 SQL 字面量和业务数据。

CSV 使用 UTF-8 BOM，首行为字段名；`\N` 表示数据库 NULL，普通反斜杠使用
`\\` 转义。JSON 导出采用 `{"columns": [...], "rows": [...]}`，保留 SQL
重复列名和行顺序；BLOB 使用 `{"$binary": "base64..."}` 表示。导入要求唯一、
非空字段名和一致的行宽，也接受字段集合一致的 JSON 对象数组。CSV 值以文本
传入驱动，由目标列类型转换；需要精确保留二进制值时使用 JSON。

## 测试与构建

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python scripts/build.py --clean --archive
```

Windows 的测试可先执行 `$env:QT_QPA_PLATFORM = 'offscreen'`，再使用虚拟环境的
Python 执行同一命令。

PyInstaller 为**当前操作系统和架构**生成原生程序：

| 构建主机 | 可运行输出 | 分发文件 |
| --- | --- | --- |
| macOS | `dist/OpenNavicat.app` | `artifacts/OpenNavicat-<version>-darwin-<arch>.zip` |
| Windows | `dist/OpenNavicat/OpenNavicat.exe` | `artifacts/OpenNavicat-<version>-windows-<arch>.zip` |
| Linux | `dist/OpenNavicat/OpenNavicat` | `artifacts/OpenNavicat-<version>-linux-<arch>.zip` |

ZIP 用于构建调试。正式里程碑发布使用安装包：macOS `.dmg`、Windows `-setup.exe`、
Linux `.deb`，每个平台分别提供 amd64 和 arm64。安装包包含 Python、Qt、驱动和共享库。
当前构建不配置发布签名或公证。详细规则见 [发布约定](docs/release-policy.md)。

`.github/workflows/build.yml` 定义六个原生 OS/架构主机的测试、打包、安装、运行和卸载检查，
只有六份安装包和安全连接检查均通过时才发布 tag 对应的 Release。
**存在 CI 配置不代表这些平台已执行或通过验收**；
实际状态应以对应运行记录和目标机器上的启动结果为准。运行环境也受该版本 Qt
与驱动 wheel 的平台要求约束。

源码和打包程序均提供隔离的端到端 smoke：

```bash
.venv/bin/python -m opennavicat --smoke-test
# macOS 打包后
dist/OpenNavicat.app/Contents/MacOS/OpenNavicat --smoke-test
```

该命令创建临时示例数据库，通过真实后台连接运行查询，验证结果后退出。
它也实际加载 PostgreSQL、MySQL 和 SSH/加密依赖；`--smoke-report <path>` 保存运行时版本、
架构与驱动报告。Windows arm64 使用原生 libpq（通过 `scripts/build-libpq-windows.ps1`
构建）与 psycopg 的 Python 绑定，运行源码时需把 libpq DLL 目录放入 PATH 并设置
`OPENNAVICAT_LIBPQ_DIR`；该脚本也配置加密库的 OpenSSL 源码构建路径。安装包直接包含运行库。
`--demo` 则打开可交互的持久示例工作区。具体环境、测试数量、已构建产物及尚未验收项
见 [0.4 验证记录](docs/validation-0.4.md)（[0.3](docs/validation-0.3.md)、[0.2](docs/validation-0.2.md)、[0.1 历史记录](docs/validation.md)）。Linux 容器构建可执行 `scripts/build-linux.sh`，
它在独立环境中测试、打包并运行产物，输出到 `artifacts/linux/`。

README 演示可用 `.venv/bin/python scripts/record-demo.py` 重新录制，需要本机已有 ffmpeg。
脚本直接记录运行中 Qt 窗口的连续帧，临时样例库与 PNG 自动清理，仅输出一段 GIF 和不含个人路径的记录。

## 参考、实现与许可证

[Navicat 调查报告](docs/navicat-reference.md) 区分资源中的功能声明、重建设计和
尚未验证的行为。当前参考对象是本机 Navicat Premium 18.0.5 build 321；当前实现的行为
由 OpenNavicat 源码和测试决定。原始 REA 证据是本机诊断文件，已从源码分发忽略。

OpenNavicat 自有代码使用 [MIT License](LICENSE)。依赖分别使用其自身许可证，
见 [第三方声明](THIRD_PARTY_NOTICES.md)。项目与 Navicat / PremiumSoft 无隶属关系；
未将其程序代码、图标或美术资源作为应用资产使用。
