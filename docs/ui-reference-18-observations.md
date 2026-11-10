# Navicat 18 实际界面与行为观察

日期：2026-11-10，Asia/Shanghai。用户明确恢复目标并选择
`/Users/wangshubo/Downloads/Navicat Premium.app`。plist 为 **18.0.5 / build 321**。
本记录来自实际启动并操作这份应用的 CUA 观察，不用静态资源或旧16截图替代运行事实。
静态证据与覆盖限制另见 [reference-18-static.md](reference-18-static.md)。

只连接本任务先前创建的 `OpenNavicat UI Reference` SQLite 样例。
既有连接树中可见的其它配置没有打开、导入或复制；其名称和原始截图不进入本记录和发布资产。
演示数据库原有 customers、orders、products 三张表，均为生成的示例数据。

## N18-01：启动与主窗口

实际出现14天评估提示，点击“试用”进入。产品注册、购买、头像与云账号区域不重建。
启动介绍显示“数据脱敏”；查询首次打开的介绍显示“重构（重命名符号）”。介绍本身只证明入口声明，
未执行其中的数据脱敏或符号重命名功能。

工作区仍由左侧连接树、中间对象/多文档标签、右侧共享信息区组成。连接树底部为搜索与过滤控件。
主工具栏包含“BI”，右侧“查看”区可切换信息窗格和 AI 会话面板。
对已连接 SQLite 的实际工具栏顺序为：

连接 / 新建查询 / 表 / 视图 / 索引 / 触发器 / 用户 / 查询 / 备份 / 自动运行 / 模型 / BI。

数据库树实际展开为 表 / 视图 / 查询 / 备份。索引和触发器的主工具栏入口不意味着树中也有独立分类。
对象表列为名称、根页面、有索引、有触发器；点选对象更新右侧常规/DDL。
表页操作区实际有打开表、设计表、新建表、通过 AI 新建表、删除表、导入向导、导出向导、
刷新、详细信息、ER 图表和搜索。未执行 AI、导入导出及 ER 图外键编辑，不据此关闭门禁。

## N18-02：已有表设计器七个页面

点选 customers → 设计表，打开**独立主工作区标签**，不打开模态结构窗，也不替换数据标签。
右侧共享信息区仍显示该表的信息。顶部工具行随页面变化。

| 页面 | 实际控件与列 |
| --- | --- |
| 字段 | 保存、添加字段、插入字段、删除字段、主键属性、询问 AI、专注模式；名称、类型、大小、比例、不是 Null、键。类型为可编辑组合框，Null 为复选框，键显示顺序。 |
| 字段的底部属性 | 选中字段才显示；默认值、排序规则、不是 Null ON CONFLICT、生成类型、生成值。INTEGER 主键另出现自动递增复选框；适用性随选择变化。 |
| 索引 | 添加/删除索引；名称、字段、唯一键。字段单元格带选择按钮。 |
| 外键 | 添加/删除外键；名称、被引用的表、被引用的字段、本地字段、删除时、更新时、延迟。 |
| 唯一键 | 添加/删除唯一键；名称、字段、ON CONFLICT。customers 的 email 唯一约束出现在这里，索引页为空。 |
| 触发器 | 添加/删除触发器；名称、触发、插入、更新、删除；底部“定义”代码区与“高级”切换。 |
| 选项 | 主键 ON CONFLICT、自动递增数值、没有 RowID。当前样例自动递增数值为 -1 且禁用，不推断所有表的适用性。 |
| SQL 预览 | “更改”与“DDL”切换；没有结构修改时，更改页显示“无更改”。 |

索引的“字段”按钮打开小型“字段”对话框：字段、排序规则、排序顺序三列，
添加、移除、好、取消。添加行后，字段和排序规则为可编辑组合框，排序顺序为下拉框。
这是复合字段选择流程，不能用裸逗号文本框宣称已完整还原。

添加未保存索引后标题追加“— 已编辑”。曾创建空字段索引草稿，只观察预览，随后关闭，
在“是否保存更改？”的保存/不保存/取消中选择不保存，未执行该索引。

## N18-03：实际新建、保存与修改表

点击新建表，直接打开“无标题@main”的设计标签，字段页已有一个空字段行。
在此新建仅用于本次测试的 `reference18_designer_probe`：id INTEGER 主键、label TEXT。
首次保存出现 Sheet，包含表名称、只读 SQL 预览、“不再显示 SQL 预览”（默认未勾）、保存/取消。
名称为空时保存禁用，输入名称后预览同步更新。保存后通过独立 SQLite 连接读取 sqlite_master
与 table_xinfo，确认表和两个字段真实存在，不只依据成功提示。

随后将 label 重命名为 label_v18。原版更改预览实际生成：

