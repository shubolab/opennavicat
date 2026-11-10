# Navicat 18.0.5：静态参考与功能入口清单

调查日期：2026-11-10。用户恢复完整目标后，指定的新参考为
`/Users/wangshubo/Downloads/Navicat Premium.app`。
本稿记录这份已交付 macOS bundle 的静态声明，供后续实际运行对照和验收矩阵使用。
**没有执行参考程序、连接数据库、访问用户配置，或据此宣称任何功能和 UI 已通过验收。**
实际运行观察由另一条调查链记录；不能将下文的手册步骤当成已经点击执行的步骤。

## 身份与旧参考的区别

| 项目 | 旧参考 | 新参考 |
| --- | --- | --- |
| 精确位置 | `/Applications/Navicat Premium.app` | `/Users/wangshubo/Downloads/Navicat Premium.app` |
| plist 版本 / build | 16.1.1 / 51 | **18.0.5 / 321** |
| Bundle ID | `com.navicat.NavicatPremium` | `com.navicat.NavicatPremium` |
| 这份主程序的 file 结果 | Mach-O 64-bit executable x86_64 | Mach-O 64-bit executable arm64 |
| plist 最低系统声明 | macOS 10.14 | macOS 14.6 |
| plist SHA-256 | `42781a33fd75c3cbccfcedf2ab473e0a7d144bdb40b465a898466aa7bd3dc42e` | `3350c306fb699d0af18b1baf5de569640be232cd36106e80eb56370162d0f9b2` |
| 主程序 SHA-256 | `a3c99649690f43cb7224741a965f6471ce54fc0f1e26ba8adb5ea82357f38843` | `3857a354fde7f42f8afbf9ef3a25956e6ff3b9745f598f77bbab1d27e7493bd1` |

版本、最低系统和文件关联是 plist 声明；架构和主程序摘要是独立的只读文件检查。
REA 的资源会话仍使用 `executable_sha256: null`，没有把这份摘要伪装成 REA 已打开的
可执行目标或已绑定的原生分析快照。这份下载包为 ARM64，不能由此推断其它 Navicat
下载包、Windows 或 Linux 版本的架构支持。

新版 plist 新增 `.nmodel` 模型工作区、`.nbi` BI 工作区、`.nagg` 聚合管线关联，
保留 `.ncharts` 和部分 `.ndm2/.ndmc2/.ndml2`，实体快捷方式新增 `.nescsnowflake`。
文件关联不是文件兼容性证明：旧格式迁移、往返保存和外部文件导入仍需独立验证。

以下是**两份本地参考的可见静态差别**，不是某个功能首次发布于 v17 或 v18 的版本史：

| 差别 | 新参考的直接证据 | 对完整目标的影响 |
| --- | --- | --- |
| 数据库族扩大 | Snowflake、Redis 的连接/对象/安全管理手册和专用 NIB；旧包同名 NIB 集合没有这些模块 | 九个数据库族都应进入功能和驱动专项清单；云托管数据库不因云账号排除项而消失 |
| AI 工具面 | AI Assistant、附加 schema/tab、Ask AI、Fix Query、Fill with AI；新增 AI 设置和 schema 附件档案 | 保留实际 AI 工具功能和外部 provider 配置，不加入 Navicat 产品账号/订阅流程 |
| 数据分析与脱敏 | Data Profiling、Data Masking、Data Dictionary 的专用手册和档案 | 需要独立的分析结果、脱敏两种部署方式和数据字典向导，不能以 SQL 编辑器替代 |
| 模型工作区改组 | 模型、图、字典、命名标准、词汇表；Hybrid、Dimensional、Data Vault；批量编辑与 workspace 比较 | 当前只读 schema 图只是子能力；完整模型设计、正反向工程和同步继续开放 |
| Charts 扩展为 BI 工作区 | `.nbi`、数据源/图表/仪表盘/参数，实时或归档数据，数据源管线 | 旧版已有 Charts，不能把整个图表功能说成新出现；需要按新版完整工作区重新对照 |
| MongoDB 专用聚合管线 | `.nagg`、管线阶段编辑、分阶段预览、解释、执行和自动任务 | 与旧 Find/Aggregate Builder 分开跟踪，不能只做 JSON 查询输入框 |
| 主窗口/连接组织 | 信息与 AI 共享侧栏、连接管理器、连接星标/隐藏、导航过滤扩展、URI 连接 | 旧版主窗口和连接窗口截图不足以覆盖新版 UI |

