# 0.6 候选：MongoDB 文档、原生命令、GridFS 与安全验证

记录日期：2026-11-10，Asia/Shanghai。**0.6 当前是开发候选，尚未发布。首轮六目标原生构建、安装、运行、卸载均通过；随后发现的问题已经修补，最终源码正在重新执行发布门禁。公开下载核验须在发布后进行。**
本文件记录 `work/mongodb-adapter` 开发分支上已经取得的有界证据，不把组件测试、源码 smoke 或参考程序观察当作最终安装包验收。
源码版本已升为 **0.6.0**，对应源码 smoke 已实际返回该版本；冻结发布源码、原生安装包和公开发布记录仍待补齐。

开发 checkpoint 包括 `288beb1`（BSON 文档适配器及工作区）、`8bf794c`（连接设置及 URI）、
`586a0e3`（GridFS 页面）、`02765ae`（传输、元数据与 GridFS 后端）、`f519876`（上传元数据日期精度）和
`84b45d3`（真实 MongoDB CI 门禁）。这些 checkpoint 不代表最终发布源码已冻结。
0.5 的历史证据见 [0.5 验证记录](validation-0.5.md)，不替代本轮新增 MongoDB 路径的验证。

## 参考来源与适用范围

参考为用户指定的 Navicat Premium **18.0.5 build 321**。
MongoDB 实际观察只使用任务自建的 MongoDB 8.0.34 与合成数据，包含连接表单、树分类、集合网格/树/JSON、
文档验证与插入、查询、聚合工具、索引设计，以及 GridFS 上传、图像预览、下载和删除。
逐项操作和未执行项见 [MongoDB 18 版运行观察](ui-reference-18-mongodb.md)。
静态入口线索见 [18 版静态参考](reference-18-static.md)；未观察的行为不由文件名或静态资源推定为已验收。

原版文档编辑器和 JSON 视图使用 `ObjectId`、`Long`、`ISODate`、`Decimal128`、`Binary.createFromBase64` 等 JavaScript 构造式。
当前文档编辑器明确标为 **Canonical Extended JSON**，不能据此声称已经实现完整 mongosh JavaScript 或原版 JSON 编辑语言。
数据库认证及数据库工具继续属于完整范围；排除项仍只包括产品账号、产品登录/注册、激活、订阅及云团队账号系统。

## 当前实现与数据保持边界

- 连接设置提供 Standalone、无验证及密码 SCRAM 默认协商/SCRAM-SHA-1/SCRAM-SHA-256、认证库、TLS 和 SSH。
  General 的认证字段与用户名密码位于同一个表单，默认端口 27017、SSL 关闭。
  SRV、副本集、分片、未接入认证方式和非 primary 读取偏好保留明确状态，不静默降级。
  URI 只在内存中解析，保留认证库和 TLS 验证语义；未知、重复或无法准确表达的选项拒绝。
  组合 PEM 的 URI 参数仍保留接入门禁，不能把设置窗口已验证的独立证书/密钥路径能力泛化为任意 URI 支持。
- 集合和视图进入专用 BSON 工作区，默认网格，可切换树与只读 Extended JSON 展示。
  原始 BSON、精确类型和编辑前像独立保留。替换/删除由服务端条件写和受签封 token 绑定会话、命名空间及集合 UUID；
  不信任界面可变的字典作为原始前像。无法证明无损往返的类型、投影结果和视图保持只读，并提供原始 BSON 导出。
  单文档写入不被描述成多文档原子批处理。
- 查询页按 MongoDB 原生命令协议执行受支持的 BSON 命令和 shell 语法片段，关闭 SQL 格式化、SQL 构建器及 SQL EXPLAIN。
  类型可无损表示时返回 Canonical Extended JSON，否则使用明确的原始 BSON 表达，不通过 `default=str` 抹掉类型。
  完整 mongosh、可视 Find/Aggregate Builder 和独立 Pipeline 工作区仍有开放门禁。