```sql
ALTER TABLE "main"."reference18_designer_probe"
  RENAME TO "_reference18_designer_probe_old_20261110";
CREATE TABLE "main"."reference18_designer_probe" (
  "id" INTEGER NOT NULL,
  "label_v18" TEXT,
  PRIMARY KEY ("id" DESC)
);
INSERT INTO "main"."reference18_designer_probe" ("id", "label_v18")
  SELECT "id", "label" FROM "main"."_reference18_designer_probe_old_20261110";
```

点保存后还有一次只读 SQL 预览确认 Sheet，含保存/取消及同一预览偏好。实际执行后独立核实：
新表字段已改变，**旧表实际保留**。此例没有索引、外键或数据，不推断复杂表的迁移策略。
OpenNavicat 必须另行验证数据、依赖、事务和并发；安全的原地 ALTER 与原版保留旧表行为的差别
仍需明确记录，不能由相同截图宣称完全功能一致。

测试结束使用原版查询页执行两条 DROP，仅清理上述新建表与其旧表副本；两条消息均为 OK。
刷新对象页后恢复为 customers、orders、products，独立数据库复核确认无测试残留，
样例行数仍分别为6、36、6。

## N18-04：查询、运行和多结果

新建查询打开独立工作区标签，未运行时只有编辑区。顶部为保存、查询创建工具、美化 SQL、
代码段、询问 AI、结果在下/在旁切换、专注模式；第二行为连接、数据库、运行和解释。
右栏为常规、代码段、标识符切换。没有执行 AI 或向外部服务发送样例数据库。

实际执行：

```sql
SELECT name, city FROM customers ORDER BY id;
SELECT COUNT(*) AS customer_count FROM customers;
```

产生“消息 / 摘要 / 结果 1 / 结果 2”；默认显示结果1，真实样例为6条记录。
结果区工具有固定、单元格编辑器、数据分析、导出结果，“数据 / 信息”切换和网格/表单切换。
清理表的非查询语句执行后，结果标签消失，消息页记录逐条 SQL、OK 与查询时间。
关闭查询出现“是否保存对查询的更改？”保存/不保存/取消；选择不保存，仅丢弃本次草稿。

## N18-05：新连接管理器与数据库类别

点击连接打开独立“新建连接”窗口，不是旧版纯下拉菜单。包含新建/编辑连接切换、左侧连接类型与
供应商筛选、搜索、图标/列表视图、通过 URI 新建连接、取消、下一步。
实际在列表视图确认九类：MySQL、PostgreSQL、SQL Server、Oracle、SQLite、MariaDB、
MongoDB、Redis、Snowflake。以下14个供应商节点都已实际展开：

| 供应商 | 实际列出的别名 |
| --- | --- |
| Alibaba Cloud | ApsaraDB RDS for MySQL / PostgreSQL / SQL Server；ApsaraDB for MongoDB / Redis / OceanBase (MySQL Mode)；PolarDB for MySQL / PostgreSQL / Xscale |
| Huawei | Cloud RDS for MySQL / PostgreSQL / SQL Server；Cloud Document Database Service；Cloud Distributed Cache Service for Redis |
| Tencent Cloud | TencentDB for MySQL / PostgreSQL / SQL Server / MariaDB / MongoDB / Redis |
| Kingbase | KingbaseES |
| PingCAP | TiDB |
| HighGo | IvorySQL |
| OceanBase | Community Edition；Enterprise Edition (MySQL Mode) |
| AWS | Amazon RDS for MySQL / PostgreSQL / SQL Server / Oracle / MariaDB；Amazon Aurora for MySQL / PostgreSQL；Amazon Redshift；Amazon DocumentDB；Amazon ElastiCache for Redis |
| Fujitsu | Enterprise Postgres |
| Google Cloud | Cloud SQL for MySQL / PostgreSQL / SQL Server；Google Memorystore (Redis) |
| Microsoft | SQL Server；Azure SQL Database；Azure Database for MySQL / PostgreSQL / MariaDB；Azure Cosmos DB for MongoDB；Azure Cache for Redis；Microsoft Garnet |
| MongoDB Atlas | MongoDB Atlas |
| Oracle | MySQL；Oracle；Oracle Database Cloud Services；Oracle MySQL Cloud Service |
| Redis Enterprise Cloud | Redis Enterprise Cloud |

只枚举入口，没有连接这些服务。Redis 设置实际打开过：常规/高级/数据库/SSL/SSH/备注；
类型为单机、集群、哨兵。切为哨兵后出现哨兵、Sentinel SSL 页，常规页有哨兵主机、端口、
哨兵验证、组名称和组验证。未填写凭据、未测试连接、未保存，取消退出。

## N18-06：工具菜单

查询窗口实际展开“工具”：数据传输、数据生成、数据脱敏、数据字典、数据同步、结构同步、
命令列界面、服务器监控、在数据库或模式中查找、历史日志。
服务器监控子菜单列出 MySQL、PostgreSQL、Oracle、SQL Server、MariaDB、MongoDB、Redis、Snowflake。
菜单入口已运行观察，内部向导与真实协议行为仍须分别验收。