数据生成、查询构建、同步、备份、自动运行等能力在旧参考已经存在，不能因为新版手册
合并、改名或 NIB 重组就当成新增或删除。静态档案缺失也不能证明运行时没有某项功能。

## 新参考声明的完整功能类别

下表是本次可建立的**功能类别覆盖清单**。来源为 bundle 内
`Contents/Resources/en.lproj/Navicat Help/`；英文相对文件名用于定位，中文对应手册也已
保存为忽略的调查材料。每类仍须展开实际菜单、对话框、驱动适用范围和成功/失败流程，
才可转为验收结论。手册中的版本/Edition 限制应作为参考适用性记录，不能将其变成重建品
的产品账号、激活或订阅系统。

| 类别 | 保留的能力与验收范围 | 本地手册入口 |
| --- | --- | --- |
| 连接与配置 | 九个数据库族、连接管理/星标/隐藏、URI、连接 profile、数据库选择、SSH/SSL/HTTP、原生认证和云数据库参数 | `connection.html`, `con_manage.html`, `con_profile.html`, `con_*_settings.html`, `share_uri.html` |
| 工作台和导航 | 主/对象工具栏、对象页、详细列表与 ER 图、导航分组/搜索/过滤、信息与 AI 栏、状态栏、多标签/窗口和工作区恢复 | `main_window.html`, `organization_customization.html`, `search_filter.html`, `favorites.html`, `hot_keys.html` |
| 数据库对象设计和维护 | 按驱动的数据库/schema、表/集合/视图、函数过程和其它对象；DDL、约束、维护、服务器版本差异 | `mysql_object.html`, `postgresql_object.html`, `oracle_object.html`, `sqlite_object.html`, `sqlserver_object.html`, `mongodb_object.html`, `snowflake_object.html`, `redis_object.html` |
| 关系型数据编辑 | 行增删改、单元格编辑器、筛选排序、分页和抓取、外键辅助、视图 profile、导出、NULL/BLOB/日期/JSON 等类型 | `data_rdbms.html`, `data_assistant_editors.html`, `data_filter_wizard.html` |
| MongoDB 数据和 schema | Grid/Tree/JSON 视图、集合字段结构、验证、普通/时序集合、Schema Analysis、索引和 GridFS | `data_mongodb.html`, `mongodb_object.html`, `schema_analysis.html` |
| Redis 数据工具 | 数据库编号、All Data、Grid/Tree、key 模式搜索、类型/值/TTL 编辑、分批抓取、嵌套数据展平 | `redis_object.html`, `data_redis.html` |
| 查询与执行 | 查询编辑、选择/当前语句执行、结果及消息、参数、代码片段/补全、Explain/可视化计划、SQL 格式化与取消 | `query.html`, `query_editor.html`, `query_results.html`, `query_explain.html`, `query_code_completion.html`, `query_code_snippet.html` |
| 可视查询和 Mongo 管线 | Query Builder、Find/Aggregate Builder、独立管线设计/阶段开关与顺序、输入输出预览、脚本导入、解释、导出、自动执行 | `query_builder.html`, `query_find_builder.html`, `agg_pipeline.html` |
| AI 辅助 | 多 provider、自然语言生成/解释/优化/修复、schema/tab 上下文、插入编辑器、比较回复、分叉聊天、建表/注释及填充数据预览应用 | `ai.html`, `ai_chat.html`, `query_ask_ai.html`, `query_fix_ai.html`, `data_fill_ai.html` |
| 数据质量分析 | 范围/过滤采样、列质量、分布、distinct/unique/NULL/empty/min/max 等统计、spotlight 与结果导航 | `data_profiling.html`, `data_profiling_2.html` |
| 数据生成 | 按字段生成、依赖与顺序、预览、部署和生成 profile；Generic/Personal/Business/Location/Payment/Computer/Product | `data_generate.html`, `data_generate_*.html` |
| 数据脱敏 | 转移时脱敏和原表脱敏；字段选择；Substitution/Variance/Hashing/Nulling/Shuffling；事务、批次、失败和保存 profile | `data_masking.html`, `data_masking_substitution.html`, `data_masking_variance.html`, `data_masking_others.html` |
| 数据字典 | server/database/schema 对象选择、对象顺序、模板、封面/目录/页眉页脚/纸张、输出和自动任务 profile | `data_dictionary.html` |
| 导入导出与文件执行 | 多文件、ODBC、格式/编码/分隔符/固定宽度、字段映射与结构、导入模式、批次、错误处理；对象/查询结果导出与 SQL 文件 | `import_wizard.html`, `export_wizard.html`, `dump_execute_sql.html` |
| 数据迁移/同步 | 跨对象与跨库 transfer、选项/预览/部署、data sync 比较/映射/应用及 profile | `data_transfer.html`, `data_sync.html` |
| 结构同步 | 驱动对象比较、DDL 差异、部署脚本和依赖处理、同步选项及 profile | `structure_sync.html` |
| 备份恢复和原生工具 | 关系库内置备份、SQL Server backup/restore、Redis backup/restore、MongoDump/Restore、MongoImport/Export、Oracle Data Pump | `build_in_backup_restore.html`, `backup_restore_sqlserver.html`, `build_in_backup_restore_redis.html`, `mongo_dump_restore.html`, `mongo_import_export.html`, `oracle_data_pump.html` |
| 自动运行与运维 | 批量任务、参数/历史/定时；console、server monitor、Redis command monitor 和 Pub/Sub 消息/归档/profile | `automation.html`, `console.html`, `server_monitor.html`, `command_monitor.html`, `pub_sub.html` |
| 调试 | Oracle PL/SQL、PostgreSQL PL/pgSQL 调试的断点、步进和上下文 | `debugger_oracle.html`, `debugger_postgresql.html` |
| 数据库安全管理 | 数据库用户、组、角色、成员、对象/服务器权限，Redis ACL、Snowflake account/database role；保留数据库认证 | `security_*.html`, `security_privilege_manager.html` |
| 模型 | Conceptual/Logical/Physical/Hybrid；Relational/Dimensional/Data Vault；对象、图、视觉元素、导入SQL、反向工程、正向工程、双向同步、转换、比较、批量编辑、词汇表和命名标准 | `model.html`, `model_model*.html`, `model_diagram*.html`, `model_compare.html`, `model_*_to_*.html`, `model_glossary.html`, `model_naming_standard.html` |
| BI | 跨源数据集、Live/Archive、查询和管线/连接、计算/映射字段、数据分析；20余种图、过滤/参数/主题、多页仪表盘、互动、展示/打印/导出 | `bi.html`, `bi_datasource*.html`, `bi_chart*.html`, `bi_dashboard*.html`, `bi_parameter.html`, `bi_theme.html` |
| 本地产品设置 | 主题/字体、导航/窗口/标签、网格/编辑器/结果、代码补全、环境、日志、自动恢复和文件位置 | `options_settings.html`, `log_files.html` |