- GridFS 使用专用主工作区、九列文件元数据、名称筛选、分页、进度与后台传输。
  上传创建新文件版本，流式计算 MD5 和 MIME，成功写入数据块后才发布文件记录；不会按同名覆盖既有文件。
  下载经过同目录临时文件、完整核对与原子提交，默认禁止覆盖已有目标，并清理远端文件名中的目录成分。
  删除按文件元数据前像检查后执行 GridFS 生命周期；standalone 的文件记录和数据块删除不是多文档事务，清理失败必须明确报告。
  取消使用单次传输的独立标记，等待当前有界 I/O 返回后检查并清理，不用全连接取消误杀其它任务。
- GridFS 预览仅支持有界图像，临时下载和解码都在后台，临时目录仅属于本次操作。
  当前保守限制为文件长度小于 **1,000,000 bytes**，并限制解码尺寸；原版精确“1 MB”边界尚未实测。
  文本等非图像提示保存到磁盘。外部应用“打开文件”仍明确禁用，不能算作已完成。
- 主窗口按 MongoDB 切换树分类、工具栏和八列集合统计列表，关系型对象列表恢复五列。
  索引从“其它”动态入口访问；专用集合/索引设计器等未完成工具不伪装成 SQL 表设计。
  文档和 GridFS 页面不使用通用 SQL 表导入/导出；MongoClient 关闭经后台串行 owner 完成。

## 已取得的开发期测试证据

以下运行范围与时点不同，且相互重叠，**不得相加为一次完整套件结果**。
Qt 专项在本机 macOS/ARM64、Qt offscreen 上运行，不构成 Windows/Linux 界面或打包程序验收。
服务端使用自有、只绑定 loopback 的 Docker MongoDB **8.0.34 / arm64**，不使用现存用户连接或业务数据。
本轮客户端为 Python **3.13.10**、Qt **6.12.0**、PyMongo **4.19.0**；相关安全依赖为
Paramiko **5.1.0**、cryptography **50.0.2** 和 pyOpenSSL **26.4.0**。
SSH 使用每客户端原生 pool/TLS context 适配；初期 SOCKS 参数路线已由真实测试否定并移除，不依赖 PySocks。

| 实际运行范围 | 结果 | 证据及边界 |
| --- | ---: | --- |
| 独立审查修补后源码全量 | 870 passed，231 skipped，0 failed，41.66 秒，`-Werror` | 显式启用自建 MongoDB 协议夹具，包含新增录制器与键盘回归；其它未启用端点、平台和安全专项保留跳过；`artifacts/mongodb/source-reviewed-final.xml` |
| 首轮整合源码 checkpoint | 819 passed，231 skipped，0 failed，39.77 秒，`-Werror` | 后续审查前的历史范围；`artifacts/mongodb/source-final.xml` |
| 正式 MongoDB 协议 runner | 131 passed，0 skipped，`-Werror` | 101 后端、18 原生命令、12 MainWindow；3 个本次新建原生容器、认证就绪及最终删除均核验；`artifacts/security-integration/mongodb-protocol-final/summary.json` |
| MongoDB 后端、文档页面、GridFS 联跑 | 138 passed，0 skipped，`-Werror` | 100 项后端、15 项文档页面、23 项 GridFS；`artifacts/mongodb/backend-browser-gridfs-results.xml` |
| TLS、双向 TLS、SCRAM 与 SSH 专项 | 22 passed，0 skipped，`-Werror` | 真正服务与协议，不替换加密/网络为 mock；`artifacts/mongodb-security/final/pytest-results.xml`、`summary.json`、`final-verification.json` |
| MongoDB 设置/URI 与既有连接窗口回归 | 152 passed，`-Werror` | 52 项新增 MongoDB 与 100 项既有设置/管理器；`artifacts/mongodb/connection-settings.log`；没有自动连接或读取用户凭据 |
| GridFS 页面最终专项 | 23 passed，0 skipped，`-Werror` | 19 项受控 Qt 与 4 项真实服务；包含在上述 138 项内；`artifacts/mongodb/gridfs-ui-results.xml` |
| 实际 MainWindow MongoDB 集成 | 8 passed，0 skipped，`-Werror` | 工厂、导航、类型、视图、保存后离开、原生命令和导出失败保护；`artifacts/mongodb/mainwindow-complete.xml` |
| GridFS 主窗口补充路由 | 1 passed，0 skipped，`-Werror` | 独立补跑，不等于重新完成全部 MainWindow 套件；`artifacts/mongodb/gridfs-window.xml` |
| 原生命令与 MainWindow 定向联跑 | 24 passed，0 skipped，`-Werror` | 16 项原生命令及上述 8 项 MainWindow；`artifacts/mongodb/query-mainwindow-results.xml`；与后续 18 项原生命令及独立修补复核重叠 |
| 后续根集成专项 | 123 passed，0 skipped，`-Werror` | 18 项原生命令、10 项 MainWindow、39 项安装合同、50 项发布门禁、6 项查询协议；`artifacts/mongodb/root-integration-final.xml`；仍非完整源码套件 |
| 主窗口审查问题修补与独立复核 | 根回归 2 passed；独立复核 2 passed，`-Werror` | 分别为 `artifacts/mongodb/window-review-fixes.xml`、`window-independent-review-fixed.xml`；两个同范围复跑，不相加 |
| 设置、文档、GridFS 联合回归 | 190 passed，0 skipped，`-Werror` | 包含前述设置、文档和 GridFS 范围，属于重叠证据；`artifacts/mongodb/mongo-ui-settings-results.xml` |

