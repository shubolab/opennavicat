# 本机 Navicat 实际界面观察

2026-11-10，Asia/Shanghai。参考对象为 `/Applications/Navicat Premium.app`，
静态版本身份仍见 `navicat-reference.md` 的 REA Evidence。本文记录的是本轮实际打开
应用后的运行观察，不能与此前只读取资源的调查混同。

## 固定范围与样例

用户明确要求 1:1 UI 与完整数据库工具，排除产品账号、登录、订阅和授权体系。
数据库认证、数据库用户/权限以及云数据库连接保留。

只创建了一个用于本任务的连接 **OpenNavicat UI Reference**，指向
`artifacts/ui-reference/sample-workspace.sqlite3`。样例包含 customers、products、orders
三张表及 order_summary 视图，不包含用户业务数据。参考端本轮仅执行 SELECT 和
元数据浏览，没有打开既有服务器连接，也没有输入或保存数据库密码。

参考端使用 CUA 原生 UI 接口直接读取窗口和截图。原始截图出现在本轮工具记录中；
未将含既有连接名称的整窗截图复制到项目或分发包。下面记录可复做的步骤和实际发现，
没有把当前源码截图当作参考截图。

## 实际走过的状态

| 观察 | 原版步骤 | 观察到的界面 |
| --- | --- | --- |
| L1 主窗口 | 启动 Navicat，保持既有连接关闭 | 上方大图标工具栏；左连接树，底部搜索；中间对象标签；可选右侧信息窗格。 |
| L2 驱动菜单 | 点击“连接” | MySQL、PostgreSQL、Oracle、SQLite、SQL Server、MariaDB、MongoDB；下方有云数据库别名子菜单。 |
| L3 SQLite 连接 | 连接 → SQLite，创建本任务样例连接 | 可切换的 Main Profile 左栏；常规/高级/数据库/HTTP；现有、新建 SQLite 3、新建 SQLite 2 三个单选项；保存密码独立行；底部测试/取消/保存。 |
| L4 对象列表 | 双击样例连接 → 双击 main | 导航为表/视图/查询/备份分类；表列表列为名、根页面、有索引、有触发器。customers/ orders 有实际索引，products 的 INTEGER PRIMARY KEY 不被计作独立索引。 |
| L5 数据表 | 双击 customers | 数据单独标签；顶部事务/编辑器/筛选/排序/列/导入/导出/生成/图表工具；左侧实际行复选框，密集网格，底部记录操作与分页。 |
| L6 SQL | 新建查询，输入 `SELECT id, name, email, city FROM customers;`，点击运行 | 查询工具条和连接/数据库/运行行分开；未运行时编辑器占满区域；成功后信息/摘要/结果 1 居中切换，返回六行。 |
| L7 连接安全页 | 新建 PostgreSQL，逐个查看常规/SSL/SSH，随后取消 | 常规含主机、5432、postgres 初始库和用户；SSL 有模式、客户端 key/cert、根证书、撤销列表；SSH 有主机/22/用户/密码或公钥/压缩。没有执行网络连接。 |
| L8 信息窗格 | 查询页点击显示信息；对象页选中 customers | 约 200 logical px 的右栏，常规/DDL 或查询相关切换，真实连接、数据库、对象和元数据。 |
| L9 结构设计 | 对象页选中 customers → 设计表 | 独立设计标签；保存/添加/插入/删除/主键工具；字段/索引/外键/唯一键/检查/触发器/选项/SQL 预览；字段列含名、类型、大小、比例、不是 null、键。 |
| L10 SSL 详细状态 | 再次新建 PostgreSQL → SSL → 使用 SSL → 模式 → 使用验证 | SSL 页同样有连接示意图；未勾选时模式与证书区禁用。模式依次为允许（allow）、prefer、require、verify-ca、verify-full；“使用验证”单独控制客户端密钥、客户端证书、根证书、证书撤消列表四个文件项。仅查看设置，未发起连接。 |
| L11 SSH 详细状态 | 同一未保存 PostgreSQL 连接 → SSH → 使用 SSH 隧道 → 逐项切换验证方法，随后取消整个连接窗 | 启用后示意图为客户端 → SSH Server → 数据库；主机、22、用户名、验证方法。方法为密码、公钥、密码和公钥。密码有独立“保存密码”；公钥为私钥文件、通行短语、独立“保存通行短语”；组合方式同时显示两组。底部“使用压缩”。未输入秘密、未保存网络连接。 |
| L12 SQLite 设计页详细状态 | 回到本任务 customers 设计标签，仅选中字段并切换所有设计页 | 见下节字段、索引、外键、唯一键、检查、触发器和选项的实际列/属性。没有修改或保存 schema。 |
| L13 云数据库菜单清单 | 点击主工具栏“连接”，读取所有子菜单 | 下节归档完整实际名称；这些是数据库连接入口，不属于排除的产品账号系统。没有打开既有连接。 |