Navicat Cloud、On-Prem 产品账号、项目团队同步、个人 profile 云同步、授权/订阅/注册及
产品账号登录界面属于用户排除项。上表的本地模型/BI/自动任务、云托管数据库连接及数据库
用户/角色不会因这个排除项被移除。AI provider 的连接设置也不是 Navicat 产品账号。
本次只读取这些字段的资源声明，没有读取任何真实 API key、密码或账号配置。

## 数据库族的专用对象边界

这些是各对象手册列举的对象族，必须进一步展开专用设计器和操作，而不能仅做一项连接驱动。

| 数据库 | 手册声明的对象或特殊行为 |
| --- | --- |
| MySQL / MariaDB | 数据库、表、视图、过程/函数、事件、表空间；MariaDB 10.3+ sequence；具体表选项按驱动/版本区分 |
| PostgreSQL | 数据库/schema、普通/外部/分区表、视图/物化视图、函数过程、类型、外部服务器、聚合、转换、domain、索引、operator/class、sequence、trigger、tablespace、cast、language |
| Oracle | schema、普通/外部/IOT表、视图/物化视图、函数过程、package、回收站、database link、索引、Java、物化视图日志、sequence、synonym、trigger、type、XML schema、directory、public对象、tablespace |
| SQLite | 数据库文件和附加数据库、表、视图、索引与触发器、维护；加密/SQLite版本和用户管理的适用性仍需实际确认 |
| SQL Server | database/schema、表/视图/函数过程、索引、synonym、trigger、backup device、linked server、server/database trigger、assembly、partition function/scheme；login 与数据库用户/角色分开 |
| MongoDB | database、普通/时序 collection、view、function、index、MapReduce、GridFS，以及独立 aggregation pipeline |
| Snowflake | database/schema、Standard/Hybrid/Dynamic table、view/materialized view、procedure/function、sequence、维护；用户、account role/database role、warehouse/namespace 等安全设置 |
| Redis | database编号与key值，不用关系表建模替代；key类型/TTL、嵌套数据、ACL的command/key/channel规则、Sentinel连接、Pub/Sub、command monitor及备份恢复 |