这些回执位于本机忽略的 `artifacts/`，没有把连接名称、主机、密码、证书私钥或原版用户树截图纳入公共资料。
测试失败、setup 错误和修补前的日志保留为过程证据，不计作通过。

### 类型、冲突与文件结果核验

真实文档测试核对 Int64、Decimal128、Binary、日期、嵌套数组/字段等类型和 BSON 字节保持，
以及同值不同类型、字段顺序、并发更新、集合 drop/recreate 后旧 token 等冲突情形。
文档页的 Save/Discard/Cancel、保存失败保留草稿、保存后关闭/断开/退出由 Qt 与独立服务查询核验。
原始 BSON 导出还注入了最终原子替换失败，确认旧的完整目标保留且本次临时文件清理。

GridFS 真实页面测试完成 PNG 上传后的 `image/png` 与实际 MD5 核对、图像预览、下载逐字节一致、
删除后文件记录与 chunks 均消失、stale 文件元数据拒绝删除，以及已有下载文件保持不变。
实际多块上传在首块后请求取消，确认未发布文件记录且仅清理本次新文件的 chunks，原有文件和数据块保留。
受控 Qt 测试覆盖超过 32 位的进度计数、排队传输取消不影响前一查询、页面销毁后的迟到响应，
以及写入完成至排队刷新之间保持 busy，避免把旧文件列表暴露为已经刷新的结果。

页面测试仅操作随机生成的 `onv_ui_UUID` 集合和 `onv_gridfs_UUID` 存储桶。
早期一次异常 teardown 留下的独有 GridFS 桶已按其精确测试文件和 marker 识别、清理，
最终 `artifacts/mongodb/gridfs-ui-cleanup.json` 记录剩余页面测试集合为 0；原参考桶未删除。

### 安全专项的有效边界

22 项安全验证使用两台独立的自有 TLS/mTLS 测试容器和真正 Paramiko SSH peer/TCP 转发，验证：

- require、verify-ca、verify-full 的实际连接和查询，verify-ca/full 下错误 CA、默认不信任 CA 和明文拒绝。
- 原始主机名/SAN 保留：verify-ca 与 verify-full 区分主机名策略，SSH 转发不会把待验证名称改成 loopback。
- 加密客户端私钥、缺失或吊销的客户端证书、实际服务器证书 CRL、错误密钥密码的类型化失败及临时 PEM 清理。
- SSH 密码/公钥、未知 pin 在明确批准前不发认证、pin 不匹配与真实 peer 换钥拒绝，以及拒绝连接后的清理和重连。

这是 **SCRAM 密码认证加传输层双向 TLS** 的证明，不是 MONGODB-X509、LDAP、Kerberos 或所有数据库认证方式已实现。
安全报告保留运行时源码摘要；后续查询/GridFS 仍有源码变化，最终冻结版本须重新完成适用回归。
安全测试容器与临时证书目录已核验移除，原参考容器身份未变，见 `final-verification.json`。

