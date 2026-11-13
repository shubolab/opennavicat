# 2026-11-13 远端进展核对与继续入口

完整 goal 已由用户恢复为 active。继续独立原生 Qt、完整 Navicat 数据库工具和
Windows/Linux/macOS 的 1:1 保留 UI；本次同步、既有 Release 或下一个里程碑均不表示完成。
全部门禁继续以 [parity matrix](../parity-matrix.md) 为准。

## 已核对的恢复基线

- 远端：`git@github.com:shubolab/opennavicat.git`。
- `origin/main`：**`c5f4c7467f8d3296ba282bd43168a05089b987ad`**，提交为
  `Add OpenNavicat goal resume snapshot`；本轮新分支从该提交开始。
- 当前工作树：`/Users/wangshubo/.codex/worktrees/release-0-10/opennavicat`，
  分支 `codex_mongodb_pipeline_tasks`。目录名沿用已有受管工作树，不表示仍停在0.10发布候选源码。
- 原主目录 `/Users/wangshubo/develop/opennavicat` 继续保留；未重置、清理或丢弃其它分支及未合并提交。
- 本机参考 `Navicat Premium.app` 的 plist 本轮重新核对为 **18.0.5 build 321**。
  Windows/Linux 仍需各自实际参考验收，不能由这份 macOS 身份替代。

此前 [11月12日 goal 交接](2026-11-12-goal-resume.md) 已包含远超过早期存储候选的实现。
旧 [state](2026-11-12-state.json) 和 [progress](2026-11-12-progress.md) 累积了多个时点：
其中 `ci_current=003c486`、旧候选 next steps、旧演示数据，以及发布后仍保留的
`public_release_created=false` 均不能作为当前状态。新的机器索引是
[2026-11-13-state.json](2026-11-13-state.json)；旧文件保留历史，不把其重叠测试数量相加。

## 公开版本与当前 main 分别核实