## N18-07：独立索引设计与延迟外键补充

点击SQLite主工具栏的索引，实际切换到独立索引对象列表。列为名称、唯一键、自动索引、
表名称、根页面；默认隐藏系统索引，并显示隐藏系统项目计数。选中 orders_customer_idx 后有
设计索引、新建索引、删除索引、刷新，右栏包含表名、字段及排序、根页面和自动索引信息。
设计索引打开独立主标签，常规/SQL预览两页，工具行为保存、添加字段、删除字段、专注模式。
常规页有表名称组合框、唯一键复选框，以及名称/排序规则/排序的字段表。只查看，未修改或保存该索引。

另在 orders 的表设计外键页，将 customer_id→customers.id 的“延迟”由未勾改为勾选，
SQL预览明确生成 **DEFERRABLE INITIALLY DEFERRED**。该预览还显示重建前删除显式索引、
重建后恢复索引。此操作只查看预览，关闭时选择不保存，没有重建 orders 或改变外键。

## N18-08：新建独立索引的保存 Sheet

新建索引打开未命名主标签，常规页初始表名称为空、唯一键关闭、字段表为空。
保存出现索引名称、SQL预览、“不要再显示”、保存和取消；未输入名称时保存不可用。
本轮AX操作虽使字段组合框显示city，但未成功提交单元格模型，SQL预览仍为空字段列表。
因此取消Sheet并不保存关闭草稿，没有执行索引DDL，也不把此操作计入原版新建索引验收。
现有orders_customer_idx未修改。

## N18-09：独立触发器入口

主工具栏触发器打开独立空列表，列为名称、类型、表名称、触发。
新建触发器打开常规、定义、SQL预览三个页面，工具栏提供保存和专注模式。
常规页实际看到触发器类型（Table / View）、表名称、触发、插入/删除/更新复选框，
更新字段列表及全选/取消全选，以及WHEN条件输入。没有实际展开“触发”的值列表，
没有进入定义页或保存；切换View的操作未完成，不能据此声称已观察INSTEAD OF界面行为。
关闭未修改草稿，数据库未产生任何触发器。

## N18-10：Redis 连接、数据库导航与键编辑器

本轮在指定的 18.0.5 build 321 参考程序中，新建自己的
`OpenNavicat Redis UI Reference` 连接，指向本机回环 `127.0.0.1:32779`，
认证选择 None；实际“测试连接”成功，返回 Redis **7.4.9**。
端点属于本轮自行创建的隔离 Redis 容器，数据为生成的示例；没有读取既有连接配置或业务数据库。

连接打开后实际显示 16 个逻辑数据库，各数据库名称旁标注键数量。
数据库 0 下为“数据 / 查询 / 备份”类别；数据类别的对象列表只有 `(所有数据)`。
双击后打开 `(所有数据) @ 0 …` 独立主标签，页面为上方键列表、下方编辑器的垂直分隔，
工作台的全局信息栏仍位于右侧。

- 键列表列为“键 / 类型 / 值摘要 / 大小 / TTL”。
- 第一行工具包括保存、展平、编辑器、网格/树切换、专注模式。
- 第二行包括“所有类型”过滤与“键包含”搜索；底部有添加/删除、上次刷新、刷新与加载控件。
- 实际双击一个 string 键后，下方出现键名、类型、值的代码编辑区、TTL、应用与放弃。
  因为没有修改值，应用/放弃处于不可用状态。
- TTL 弹出项实际显示“无 TTL / 过期时间（秒）/ 本地时间”。后两项的输入操作没有完成，
  没有应用 TTL 变更。

Redis 上下文的主工具栏实际为“连接 / 新建查询 / CLI / 数据 / 用户 / PubSub / 查询 /
备份 / 自动运行 / 模型 / BI”。这里记录的是可见入口；PubSub、CLI、用户权限等没有执行。

本轮只读取隔离容器中已有的 7 个示例键，没有在原版界面写入数据。
未成功切换并完成 hash、list、stream 等其它类型的编辑流程，因此这份观察不构成
六类数据类型的编辑验收，也不构成 TTL、集合写入、冲突处理、集群或哨兵功能验收。

## 证据边界

本轮只操作 macOS 18.0.5、自己的 SQLite 数据库，以及自行创建的隔离 Redis 配置和示例键。
不是九驱动连接验收，也不是 Windows/Linux 对应版本的 UI 对照。
原版图标、程序、界面档案和带既有连接树的截图不作为 OpenNavicat 资产分发。
README 演示按用户最新要求使用一段 OpenNavicat 自身实际运行 GIF，全部为隔离样例数据。
完整目标仍由 [parity-matrix.md](parity-matrix.md) 管理，任何未执行行为保持开放。