数据库身份验证仍需按驱动观察 PAM/Kerberos/LDAP、OS认证、Snowflake MFA/SSO 等具体条件。
专用参数来自连接档案和手册，不意味着已经建立任何该类真实连接。

## 下一轮实际 UI 观察的入口线索

下表左侧为本地手册声明的可见路径，右侧为档案交叉线索。全部仍需要实际窗口确认，尤其
不能由英文手册推断中文译名、图标位置、间距、窗口大小和动态启用状态。

| 应观察入口 | 直接线索与应捕获状态 |
| --- | --- |
| 主窗口 Connection → 选择服务器 → Next | 新的连接种类选择/编辑容器，不可继续假定旧版单个菜单直接进入全部设置；`ConnectionFormConnTypeSelectionModule`、`ConnectionFormEditConnectionModule` |
| My Connections 的管理入口 | 管理器即刻反映导航变化；建立/编辑/星标/隐藏/删除和按连接信息搜索；`ManageConnectionViewController` |
| View → Navigation Pane / Information Pane | 底部过滤栏、厂商/活动/星标过滤、定位活动对象、折叠与顶部布局；信息/AI共享右栏；MainWindow outlets 为 `connectionTreeView`、`mainSplitView`、`rightView`、`tabControlView`、`statusBarLabel` |
| Objects 页 Detail / ER Diagram | 详细列表列选择、表下字段/索引/FK、关系编辑/删除、颜色、布局、缩放、PNG和全屏；不能把只有只读图当成该入口完整实现 |
| 表中 Data Profiling | 扫描范围/过滤、Start、列质量与分布、统计/spotlight；`DataProfilingInformationModule`、`DataProfilingColumnStatisticsViewController` |
| Tools → Data Masking | 两个起始选项对应档案 outlets `_dataTransferRadioButton` 和 `_existingTableColumnRadioButton`；之后对象、字段、技术、预览、执行与profile |
| Tools → Data Dictionary | server/object选择、模板/版面、文件、结果；对应 `DataDictionary*Module` 和 `DataDictionaryWindowController` |
| AI Chat → Settings / Attach Schemas / Attach Tabs | 多provider、名称/endpoint/key/model、温度/token/行为/测试、聊天比较；设置档案有 `_configAPIHostTextField`、`_configAPIKeyTextField`、`_configTestConnectionButton`，没有读取其运行时值 |
| Snowflake 连接一般页 | warehouse、role、配置方式、datasource/host、MFA passcode、SSO URL；`ibMFAWithPasscode:` 是档案 selector，不证明方法体或实际认证成功 |
| Redis 连接一般页 / Sentinel | 普通与Sentinel的认证模式、host/port/user/password、保存密码和group；对应 `_sentinel*` outlets 与 `ConnectionFormSentinelsModule_redis` |
| Redis 连接右键 Command Monitor / 主工具栏 Pub/Sub | 开始/停止/清空/导出；channel/pattern/shard订阅、发布、暂停/恢复、颜色、消息详情/归档；仅在自建Redis上执行，不能用用户服务观察 |
| Mongo 主工具栏 Pipeline | source collection、stage图、编辑、输入输出预览、脚本预览、Explain/Run/Stop/Export；独立`.nagg`对象与自动任务 |
| 主工具栏 Model / BI | 两类都是含多个资产的workspace；列表/缩略图、搜索、属性和依赖、独立内部标签；`ModelWorkspaceDesignViewController`、`ChartsWorkspaceDesignViewController` |
| Query 的信息/摘要/结果与 Explain | 独立 `SQLResultInfoModule`、`SQLResultSummaryModule`、`QueryResultTabModule`、`QueryExplainVisualModule`，须观察无结果/多结果/错误/取消/忙碌状态 |