## 当前审查与源码 smoke

原生命令独立 wire 审查发现的三个具体问题已经修补并重新核验：
后续语句的无效参数在执行首个写入前拒绝；批量部分写入如实报告 affected 和 partial；
普通原生游标的 getMore 不再发送不适用的 maxTimeMS，并保持同一逻辑会话。
`artifacts/mongodb-query-review/focused-recheck.json` 记录同一源码摘要
`1e5768494afa54eccba932e04320e89f21333a988c791a566d0d3c79578943fb`
下五项独立复核全部通过：预检后写入为 0、部分写入数为 2、getMore 同会话、截断时清理游标，以及 typed comment 跨 getMore 保持。
复核期间源码未变化，自有数据库与容器已清理。

另在独有单成员 primary 中实际触发写入已发生但 write concern 未满足：写入 1 项，
界面/协议层取得 1 项部分结果及 affected=1，并明确报告“确认不确定，刷新后再决定重试”，
见 `artifacts/mongodb-query-review/write-concern.json`。
该服务仅用于产生真实确认失败，不构成 Replica Set 发现、故障转移或拓扑设置已验收。
修补前 `artifacts/mongodb/query-wire.xml` 的失败仍保留为历史，不计作成功；
上述三项已不再列为未修问题，但最终冻结源码的完整回归与更广协议范围继续开放。
有界 shell 语法和 BSON 命令支持仍不能表述成完整 mongosh 引擎。

第二轮独立审查又发现并修补了脚本取消与未确认写入问题。一次脚本从解析到逐条执行共用一个取消生命周期，
保留每条命令的独立 typed comment。解析期间取消不再开始首条写入，两条命令之间取消不再执行后续写入；
已确认的先前结果继续保留，最后一条已经完整返回的成功不会被事后的取消伪装为回滚。
命令级 `writeConcern:{w:0}` 和无效确认方式在整段脚本的首次数据库 I/O 前拒绝，
插入文档里的同名业务字段不受影响。完整查询文件 38 项及既有真实服务取消通道复核均通过，
见 `artifacts/review-b493e9e/lifecycle.xml`、`operation.xml`。

JSON 编辑与 `EJSON.parse` 拒绝把溢出的有限数字静默转换为 Infinity；显式 Extended JSON 非有限值仍可用。
有界 shell 对尚未实现的数字字符串转义明确拒绝，不再删除反斜线而写入不同内容。独立精度回归 20 项通过。
连接树同时补齐 Return/数字键盘 Enter 激活，由真实 SQLite 连接和表数据的两项 Qt 回归核验；
这些自动化验证不替代原生窗口的人工界面验收。

独立 MainWindow 复核曾实际发现两项界面生命周期问题：全局刷新把索引上下文切回集合，
以及后台关闭连接期间还能排入旧会话元数据读取，导致成功断开后出现“未连接”错误。
根实现已保留当前对象类别，并为关闭中的连接添加导航、读取及回调门禁。
根确定性回归 2 项及独立 fresh-fixture 正确性复跑 2 项均通过：索引类别、Other 文案和五列布局刷新后保持，
关闭期间不排入新的元数据任务、不执行 after-connect 回调，完成后没有错误且会话已移除。
原复现为 `artifacts/mongodb/window-independent-review.json`，修补后为
`window-independent-review-fixed.json` / `.xml`；用于证实原问题的探针不计作产品成功用例。
这两项新增回归令 MainWindow 文件包含 12 项，并已在上列正式协议 runner 中全部实际执行。
之前 10 项的回执保留原始边界，不改写历史计数。

