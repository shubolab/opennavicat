# 0.1.0 验证记录

日期：2026-11-10（Asia/Shanghai）。这是可运行核心版本的验证记录；
完整 Navicat Premium 能力和 Windows 原生验收仍在目标范围内，尚未完成。

## 平台与产物

| 环境 | 源码测试 | 源码应用 smoke | 打包应用 smoke | 状态 |
| --- | --- | --- | --- | --- |
| macOS 27.2 / ARM64 / Python 3.13.10 / Qt 6.12.0 | 97 passed, 1 skipped | offscreen 与真实 Cocoa 启动通过 | offscreen 与真实 Cocoa 启动通过 | 本机原生 `.app` 已生成 |
| Debian GNU/Linux 13.7 / aarch64 / glibc 2.41 / Python 3.13.18 / Qt 6.12.0 | 97 passed, 1 skipped | offscreen 通过 | offscreen 通过 | 容器中的真实 Linux 进程，非 Linux 桌面人工验收 |
| Windows | 尚未执行 | 尚未执行 | 尚未执行 | 同源运行脚本和 CI 已提供，不能声称 Windows 已通过 |

macOS 产物：`artifacts/OpenNavicat-0.1.0-darwin-arm64.zip`，49,623,174 字节。
SHA-256：`7fd1d966418218227407def41d6e904e6de126a4e947c77419b0a7aa627fdd7d`。
应用常规文件合计 126,298,272 字节；ZIP 保留 122 个符号链接及可执行权限。
未包含 WebEngine。构建时应用源码摘要与当前源码逐项比对一致。

Linux 产物：`artifacts/linux/OpenNavicat-0.1.0-linux-aarch64.zip`，103,794,854 字节。
SHA-256：`7f900985455fccdcee52b513aba17f6f9cb0ebbcd6aa4fd37265c82b07881488`。
应用常规文件合计 183,827,276 字节，ZIP 中主程序保留可执行权限，未包含 WebEngine。
最终 Linux 源码快照、macOS 构建摘要与当前应用源码均一致。

本机最终 `.app` 已在真实 macOS 桌面打开到持久示例工作区，并通过系统辅助功能
操作实际点击 Run。界面显示 `Result 1 · 6 rows`、4 列查询数据及已完成状态。
应用保留打开，供用户直接体验。该验证只操作应用自行创建的示例库。

原生应用路径为 `dist/OpenNavicat.app`。此版本未配置 Developer ID 发布签名和公证；
本机成功启动与面向外部机器发布是不同的验收阶段。Linux 构建环境也不代表
其他发行版、较旧 glibc、x86_64 或 Wayland 桌面已经验收。

本机诊断产物（不纳入源码或运行依赖）：

- `artifacts/mac-build.log`、`mac-build-metrics.json`、`mac-packaged-smoke.log`。
- `artifacts/packaged-native-workbench.png`：真实 macOS 平台打包程序界面。
- `artifacts/linux/build-linux.log`、`pytest-results.xml`、`source-manifest.json`。
- `artifacts/schema-diagram.png` / `.svg`：真实样例 schema 的原生关系图渲染。

## 数据库覆盖

隔离的 SQLite 临时文件、PostgreSQL 15、MySQL 8.0.36、MariaDB 11.4.13。
测试仅使用为本次任务创建的一次性数据库；没有读取现有 Navicat 配置、密码或生产库。
测试完成后移除了本次创建的数据库容器。

完整命令（端口只对应本次一次性 fixture，重跑需重新准备）：

```bash
QT_QPA_PLATFORM=offscreen \
OPENNAVICAT_TEST_POSTGRES_PORT=55439 \
OPENNAVICAT_TEST_MYSQL_PORT=53369 \
OPENNAVICAT_TEST_MARIADB_PORT=53379 \
.venv/bin/python -m pytest -q
```

测试覆盖：

- schema、表/视图、字段、组合主键、索引和外键的真实元数据。
- SQL 脚本、多个结果、选区执行、失败前已完成语句的结果保留、事务和回滚。
- 原生 Qt 连接树、表格实际编辑器提交、后台写入、独立连接回读和重启恢复。
- 长查询取消、界面保持可操作、取消后的连接复用和运行中标签保护。
- 有主键的编辑/删除冲突检测、批量写入失败回滚、只读及 TLS 部分场景。
- CSV NULL/空串/反斜杠/Unicode，JSON 对象/数组/标量/二进制，精确字段映射。
- SQLite、PostgreSQL、MySQL、MariaDB 全部 12 个有序跨驱动数据比较/应用方向，
  外加 MariaDB 同驱动；包含 Decimal、JSON、BLOB/bytea、日期及 NULL。
- 跨驱动同步后重复比较无差异，日期主键在 SQLite 文本与 PostgreSQL 日期对象间正确匹配，
  小数比较保留高精度差别而不过度更新等价数值。
- 差异预览无写入，删除默认关闭，外部修改/新增冲突、目标配置和结构变化拒绝应用。
- 关系图使用真实外键，鼠标拖动更新连线，PNG 像素和 SVG 结构可用。

1 项跳过是 PostgreSQL TLS 专项：该临时 PostgreSQL 实例没有启用 TLS。
MySQL TLS 要求与证书校验行为已执行测试。密码持久化采用 mock keychain 验证文件
不落明文；三平台原生密钥服务的锁定/授权交互仍需要专门验收。

## 当前边界

- Windows 实际运行、Linux 桌面会话、干净机器安装、签名和升级尚未验收。
- PostgreSQL 表 DDL 是元数据重建；不包含完整 ownership/grants/storage/独立索引。
- SQL 返回行数有限制；PostgreSQL libpq 普通游标仍可能缓存服务器完整结果。
- 数据比较上限是每表 10,000 行，要求相同列名和主键；不负责自动类型/排序规则迁移。
  SQLite NUMERIC 亲和性可能降低高精度 decimal 的精度。自引用、唯一值交换和复杂
  约束顺序可能导致整批回滚，而不是自动规划替代顺序。
- 比较应用只检查实际更新/删除记录的原始值，不覆盖预览后变化的无差异行；
  不声明对两端整个数据库提供跨连接一致性快照。
- SSH、Oracle/SQL Server/MongoDB、完整结构同步、可编辑模型、任务计划及原生服务端
  备份恢复等能力尚未完成，详见功能路线图。

使用的 Navicat 参考事实及 Evidence ID 记录在 `navicat-reference.md`，静态资源观察
不构成本应用行为或完整兼容性的验证。