本轮参考主窗口 JPEG 的 SOF 尺寸实际解码为 **3024 × 1898 pixels**；
在本机 2× 显示密度下对应约 **1512 × 949 logical px 的完整外框**。
这与 Qt `QWidget.grab()` 的客户区 PNG 范围不同。连接窗同样按外框尺寸核对，
不能把标题栏算作可编辑内容区。

## 已纳入 0.2 的重建

主工具栏分组、SQLite 驱动下函数入口可见性、导航分类、对象列表、信息栏、
查询双工具行、信息/摘要/结果页、连接窗结构、系统明暗配色、密集表格和实际行选择，
已替换 0.1 的品牌欢迎页、深蓝侧栏和自定义操作布局。

这些是重建进展，**没有关闭完整 UI 或功能对齐门禁**。仍存在的明确差距包括完整设计器、
向导、多驱动专用对象、各项未实现工具、图标细节、其它视觉状态与三平台实际对照。
0.2 中 SQLite 2、SSH、HTTP 等项保持未实现状态，不能以控件存在代替行为。
0.3 已开始实现并验证 SSH 和完整证书文件设置；SQLite 2、HTTP 及其它开放项仍在完整清单中。

## 当前源码的可复查输出

运行 `.venv/bin/python scripts/capture_ui.py --theme dark`，可以使用同一个样例库渲染
当前实现的未连接、对象、数据表、运行前查询、结果、信息窗格和两类连接窗。
这些输出仅是 OpenNavicat 自身的实际控件渲染，用于与上述参考端观察比较。

- `artifacts/ui-reference/current/*.png`：客户区截图，不包含 macOS 原生标题栏。
- `artifacts/ui-reference/current/metrics.json`：同时列出 client/frame、DPR、侧栏和工具条尺寸。
- 原生截图中测得侧栏 240 logical px，查询工具行分别 36 / 34 logical px；
  结果有六行、四列，结果切换条在结果区居中。
- 已知当前主外框为 1512 × 948 logical px，与参考外框仍相差约 1 logical px；
  当前没有声称像素级验收完成。

完整功能和三平台要求持续见 `parity-matrix.md`，不因本轮 UI 工作缩小目标。

## L12：SQLite 现有表设计的实际字段

以下是同一任务样例库的运行观察，不能直接推广到其它驱动。

| 设计页 | 实际列或属性 |
| --- | --- |
| 字段 | 名、类型、大小、比例、不是 null、键；选中普通 TEXT 字段后，下方出现默认值、排序规则、不是 null ON CONFLICT、Generated Type、Generated Value。 |
| 索引 | 名、字段、唯一键；独立添加/删除索引和保存。customers 的 email UNIQUE 位于唯一键页，因此本页索引数为 0。 |
| 外键 | 名、被引用的表（父）、被引用的字段、本地字段、删除时、更新时、延迟。 |
| 唯一键 | 名、字段、ON CONFLICT。样例 email 字段显示 `"email" ASC`。 |
| 检查 | 名、表达式。 |
| 触发器 | 名、触发、事件、列、给每个；下方有 Definition/Advanced 切换及代码编辑器。 |
| 选项 | 主键 ON CONFLICT、自动递增（本表为禁用的 -1）、没有 RowID。 |
| SQL 预览 | 独立代码区域和保存菜单。 |

这些表面仍需要可编辑、保存、失败回滚、数据与依赖保留等真实行为，现有只读结构页没有达到这一要求。

## L13：实际云数据库连接菜单

| 分组 | 本机参考实际菜单项 |
| --- | --- |
| Amazon AWS | Amazon Redshift；Amazon Aurora for MySQL；Amazon Aurora for PostgreSQL；Amazon RDS for MySQL / PostgreSQL / Oracle / SQL Server / MariaDB；Amazon DocumentDB。 |
| Google Cloud | Google Cloud SQL for MySQL / PostgreSQL / SQL Server。 |
| Oracle Cloud | Oracle Database Cloud Services；Oracle MySQL Cloud Service。 |
| Microsoft Azure | Microsoft Azure SQL Database；Microsoft Azure Database for MySQL / PostgreSQL / MariaDB。 |
| MongoDB Atlas | MongoDB Atlas。 |
| Alibaba Cloud | OceanBase Community Edition；Alibaba Cloud ApsaraDB for RDS (MySQL) / (PostgreSQL) / (SQL Server) / (MongoDB)。 |
| Tencent Cloud | Tencent Cloud TencentDB for MySQL / PostgreSQL / SQL Server / MariaDB / MongoDB。 |
| Huawei Cloud | Huawei Cloud RDS for MySQL / PostgreSQL / SQL Server；Huawei Cloud Document Database Service。 |

这里保留的是安装版本的界面声明，不能将菜单存在或兼容协议直接当作云端连接验证。