源码 `--smoke-test` 已在隔离工作区返回 6 行查询，运行时包含
`mongodb_adapter_constructed=true`、`mongodb_bson_probe=true`、`mongodb_tls_context_ready=true`，
同时保留此前 SQL Server、Redis 和实际 OpenSSL cipher 探针。
版本提升后又实际执行源码 smoke，报告为 `artifacts/mongodb/source-0.6-runtime-smoke.json`，版本字段为 **0.6.0**。
早期 `source-runtime-smoke.json` 的 0.5.0 记录仍作为历史保留；版本提升后的安装/发布合同专项另有 89 项通过。
这个 smoke 实际构造 MongoDB 适配器、检查 BSON 与 TLS context，但没有连接外部 MongoDB；
不替代真实协议专项，也不证明最终安装程序包含正确的原生依赖。

## 本轮原生发布与完整目标门禁

首轮 `b493e9e` 的 [CI 42824442618](https://github.com/shubolab/opennavicat/actions/runs/42824442618)
六个原生目标均通过：每目标 1,050 项收集，macOS/Linux 754 passed、296 skipped，
Windows 753 passed、297 skipped；冻结程序的 MongoDB 适配器、BSON 原生扩展及 TLS context 探针均为布尔 true。
这证明该 checkpoint 的六包，不将其冒充为后续修补源码的最终产物。

独立 macOS ARM64 0.6.0 冻结开发包另实际完成 Cocoa 启动、SQLite 6×4 smoke、67 个原生映像与 provider 检查，
源码输入核验没有 drift。人工实际打开主窗口及九类型连接管理器后退出，未覆盖旧版本实例。
连接树操作受到 Qt 原生 AX 错误阻挡，因而**本轮没有声称手工完成冻结程序的 MongoDB/GridFS 数据页验收**。
相关小型回执在 `artifacts/mongodb/local-native/`；本次独有 GUI 容器已核验删除。

初次数据库专项中 MongoDB 8.0.34 的容器在认证就绪前退出。保留退出状态和服务日志后，
第二轮 [CI 42826666852](https://github.com/shubolab/opennavicat/actions/runs/42826666852)
证实是服务器拒绝当前 runner 内核，退出码 1、OOMKilled=false，非适配器断言失败。
[MongoDB 官方说明](https://www.mongodb.com/docs/v8.0/release-notes/8.0/) 记录 TCMalloc 对特定内核的限制，
[SERVER-131779](https://jira.mongodb.org/browse/SERVER-131779) 记录 Ubuntu 回移修复内核的版本识别问题。
数据库专项单独改用 Ubuntu 22.04 runner 及其对应运行库，保留 MongoDB 8.0、全部专项和零跳过要求；
六份桌面安装包的目标平台不变，没有禁用服务器检查或改写用户内核。
修补后的最终原生报告仍须核对实际内核回执和测试结果。

| 本轮最终目标 | 原生构建及安装/运行/卸载 | 公开资产下载核验 |
| --- | --- | --- |
| Windows amd64 | 首轮通过；修补后待核验 | 待发布后 |
| Windows arm64 | 首轮通过；修补后待核验 | 待发布后 |
| macOS amd64 | 首轮通过；修补后待核验 | 待发布后 |
| macOS arm64 | 首轮通过；修补后待核验 | 待发布后 |
| Linux amd64 | 首轮通过；修补后待核验 | 待发布后 |
| Linux arm64 | 首轮通过；修补后待核验 | 待发布后 |

最终六份安装报告须由对应原生架构的实际打包程序产生，核对原生映像、动态依赖、BSON 与 TLS 探针，
并实际安装、启动、运行和卸载。发布器继续要求六安装包、manifest、摘要及公开下载字节核验。
只改 CI/打包字段、导入客户端成功或默认跳过外部服务的测试均不能替代这些步骤。

MongoDB SRV/Replica Set/Shard Cluster、非 primary 读取、全部认证方式、集合及索引专用设计器、
时序集合完整选项、函数、MapReduce、独立聚合管道、完整 mongosh、GridFS 外部打开和更多恢复/规模边界仍开放。
原版的 BSON JavaScript 编辑语言、全部视图与三平台逐项 UI 对照也未完成。
其它驱动、AI、Profiling/Masking/Dictionary、完整导入导出/同步、备份/自动任务、模型和 BI 等继续按
[完整对齐矩阵](parity-matrix.md)及[路线图](roadmap.md)推进；本轮候选与未来六安装包不缩减完整目标。
