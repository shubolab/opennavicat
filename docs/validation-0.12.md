# 0.12 候选验证记录

日期：2026-11-13，Asia/Shanghai。**0.12.0 尚未发布**。当前公开版本是
[v0.11.0](https://github.com/shubolab/opennavicat/releases/tag/v0.11.0)，源码87df5cd；
本分支的新功能不能借用该版本的六安装器或公开下载资格。

## 源码与实际参考

候选从0.11源码继续，有限后端检查点1841c01已推送，随后合入仅文档的ee876b5。
本轮接入原生Qt stream消息/输出、当前页Stop、typed结果和结构化安全错误路由，以及Batch边界状态。
版本元数据已调整0.12.0。有限UI/GIF检查点已提交`979d052`，随后`479a811`修复Task ownership并加入四项回归。
修后默认全源的201份文件前后哈希一致，并逐个匹配`479a811`不可变Git blob；
测试开始时的旧HEAD只是测量基点，实际已测应用/测试/runner源码绑定到`479a811`。
包含本验证文档的最终候选提交、该精确SHA的六目标CI和正式发布仍待完成。

实际参考为Navicat Premium **18.0.5 build321 / macOS**，使用自有临时MongoDB8.0.34单节点副本集。
新观察为 [AP18-11](ui-reference-18-mongodb-pipeline.md)：
同页空跑/事件/再次空跑的页签数量为3/4/3；忙时只有管道与脚本两页，spinner位于下方公共详情区及app页签，
消息/输出稳定显示脚本、状态和实际耗时，无事件网格。错误和Stop回三页。
实际非法`$limit`错误另有服务器原文和独立`at line 1, column 27`行；Stop没有位置行。
源、采样、数量和operator在忙时禁用；其它未执行的状态继续开放。
原版退出与临时服务/卷清理有独立证据，原版遗留idle游标不作为期望产品行为复制。

## Task ownership修复后的本机检查点

`479a811`将TaskSignals归属MainWindow，并在既有finished清理之后连接`deleteLater`。
四项真实Qt回归覆盖serial pool与cancel pool各自成功/错误路径、前任务native尾段仍活跃时提交后续任务及worker GC，连同原18项共22项通过；
销毁/释放在GUI线程，完成的Task/TaskSignals不再留存。下表各scope独立归属、彼此重叠，不相加。
原始parallel SIGSEGV是否由同一retention造成仍未证明。

| 范围 | 修后结果 | 证据与边界 |
| --- | --- | --- |
| 默认全源 | **2184通过 / 636条件跳过，84.67秒（stdout）** | Python3.13.11、Darwin arm64、offscreen、native helper已构建；201个源路径前后相同且逐Git blob匹配`479a811`，JUnit84.650秒。条件跳过不是实际服务执行。 |
| 原生Cocoa | **98通过 / 零跳过，28.60秒（stdout）** | 16真实Window＋78组件＋新增4项真实生命周期回归；actual Cocoa、Darwin arm64、版本0.12.0，14份源哈希前后相同；自有RS一容器/两卷独立逐ID absent。 |
| 实际Window独立runner | **16通过 / 零跳过** | 11份声明源码哈希匹配修后源码；自有RS一容器/两卷独立逐ID absent。涵盖实际工具栏→wire、错误/位置、Stop、普通视图恢复及两条真实Batch路径。 |
| 普通集合runner | **511通过 / 零跳过** | 严格case manifest；outer observer的198份源哈希前后相同，runner声明48份源哈希匹配；8容器/16卷独立逐ID absent。包含整个22项lifecycle模块。 |
| recorder专项 | **offline79通过 / 3条件跳过；own-RS另1通过 / 零跳过** | 两scope各79份文件前后相同并逐Git blob匹配`479a811`；真实Run取得2个typed事件、精确BSON/UUID及原主连接不变，own-RS容器/两卷独立absent。offscreen专项没有录制GUI。 |
| 源smoke | **6行×4列，版本0.12.0** | 修后Cocoa源smoke exit0，全部驱动/加密/native-JS探针通过；不是包内smoke。 |
| 未变的声明源scope | 后端**46/0跳过**；普通TLS/SSH **24/0**；stream TLS/SSH **2/0**；stopped cleanup **13/0** | 原各scope声明源字节未变并重新绑定，不冒称ownership之后重跑。后端32纯/14真实RS；stream transport仅standalone及40573拒绝；cleanup零start/零DB。普通TLS全部卷ID未由外层observer独立保留的限制继续有效。 |

修后回执分别为`artifacts/release-012/final-default-ownership/verification.json`、
`final-native-cocoa-ownership/verification.json`及`independent-cleanup.json`、
`final-window-owner-fix/summary.json`、`final-collections-owner-fix/summary.json`及`independent-check.json`，
总交接索引为`owner-fix-validation.json`。
recorder两scope目录为`final-recorder-ownership-{offline,own-rs}-20261113T065926Z-9dc0cd/`。
这是本机有限范围资格，不是新六安装器、公开发布或完整参考验收。

## Task ownership修复前的本机候选检查点

以下范围分别有回执，彼此重叠，不相加；均归属于后续Task ownership修复之前，
不能移用成修后源码资格，也不替代原生安装器或完整参考验收。
Root已将默认全源回执中的201个源路径逐个与Git index blob匹配；
第一源码检查点保持为这些已测字节，ownership小修复不混入该资格。

| 范围 | 已执行结果 | 边界 |
| --- | --- | --- |
| 默认全源 | **2180通过 / 636条件跳过，82.89秒** | Python3.13.11、Darwin arm64、offscreen，native helper已构建；201个runtime/test/runner/native/workflow/project源路径前后哈希相同。条件跳过不算真库执行。 |
| 有限后端独立runner | **46通过 / 零跳过** | 32纯合同、14真实认证RS；包括bounded errmsg/去URI及已知配置秘密、phase分类、实际wire预算/BSON/无初始retry/游标清理。6份声明源哈希匹配。 |
| 实际Window独立runner | **16通过 / 零跳过** | 实际工具栏点击→draft/signal→Window→serial queue→真实wire；含页级Stop、queued/cold/late取消、主连接Off后的重复Batch和持久历史、真实byte失败，以及非法`$limit`的服务器errmsg/冻结脚本位置。11份声明源哈希匹配。 |
| offscreen组件 | **78通过 / 零跳过** | 新stream组件57项＋既有pipeline组件21项；动态2/3/4页、私有typed事件、普通视图恢复、合法有/无位置错误、恶形字段/秘密cause及优先级负测。 |
| 原生Cocoa | **94通过 / 零跳过，28.89秒（JUnit）** | 16真实Window＋78组件；实际platformName=cocoa、Darwin arm64、版本0.12.0，13份源码哈希前后相同；本次独立RS容器及两卷逐ID确认absent。不是全平台完整UI验收。 |
| 普通TLS/SSH | 24通过 / 零跳过 | 本轮已执行；fixture确认`rm --volumes`及CID absent，但首wave外层observer未保留每个卷ID，不能写成全部普通TLS卷均独立逐ID核验。 |
| stream独立TLS/SSH | **2通过 / 零跳过** | 最后源范围匹配的真实TLS/SSH/mTLS，客户端先于tunnel/PEM关闭；standalone实际拒绝40573，不冒充RS事件。 |
| stopped清理边界 | **13通过 / 零跳过** | 严格独立qualification UUID、exact case/JUnit/source hash/ledger；零start/零DB，15个新stopped容器和30卷的精确清理回执。 |
| Batch runtime | 28通过 / 零跳过 | typed部分失败、行/时间边界和cleanup_failed优先于取消；不是28条真实DB工作流。 |
| recorder专项 | offline 79通过 / 3条件跳过；own-RS另1通过 / 零跳过 | 78份源哈希前后一致；真实按钮Run得到2个typed事件、保原主连接和BSON/UUID、精确清理。该专项没有录制或捕获GUI，不冒充最终GIF审核。 |
| 源smoke | 6行×4列，版本0.12.0 | 所有驱动构造、TLS/BSON、加密及native-JS BigInt/取消/回收探针通过；不是安装包smoke。 |
| 普通集合runner r2 | 507通过 / 零跳过 | fresh串行执行，8个容器/16卷独立逐ID确认absent；不用于消除首轮进程崩溃记录。 |

主要私有回执为 `artifacts/release-012/final-default/verification.json`、
`final-native-cocoa/verification.json`及其`independent-cleanup.json`、
`final-security-stream/summary.json`、`final-stopped-cleanup/summary.json`；
后端/Window分别是`artifacts/stream-structured-errors-{backend,window}-qualification/summary.json`。
各范围按自己的源码哈希和case manifest归属；未把不同scope的数量或资源总数合并。

普通集合首次并行进程出现SIGSEGV，没有完成的JUnit，失败目录
`artifacts/release-012/final-collections/`已保留（runner因缺失结果文件记录`FileNotFoundError`，
它不是对崩溃原因的诊断）。新目录`final-collections-r2/`的串行507项通过，
其`independent-check.json`确认source前后相同及8容器/16卷精确absent。
**串行重跑通过不能证明首次崩溃原因。** 另一个真实`MainWindow.submit`实验已确认：
正常完成、pool active为0且native QRunnable失效之后，Task/TaskSignals的Python对象仍被native连接闭包环保留，
worker GC不能释放。私有候选实验用GUI parent及finished后的`deleteLater`恢复GUI线程销毁/释放，
该retention随后由`479a811`最小修复及上节回归验证；是否就是首次SIGSEGV的原因仍未建立。
两次task的current-r2与candidate独立结果分别在
`artifacts/release-012/task-ownership-probe/current-r2-result.json`和`candidate-result.json`，
machine index保留了两组live/destroy/finalize对照。
本节数字保持为修复前历史；修后full-source、native/Window和collection资格见上节，仍不构成可发布结论。

## 已保留的历史检查点

UI接入前的1841c01后端检查点为默认全源2073通过/619条件跳过，74.50秒；
当时独立后端26项（12纯、14真实RS）、普通TLS24、stream transport2、stopped13均有各自回执。
初版Qt组件48项、Window13项和原生Cocoa61项（23.66秒、10份源哈希）属于更早的UI范围；
其后Window15项（19.88秒）加入真实Batch。这些不是本节最终源范围的替代品。
初版Cocoa证据在`artifacts/finite-stream-ui-phase/native-cocoa-61/`，
Window15在`artifacts/stream-window-batch-15-qualification/`。
完整有限后端合同与历史回执见 [有限stream开发记录](mongodb-pipeline-stream.md)。

## 当前错误与展示边界

组件只从实际`StreamExecutionError`读取owner已校验的`server_message`、`error_phase`和可选位置；
文字有单行/Unicode/长度边界，owner去除完整URI及当前配置的已知秘密，不拼接`str(error)`、任意cause、
codeName或事件文档。有效aggregate位置必须对应本次冻结脚本；getMore或没有可靠位置时保留合法message，
不制造位置行。恶形元数据回退为验证过的数字code；取消、cleanup_failed、行/字节/时间边界和认证错误
保持静态状态优先。完整多行错误及全部服务器错误变体仍未关闭。

事件的原始BSON/Canonical EJSON和partial/undelivered元数据保留在本次私有typed结果中，
不进入普通结果网格、`last_rows`或未验收的事件导出。Batch不把部分/行/时间边界记为无条件成功，
历史日志不读取新服务器errmsg。正常stream预览仍保留安全拒绝；参考Preview故障不被复制为期望行为。

## 待完成的交付门禁

最后完整源码提交后，仍需该精确SHA的全源与完整协议/安全CI、六原生目标产物和独立审查。
唯一GIF已刷新为实际0.12候选应用的31阶段、411帧、68.5秒，1,712,746字节，
SHA256 `d1ec33dd15e03fa38e5749f0ff0efe01051e5b40116c12229a7113d847e33c9e`。
31阶段均检查首/中/尾编码帧（93帧）并补充全尺寸视觉检查，自动逐帧guard为411/411；
未见真实连接/路径/凭据/业务内容，无原版截图。录制使用新认证RS，实际取得2个typed事件、
保留原主连接、精确BSON/UUID与游标清理，自有服务和卷已移除；事件payload未渲染到GUI。
GIF与receipt已复制到`docs/assets/`，录制源码属于`979d052`：77份capture源逐Git blob匹配该提交。
与`479a811`相比，演示运行源码仅Window的两行ownership不同；pipeline widgets及recorder未变，
新79＋1专项验证修后工作流，兼容回执在`final-recorder-ownership-compatibility-20261113T065926Z-9dc0cd.json`。
保留已审GIF，没有冒称`479a811`重新录制或旧capture哈希等于新Window；仍不是公开0.12安装器验收。
新六目标源码测试、原生构建、包内smoke、真实安装/运行/卸载、独立六安装器审查、
正式tag及八个公开资产的匿名下载尚未完成；只能在完整六安装器和校验和通过后发布。

完整UI/工具功能、正常stream预览、全部选项/拓扑/权限、Windows/Linux实际参考和其它矩阵门禁
继续开放。原版故障不取消这些要求；可用里程碑、源码或打包成功均不表示完整goal完成。