`MainMenu.nib` 与 `MainWindowController.nib` 中很多标签为空或运行时填充，存在菜单覆盖
outlet和selector；本次没有从空值推导“菜单不存在”。模型词汇表和命名标准手册注明
Hybrid Model适用；BI手册不列Redis，Data Profiling手册不列MongoDB/Redis，脱敏除MongoDB
外要求表有主键。后续应逐一核实这些适用性，而不是将所有按钮对所有驱动统一启用。

## 原始证据、派生步骤和限制

全部原始/派生调查材料位于被忽略的 `artifacts/reference-18-static/`，不会进入应用包，
也不应作为公共源代码资产提交。可提交的本稿仅为独立文字观察，不含原版图像或界面档案。

| 引用 | 证据 | 范围 |
| --- | --- | --- |
| R18-P | `ev_e6077971b8b9004970d4021add9bb985bd757fd8a99d1f1f6ac185ebb7d6ece8` | 原始新bundle的REA Info.plist；`rea-original-resources.json` |
| R18-IB | `ev_ddd39ec9e4846114cb43f4ecbe42d4e6c728523e7548138fe997552a780a80a4` | 原始根前64个档案的有界图，5,579节点/4,366边；不能覆盖整个应用 |
| R18-IB-S | `ev_58b49bd19212ac8bd01b27e365bc971c7e4f694ee7df595604e1b5a8f98fc8b2` | 上述REA概要，明确列出省略与未识别限制 |
| R18-F | `ev_f55e349a8a152e3a9f3b61f29f8c9e97d0458e40a5e127c0e131956eed3c10c2` | **派生focus.app**中29个定向档案图，5,657节点/5,032边；不是原bundle全图 |
| R18-F-S | `ev_916455b048d671f2898afc0142707e42629e820e6d330cf88a3c7852b9ffe87c` | 定向子集概要，99个complete/18个partial覆盖facet |
| R18-H | `help-text.json` | 145个英文和145个简体中文本地HTML页面的文本、链接和每文件SHA；是本地只读解析结果，不伪称REA Evidence |
| R18-I | `identity-comparison.json`, `resource-inventory.json`, `resource-name-diff.json`, `nib-basename-diff.json` | 两份bundle的文件身份/路径与资源名比较；未执行目标 |
| R18-S | `english-strings-derived.json` | 英语14个strings文件派生结果，`Localizable_Misc`为3,522项，`Localizable`为17项 |
| R18-C | `focus-copy-manifest.json`, `ui-entry-cues.json`, `focused-ui-nodes.json` | 29个原始NIB与派生副本的逐文件相同SHA链，以及档案outlet/selector线索 |

REA 6.8.0 的档案解码硬上限为64份文档/20,000对象/40,000连接。原根按发现顺序先覆盖
框架和字母序前段，遗漏大量目标窗口。因此先导出原根证据，再将29份指定档案和原Info.plist
逐字节复制到忽略目录的 `focus.app`，验证副本SHA相同，用相同资源工具解码并单独导出。
没有可执行文件，没有改变原bundle，没有将派生根当成原始完整应用。
定向子集仍有7个未能投影的层级连接和不支持的连接类型，`graph.truncated` 为true；它不能
证明运行时布局、像素几何、菜单可见性、动态对象或selector对应的方法实现。

新版部分 `.strings` 为带BOM的UTF-16字节，却在XML声明中写UTF-8，REA `inspect_plist`
明确拒绝。仅在内存中按实际BOM解码、将XML编码声明规范为UTF-8后，解析并保存派生JSON；
原路径/原SHA/源编码/规范步骤均被记录。没有重写原文件，也没有把该派生内容说成成功的REA
原样解析。资源名清单中，旧包许多en.lproj字符串在新版移到Base.lproj档案或Misc字符串；
这种包装重组不能作为功能删除证据。

会话已导出并 `close_binary`；随后 `binary_session` 确认 `open=false`、
`cleanup_pending=false`。没有启动深反编译器、调试器、运行时注入或原版UI操作。
Windows/Linux专用UI、驱动协议实测、新旧文件迁移、所有菜单状态和完整工具流程仍未解决，
需要后续独立证据。