最新公开版本仍为 [v0.10.0](https://github.com/shubolab/opennavicat/releases/tag/v0.10.0)，
本轮 GitHub 元数据核对为非 draft、prerelease，发布于 **2026-11-12 08:59:08（北京时间）**。
六安装器、`SHA256SUMS` 和 `build-manifests.json` 共八资产均为 uploaded，大小和 digest 元数据可读。
本轮没有重新下载八资产，不能把此次元数据核对表述为重新完成匿名下载或字节校验；
既有公开字节审计见 [0.10正式记录](../validation-0.10.md#010-正式-tag-与公开资产审计)。

| 对象 | 精确源码与运行 | 本轮直接核对结果 |
| --- | --- | --- |
| 正式 `v0.10.0` | `711a458a873a96034f865d36f46d4919fce39359`；[43129758054](https://github.com/shubolab/opennavicat/actions/runs/43129758054) | completed/success；六桌面、安全专项和发布共8个job全部成功。 |
| 当前恢复的 `main` | `c5f4c7467f8d3296ba282bd43168a05089b987ad`；[43214308990](https://github.com/shubolab/opennavicat/actions/runs/43214308990) | completed/success；六桌面和安全专项7个job成功，发布按非tag规则skipped。 |

当前 main 的成功 CI 不代表已公开下一版本；本轮也未重新下载或独立审计这轮 main 的安装器产物。
源码测试、原生构建、包安装/运行/卸载、公开下载和完整参考验收继续分别记录。

## 已合入 main 的后续实现

在0.10公开源码之后，main 继续加入了：

- 集合内嵌索引/键编辑、结构化排序规则及152个已枚举locale、集合选项工作流。
- 独立聚合管线、42阶段菜单与全部编辑器模板；参考实际只为
  `$match`、`$project`、`$sort`、`$limit`、`$skip` 提供创建工具切换。
- 三档源采样、阶段排序/启停、分阶段预览、运行/取消/导出，以及
  Navicat `Version 0.0 .nagg` 的导入导出、未知字段和未编辑 MQL 注释保留。
- Pipeline Options 参数映射，以及左到右视觉 Explain 画布、节点详情、搜索、缩放和阈值显示。
- 无法安全投影的精确 `$jsonSchema` 进入完整原始 Query，避免将未知、超限或不能表格化的规则改成空模式。

这些是已实现的局部工作流，不能关闭完整工具或三平台 UI 门禁。参考与实现的细节分别见
[管线观察](../ui-reference-18-mongodb-pipeline.md)、[管线实现](../mongodb-pipeline.md)、
[验证器实现](../mongodb-validator-ui.md)及上述完整矩阵。

## 测试与唯一 GIF 的证据归属

11月12日交接报告的最近本机结果为：全源 **1821 passed / 563条件跳过**，
owned MongoDB 集合 runner **290 passed / 0 skipped**（8个精确fixture），
Cocoa集合设计器 **34 passed**、Cocoa管线 **14 passed**、源smoke **6行×4列**。
上述是交接时的历史测量；本轮恢复后已另行执行下文基线，不把两轮相加或冒充完整平台验收。
当前 main 的 CI 状态已按上一节重新核对，未在本轮读取其JUnit或重算各范围测试数。

本轮直接读取当前 `docs/assets/opennavicat-demo.json` 并重算 GIF 文件 SHA256：

- **25阶段、326帧、54.34秒**，文件1,438,344字节。
- SHA256：`56d3f7add673e20c7e4e6caab5b9ee6b10d725223dab0129edcaaffb8127caa6`，与receipt一致。
- receipt为每阶段记录的隐私检查帧数均等于捕获帧数；此前交接记录逐阶段视觉隐私审查通过。
- 本轮只复核文件与receipt，没有重新录制或重新逐帧视觉审查。演示对应当前main的后续开发，
  不能据此声称这些全部存在于公开0.10安装器中。

前序fixture、参考对象及匿名卷的清理保留各自历史归属。快照核对后，本轮另用全新owned fixture继续参考观察与源码测试，后续记录单独列明。
CUA/REA/Ghidra的可用性也是前序交接事实，后续需要使用时再验证当前工具状态。

## 本轮继续顺序

1. 以新 owned fixture 实测聚合管线自动任务引用与 change-stream 入口、参数、默认值、错误/取消/保存/重开，
   先区分实际参考行为与现有模板声明，再实现其完整工作流。本快照不预先声称该项已经观察或实现。
2. 扩展大型分叉、分片与副本集视觉计划、精确成本高亮公式及三平台参考布局。
3. 继续验证器外部转换、未知/根模式组合和独立边界语义。
4. 推进其余MongoDB对象工具和其它数据库、认证/权限、向导、AI、模型、BI、备份与自动运行门禁。
5. 下个可用发布里程碑仍须以其精确源码执行六个原生目标构建和smoke，核实六安装器与校验和后才发布；
   新演示功能变化时刷新同一段实际应用GIF并审查隐私。完成该里程碑后继续完整goal。

所有新验证继续使用临时SQLite或精确owned、loopback数据库fixture；不读取已有Navicat配置秘密，
不访问用户业务库，不把原版代码、图像或原始参考证据装入应用。

## 恢复后的源码检查与修复（未发布）

- 本机当前基点c5f4c74的默认全源重新执行：1821 passed / 563条件跳过，77.86秒；
  源smoke 6行×4列及所有驱动/crypto/native-JS探针通过。没有把条件跳过算作真库执行。
- 修复aggregate脚本的第二参数边界，使用已验证BSON构造器保类型；
  实际原生JS执行生成脚本并核对参数BSON，包含零stage和typed let。
- 预览在任何数据库调用前拒绝启用的change-stream阶段，避免行数上限被误当成等待时间上限。
- 恢复连接树中已实现的聚合管道类别，通过实际点击列表/保存对象重开验证。
- 已载入stage缺少MQL文本时使用实际BSON而非Help模板；简化表单不能无损投影时保留原始编辑器。
  数组/标量stage保留真实MQL值，不能安全生成脚本的BSON在保存动作中显示错误并保留草稿。
- 管线模型专项41项零跳过、Qt页21项零跳过、真实MainWindow树导航/重开1项通过。
  这些范围重叠；不相加，不替代后续最终源码六目标CI或完整UI验收。
- 集合runner最初因Docker尚未启动而前提失败，已保留该失败，不计为通过；daemon恢复后将用新证据目录重新验收冻结源码。
- 原版批处理作业及changeStream流程仍在观察；本地Batch格式/Qt页/运行层为进行中的后续工作，不把基础模型当成功能交付。

## Batch工作流整合与0.11候选

本地Batch Version1.4格式/精确引用、四页Qt、全局列表、独立会话串行执行及SQLite历史已接入。
实际参考见AU18；重复引用、读取Start前最新保存内容、失败继续/跳过、旧名失效、
断开主连接后执行、持久历史、真实长查询独立取消及后续新会话恢复均有owned MongoDB验证。
历史失败保留结果与待保存队列，逐条确认后才移除；可显式重试且不重新执行数据库任务。
非pipeline类别、SMTP、OS计划/触发/恢复、完整副本/分组和三平台参考UI仍开放。

0.11元数据下最终本机工作树默认全源2011 passed/567条件跳过，79.24秒；
精确集合/任务runner460项零跳过，53.23秒，8个fixture逐完整ID独立确认不存在，48项源码哈希匹配。
原生Cocoa组件与4个真实主窗路径51项零跳过，10.64秒；不与其它范围相加，也不当作六安装器或全部parity。
28阶段、371帧、61.84秒的新单GIF已完成实际Cocoa录制、逐阶段隐私审核并替换README资源；0.11六目标CI/tag/公开资产仍待完成。

## 首次预检后的预算问题与当前发布门禁

候选 `4111d3310e22ac6e42d72de93fa1a93e4f975118` 已推送，非tag六目标预检
[43362158137](https://github.com/shubolab/opennavicat/actions/runs/43362158137) **7/7成功**，发布跳过。
本轮未下载这轮安装器字节；不能把状态成功当作独立资产审查。
随后真实wire/profile发现PyMongo CSOT将Run/Preview设置的1/10毫秒改为约119999，
Explain正限制也未在正确层级生效，因此**该提交不发布**。
正预算专属有限网络客户端及Explain外层时限修复正在完成取消边界和最终整体验证；
最终SHA须重新执行六目标与正式tag，不复用4111d33的CI资格。
细节、原失败及修复后独立真库回执见 [0.11验证记录](../validation-0.11.md#首次六目标预检及执行预算修复)。
完整goal保持active，不能因预检绿色、发布候选或下个里程碑而停止。

修复后本机全源2051 passed/590条件跳过；完整runner498项零跳过、52.10秒，8个认证原生ARM64 fixture
逐完整ID独立确认清理，48项源码哈希匹配。原生Cocoa56项、MongoDB TLS/SSH24项均零跳过，
源smoke6行×4列通过。三路真实Window启动漏取消已红绿复现并修复，Batch runtime亦贯穿同一取消Event，
独立cancel_pool的token/marker重试不会因GUI清理迟到而抓到下一任务。
新增参数化case首次被严格runner声明检查拒绝且未执行DB；显式列明三case后新目录验收通过，旧失败保留。
最终source仍须新main预检及六安装器审查通过后才tag；原4111预检只保留历史归属。

下一条实现入口仍为AP18-10的有限change-stream Run：同一游标消费初始/非空批，
首次空批即结束并由owner关闭，不自动resume或后台watch，也不照抄参考遗留idle游标。
每次try_next至多一次有限await不等于整次Run只能一次getMore；参考已观察到事件批后再有空批。
不得追加采样或$limit，必须分别限制行、原始BSON/渲染字节与协作时限，保留显式token选项和取得的事件。
事件显示位置、Preview All/Stage Preview、fullDocument变体、断网游标清理、其它拓扑/权限及三平台UI
仍需实际参考与owned fixture验收。此计划不属于0.11已交付功能，须先完成当前发布安全门禁。
