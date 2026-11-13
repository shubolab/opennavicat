# 0.11 候选验证记录

日期：2026-11-13，Asia/Shanghai。**0.11.0 尚未发布，最终源码、六目标 CI、正式 tag 和公开资产验收均待完成。**
当前公开安装器仍为 [v0.10.0](https://github.com/shubolab/opennavicat/releases/tag/v0.10.0)。
本文记录已经执行的局部验证及其归属，不能用这些结果预先替代最终候选的原生安装器门禁。

## 候选范围与源码归属

本轮从远端 `main` **`c5f4c7467f8d3296ba282bd43168a05089b987ad`** 恢复，
随后提交 **`3bc020f`** 修复管线脚本、预览安全和连接树导航。
Batch模型/运行层已提交为`9a8ce1a`，Qt、Store/历史与主窗口整合为`f45b7e4`。
包版本元数据已调整为 **0.11.0**，最终候选还包含本版实际GIF和录制扩展；六目标按后续冻结SHA独立验证。

0.10 公开源码之后已经进入 main 的集合行内索引、结构化排序规则/152项 locale、集合选项、
可视管线、`.nagg`、Options 和视觉 Explain 工作片段也属于本候选的继承范围。
它们不因出现在 main 或候选中就成为完整工具、全部服务版本或三平台参考验收。

本轮新增或修正的工作如下：

- 聚合脚本正确形成 `aggregate(stageArray, options)` 两个参数，包含零阶段情况。
  通过已验证的 BSON 构造器保持 Int32、Long、Decimal128 等值及 typed `let`，
  不再把 Canonical EJSON 包装对象误作普通 JavaScript 文档；无法安全表示的值明确报错并保留草稿。
- 预览在任何源集合读取前拒绝启用的 change-stream 阶段，避免把行数上限误当成等待时间上限；
  禁用的流阶段不阻止普通管线。预览拒绝写入阶段的既有门禁继续保留。
- 恢复连接树中已经实现的聚合管道类别；实际点击列表可打开已保存对象。
  简化编辑器不能无损投影的内容继续使用原始编辑器，保存动作安全显示脚本错误。
- 加入全局自动运行列表和 `常规 / 高级 / 消息日志 / 历史` 四页 Batch 工作区。
  16种参考工作类别保持可见，目前可执行范围是**已保存的 MongoDB 聚合管线引用**。
  支持重复引用、本地保存/重开、错误继续或跳过、独立会话执行、取消及每份批处理作业独立编号的本地历史。

实际参考契约见 [AU18-01–AU18-06](ui-reference-18-automation.md)，实现与未完成边界见
[Batch 记录](batch-jobs.md)。这些是 Navicat Premium **18.0.5 build 321** / macOS、
自有 MongoDB **8.0.34** 的运行观察，不是静态 REA Evidence ID 或三平台完整验收。

## Batch 文件与执行边界

本地模型支持参考的 `Version: 1.4`、`General` 和有序 `Jobs`。
非空 Jobs 使用数组并保留重复；新空任务或移除最后一项后使用观察到的 `{}`。
导入的空对象和空数组均可往返，未知非秘密字段、缺失字段和原有 Hash 保留。
新 UUID/Hash 仅为本地身份，符合已观察的字形；未复现或验证参考 Hash 算法，未宣称任意文件逐字节一致。

格式检查覆盖 UTF-8、重复键、已知字段类型、有限数字，以及 **1 MiB / 深度64 / 100,000节点 / 1,000引用** 上限。
非空凭据类字段（包括 `EmailServerPassword`）被明确拒绝，不静默擦除或退回明文存储。
SMTP 和 OS 计划仍未接通；保留其非秘密元数据不表示可以执行。

一个管线任务按连接名、MongoDB 驱动、数据库和已保存管线名精确解析。
改名、缺失、歧义或不同驱动均失败，不借稳定内部 ID 自动跟随改名。
Start 准备阶段读取**最新保存**的 `.nagg` 和已复核连接配置，冻结为一次运行的副本；
不以未保存编辑器文本代替，也不在运行中重新读取 Store 或 Qt 控件。
并发修改的全部参考语义仍待观察。

每项尝试创建自己的数据库会话，顺序连接、执行并在 `finally` 关闭，不借用交互工作区会话。
相同引用两次执行不会去重。ContinueOnError=false 在失败后跳过后续任务；true 继续。
整批跨任务执行不是事务，也不自动重试或回滚已经确认或确认未知的数据库效果。
已保存的 `$out` / `$merge` 在主窗口开始前仍需写入确认；本轮三项真实 Batch Window 用例验证的是读任务，
不能据此增加该写入路径的真实服务器验收声明。

取消使用运行专属控制、当前会话的精确 operation marker 和主窗口的**独立取消 worker**。
尚未开始的任务跳过；执行中取消可以得到 cancelled 状态。
如果执行已正常返回才观察到取消，该任务仍记成功并保留 `execution_completed / cancel_requested` 事实，
后续任务跳过，整次运行标为取消，不能暗示已完成写入被撤销。
AU18 尚未核实原版 Batch 的取消动作；当前取消合同是已测试的安全实现，参考 UI/行为对齐门禁仍开放。

事件和历史只保留有界状态、时间、任务编号、受限名称及安全错误分类/服务器 code，
不复制结果文档、SQL 正文、配置、凭据、端点、个人路径或完整异常 argv。
启用历史时，每个本地 job 保留独立执行编号和最近200条记录；关闭历史不影响现场日志。
历史写入失败保留已完成结果供显式重试保存；重试只补未落盘历史，不重跑数据库任务。
提交后读取历史失败也不得把同一次执行重复写入。

## 已执行的验证

以下范围重叠，**不能相加成另一轮全源**。离线组件测试不等于实际数据库执行，工作树专项也不替代最终冻结源码。

| 范围 | 已执行结果 | 有效边界 |
| --- | --- | --- |
| `3bc020f` 管线修复 | 模型/脚本 **41**、Qt **21**、真实主窗口树路由 **1**，各范围零跳过 | 包含真实原生 JS 执行生成脚本并逐字节核对 aggregate 参数 BSON；VM 中的合成 `db` 只证明语法/参数，不声称原生 JS host 已支持全部 aggregate Options。 |
| Batch 格式/引用模型 | **83 passed，零跳过** | 纯数据格式、未知字段、secret拒绝、有序编辑、精确引用、最新保存快照及空 Jobs 形状。 |
| Batch 串行运行层 | **18 passed，零跳过** | 假会话及真实慢子进程的独立取消/回收；包括已完成后取消、所有失败路径清理和安全日志，未访问数据库。 |
| Batch Qt / Controller | Qt **18**、Controller **8**；合跑 **26 passed，零跳过** | 控件、信号、主窗口协调、隐藏筛选选择、删除确认及历史失败保留等组件合同；不是26个原版完整工作流。 |
| Store/历史混合专项 | 新增 Store 范围 **4**；与既有15项合跑曾为 **19 passed** | 本地状态/历史行为及重叠回归，不能计成19个新增功能。 |
| 前序离线组合 | **141 passed，零跳过** | 当时的模型/运行/Qt/Store与18项生命周期范围；不含后加的8项Controller，也不是最终源码全源。 |
| 真实 Batch MainWindow | **3 passed，零跳过，7.22秒** | 一份全新、认证、loopback、原生 ARM64 MongoDB 8.0.34 fixture；单独完整 ID 检查确认已不存在。 |
| Batch整合工作树全源 | **1986 passed / 566条件跳过，零失败/错误，81.60秒** | 测量早于最终0.11元数据及下一轮录制扩展；条件跳过不算真实服务，不能写成最终冻结源码资格。 |
| 本轮完整集合 runner | **458 passed / 0 skipped，零失败/错误，52.91秒** | 8份新 exact-owned、原生ARM64 MongoDB 8.0.34 fixture；完整ID独立检查均不存在，runner所列源码哈希匹配该次工作树。 |
| 本版 GIF | **已完成录制和隐私审核** | 28阶段、371帧、61.84秒，详见末节；六安装器与正式发布仍待完成。 |

三项真实主窗口用例分别验证：

1. 保存有效管线后断开交互 MongoDB；全局自动运行仍可新建、保存并执行两次相同引用。
   两个独立会话关闭，交互 session 与连接树保持 Off；通过列表双击重开 job，历史可从新 Store 回读。
2. 同名保存管线改为合法 BSON、服务器拒绝的谓词后，下一 Run 使用新保存内容。
   Continue=true 两失败，false 一失败/一跳过；管线改名后旧引用得到 LocateObject 失败且不创建数据库会话。
   两个 job 的历史编号分别从1开始。
3. 自有服务中的只读长管线进入精确数据库/aggregate/currentOp 后触发 Stop，
   取消线程既非 GUI 也非执行 owner；当前任务取消、后续跳过，精确 operation 消失。
   随后替换为有效保存管线再 Run，两次成功且没有遗留取消。

三项均由独立客户端/独立 executor 比较6份原始 BSON 文档，确认没有修改。
私有回执为 `artifacts/batch-mainwindow/verification.json` 和 `results.xml`，记录当时9个源码/测试文件的 SHA256。
该次测量早于后续 Controller/历史修正，**不冒充最终工作树或发布源码的精确哈希资格**。
其它 ignored JUnit 包括 `artifacts/batch-model-runtime-results.xml`；这些私有证据不会随 Git clone 自动恢复或装入安装包。

Batch整合全源证据为 `artifacts/resume-20261113/batch-full-source.xml`，
完整集合 runner 为 `artifacts/resume-20261113/batch-collections/summary.json`，
独立逐ID清理核验为 `artifacts/resume-20261113/batch-independent-cleanup.json`。
458项 runner 包含与组件和三项Window专项重叠的范围，不能再相加；其全部执行与8份fixture证据
属于该次工作树，而非之后修改的recorder、0.11版本元数据或尚不存在的最终CI/tag。

## 0.11 发布门禁与剩余范围

Native Cocoa和新单GIF的实际结果见末节；最终冻结SHA与对应的原生安装器资格仍待CI补齐。
版本元数据、发布说明和实际演示必须与最终里程碑一致；旧的25阶段演示不充当新增 Batch 功能实录。

六原生目标 **Windows/Linux/macOS × amd64/arm64** 的首次预检已完成，具体归属及未通过发布门禁的原因见下节；
修复后最终源码的构建、包内 smoke、真实安装/运行/卸载和独立资产审查仍需重新取得。
Windows EXE、Linux DEB、macOS DMG 各两架构全部就绪并核对摘要后才可正式发布。
随后仍需核对 `SHA256SUMS`、`build-manifests.json` 和八个公开资产的实际匿名下载字节。
现有分发尚无发布者签名或 Apple 公证；Windows ARM64 安装引导器为x86，应用及原生依赖仍须为ARM64，不能用模拟x64替代。

当前只覆盖16种 Batch 类别中的已保存 MongoDB Pipeline 片段。
其余15类执行、完整 copy/group/rename/文件管理、全部参数、依赖、Scheduler、SMTP/附件、
退出后执行、离线与重启恢复、持续 change stream、其它拓扑/版本和三平台参考布局继续开放。
完整数据库工具和1:1保留 UI 的其余门禁见 [parity matrix](parity-matrix.md)；六安装器成功也不能替代这些验收。
产品账号/授权/云账号协作的既定排除不影响数据库认证、数据库用户/角色/权限、云数据库连接和独立AI工具范围。
完成本里程碑后完整 goal 继续推进，不自动暂停或宣称完成。

## 首次本机工作树与唯一GIF

在0.11.0元数据下，本机全源 **2011 passed / 567条件跳过**，零失败/错误，79.24秒；
精确集合/批任务runner **460 passed / zero skips**，53.23秒。八个原生ARM64认证loopback MongoDB8.0.34
fixture均逐完整ID独立确认不存在，48项所列源文件摘要与该次执行匹配。
原生Cocoa范围 **51 passed / zero skips**，10.64秒，包含组件和4条真实主窗口路径。
录制专项 **60 passed / zero skips**；不同范围重叠，不相加。
本机回执位于 `artifacts/release-011/{final-source.xml,final-collections/,cocoa-batch-pipeline.xml}`；
这仍是最终提交前的工作树测量，六目标安装器资格须按冻结SHA另行取得。

唯一真实Cocoa GIF已完成：28阶段、371帧、61.84秒、1,631,603字节，SHA256
`5f46313db93fbc3e1bd9b9cc6ffb8a0d89cda748b10c23e434ebf223582b2b4e`。
逐阶段编码帧视觉检查及逐帧可见文字guard均通过，仅本轮生成的隔离数据；没有原版截图、真实连接、
主机、个人路径、密码或业务数据。新增三段实际Batch常规/消息/持久历史；两个相同已保存引用通过
两个独立生产Session真实执行，独立客户端核验六份精确BSON和集合UUID未变，原主窗口会话可继续读。
临时服务与工作区均清理，路径和服务身份未进入公开receipt。

原版AP18-10另取得单节点副本集的有限change-stream运行和独立Stop证据；这些是参考观察，
当前实现仍不执行change-stream阶段，不能把本轮安全拒绝或wire观察当成功能完成。

## 首次六目标预检及执行预算修复

冻结源码 `4111d3310e22ac6e42d72de93fa1a93e4f975118` 的非tag预检
[43362158137](https://github.com/shubolab/opennavicat/actions/runs/43362158137) 为 **7/7成功**：
六个原生桌面目标及真库安全专项均成功，发布job按规则跳过。
本轮只核对运行状态，未下载或独立审查其安装器字节；这轮结果不作为后续修复源码的发布资格。

随后自有 MongoDB8.0.34、实际生产 MongoSession、公开 CommandListener 和 system.profile
共同确认：PyMongo4.19.0 的 CSOT 将用户设置的 Run/Preview `maxTimeMS=1/10` 改成了约119999；
Explain 外层约29999，原设置仍留在内层，无法证明内层时限在该服务器生效。
因此保留这次绿色CI与失败复现，**不发布4111d33**，先修复再重新冻结验收。

修复为正预算使用最多两个专属惰性客户端，复用已解析的TLS/SSH端点和临时证书，
保留有限socket、连接和选服时限；执行异常和Session.close关闭这些客户端。
Explain把正 `maxTimeMS` 放到外层命令。Run/Preview采用120秒、Explain采用30秒的协作截止，
剩余不足一个整毫秒时拒绝发出命令，避免把正限制舍入为服务器的无限制0。
这不是精确的整段CPU/墙钟抢占保证，也不组合任意调用方更短的外层隐式CSOT。
显式0和未设置仍沿用原路径，不能描述为服务器零毫秒超时。

修复后的独立15-case wire/profile回执确认 Run/Preview/Explain 的正1/10/5000均原样进入命令；
Explain内层不再携带该正限制。1毫秒真实出现服务器code50，且之后可重新运行。
两条专属客户端真实TLS/SSH用例另验证5000毫秒命令、verify-full原主机名及mTLS，
客户端在tunnel和临时PEM清理前全部关闭。上述仍是专项证据，最终源码与六目标结果另行记录。
私有回执分别为 `artifacts/pipeline-maxtime-wire-20261113/receipt.json`（原失败）、
`artifacts/pipeline-maxtime-fixed-wire-20261113/receipt.json` 和
`artifacts/pipeline-budget-security-final/verification.json`；各次精确fixture均已核对清理。

## 预算和启动取消修复后的本机冻结检查

进一步用真实MainWindow复现preview/run/explain三路启动竞态：worker已通过最初取消检查，
但尚未建立session操作标记时Stop，旧实现仍实际发出aggregate/explain。
现在同一次任务的独立Event贯穿三个入口及Batch runtime，冷连接完成后重新检查取消和剩余预算，
预览阶段之间也保留请求。主窗口按任务token在独立cancel_pool重试精确marker，
每任务最多一个在途取消；worker完成Event阻止GUI清理尚未送达时误抓下一任务标记。
三项真实红例已转绿；另有currentOp首次空结果后的重试、并发数量和迟到取消隔离测试。

最终本机测量如下，范围重叠，不相加：

| 范围 | 结果 | 证据归属 |
| --- | --- | --- |
| 默认全源 | **2051 passed / 590条件跳过**，78.60秒 | `artifacts/release-011/budget-final-source.xml`；跳过不计为真库验收。 |
| 完整集合/管线/Batch runner | **498 passed / 0 skipped**，52.10秒 | `artifacts/release-011/budget-final-collections-r2/summary.json`；48条精确声明目标、8个认证原生ARM64 MongoDB8.0.34 fixture，48项源码哈希匹配。 |
| 原生Cocoa组件与真实Window路径 | **56 passed / 0 skipped**，13.77秒 | `artifacts/release-011/budget-final-cocoa.xml`；包含三路启动取消及两条timer合同，不能代替三平台参考UI。 |
| 完整MongoDB TLS/SSH | **24 passed / 0 skipped**，27.87秒 | `artifacts/pipeline-budget-security-full/verification.json`；专属客户端真实mTLS/SSH原主机名和关闭顺序。 |
| 后端预算/取消 scoped | **210 passed / 0 skipped**，11.67秒 | `artifacts/pipeline-budget-cancel-event-final/verification.json`；与上列重叠。 |
| Batch runtime | **19 passed / 0 skipped** | `artifacts/pipeline-budget-batch-runtime-final.xml`；含调用前检查之后、标记建立之前的独立Event回归。 |
| 源Cocoa smoke | **passed，6行×4列** | `artifacts/release-011/budget-final-source-smoke.json`；0.11.0及驱动/加密/native-JS探针。 |

八个runner服务均逐完整容器ID独立确认不存在；独立回执
`artifacts/release-011/budget-final-independent-cleanup.json`也确认48项所列源码摘要匹配。
runner首次声明无参数化后缀的新增case被严格清单检查拒绝，未进入数据库执行；
保留 `budget-final-collections/` 失败记录，改为preview/run/explain三个明确case后在新r2目录通过，未弱化校验。
本轮执行预算/取消修复没有改变所示控件或演示工作流，保留前述已审查的28阶段唯一真实GIF。
新六目标CI、安装器独立字节审查、正式tag和公开下载资格仍须绑定后续冻结SHA，不能借用4111d33结果。
