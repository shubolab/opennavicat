# 0.12 预发布验证记录

日期：2026-11-13，Asia/Shanghai。[**v0.12.0预发布已公开**](https://github.com/shubolab/opennavicat/releases/tag/v0.12.0)，
非draft、prerelease=true，发布时间**2026-11-13 16:17:20（北京时间；08:17:20 UTC）**。
annotated tag精确绑定源码`b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`。
正式六安装器、校验和/manifest八资产已匿名下载核验；这不是完整Navicat UI/功能完成，goal继续active。
[v0.11验证记录](validation-0.11.md)保留上一发布历史，不替代本版资格。

## 源码与实际参考

候选从0.11源码继续，有限后端检查点1841c01已推送，随后合入仅文档的ee876b5。
本轮接入原生Qt stream消息/输出、当前页Stop、typed结果和结构化安全错误路由，以及Batch边界状态。
版本元数据已调整0.12.0。有限UI/GIF检查点为`979d052`，`479a811`修复MainWindow调用点，
`d9bee66`另修复实际ConnectionDialog Test按钮的TaskSignals生命周期；`1a6eeed`只修正测试子进程ready握手。
本版完整发布源码冻结为**`b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`**：
只新增限定`hdiutil CREATE Resource busy`的打包修复及合同测试，应用源码字节仍与`d9bee66`相同。
默认全源2197通过/636条件跳过，201份文件前后相同并逐Git blob匹配`b6e1da4`。
main预检[43388068691](https://github.com/shubolab/opennavicat/actions/runs/43388068691)
已**completed/success，7/7**（六原生目标＋DB security，publisher skipped），同SHA的独立安装器审查通过。
据此已创建并推送annotated tag **v0.12.0**，精确指向`b6e1da4`。
正式tag流水线[43390347592](https://github.com/shubolab/opennavicat/actions/runs/43390347592)
为`push / v0.12.0 / b6e1da4`，已**completed/success，8/8**。
正式独立13 artifact/六安装器审查与八个公开资产匿名下载核验均通过；正式六SHA全部不同于main预检，
下方正式表来自tag产物及公开实字节，不以main产物替代。后续doc-only提交不改tag源码。
旧`1a6eeed`预检[43384833779](https://github.com/shubolab/opennavicat/actions/runs/43384833779)
保持**completed/failure**：五原生目标和DB security成功，macOS amd64的hdiutil CREATE报Resource busy，
未到安装后launch，publisher skipped；其2186本机结果不替代新打包源码资格。
此前第二调用点修复前的`f0b558e`预检[43383205925](https://github.com/shubolab/opennavicat/actions/runs/43383205925)
终态为**cancelled**，未tag/发布，partial产物不作新资格。

实际参考为Navicat Premium **18.0.5 build321 / macOS**，使用自有临时MongoDB8.0.34单节点副本集。
新观察为 [AP18-11](ui-reference-18-mongodb-pipeline.md)：
同页空跑/事件/再次空跑的页签数量为3/4/3；忙时只有管道与脚本两页，spinner位于下方公共详情区及app页签，
消息/输出稳定显示脚本、状态和实际耗时，无事件网格。错误和Stop回三页。
实际非法`$limit`错误另有服务器原文和独立`at line 1, column 27`行；Stop没有位置行。
源、采样、数量和operator在忙时禁用；其它未执行的状态继续开放。
原版退出与临时服务/卷清理有独立证据，原版遗留idle游标不作为期望产品行为复制。

## 正式tag与公开资产审计

正式run六个原生目标、安全专项、publisher共8个job全部成功。独立下载13个artifact并审查六安装器的实际字节、
native/installed双manifest、sidecar、原生架构、构建/包内smoke/安装/launch/卸载收据。
正式报告为`artifacts/release-012/tag-ci-audit-43390347592/verification.json`。
两个正式CI DMG均首试create成功，不声称实际busy重试恢复。

Root使用禁用curl配置、无Authorization的匿名下载取得全部八公开资产，全部HTTP200，
API size/digest、SHA256SUMS、合并manifest及正式CI字节一致；public tag也核对到b6。
回执为`artifacts/release-012/public-43390347592/verification.json`及`public-release-api.json`。

| 正式目标 | 字节数 | 正式及公开安装器SHA256 |
| --- | ---: | --- |
| linux-amd64 | 76484848 | `8ffde47fd6d8b06043ab14bafdd584bc416ff19972acab331c84f3f803369da1` |
| linux-arm64 | 75128210 | `21a07643ddae14e167e631225d2f80f632c660b5769ddf3b2570a939bf27cf89` |
| macos-amd64 | 50457997 | `f08d3efb31e784ac1108e8d1528ffbed13b4785c4d504ddb7928fbf71be27988` |
| macos-arm64 | 49791328 | `8a37a8abd41fcf949a54a042449dd00ad6f2f76cf8a0ec3ee40e3067e2975c17` |
| windows-amd64 | 43802062 | `15b08840d89f7ad3ad247a6a1c37d8edecba524da38619e80c9556d536c3a662` |
| windows-arm64 | 34745994 | `8151d2401e63cc003829c09e2f250fa702b02e3f4191d6738882fe8083ed2fa7` |

`build-manifests.json`为205,707字节，SHA256 `d7dd33b72b4fbfccf29a2307c6ef7fa81758730271f0d6faeee71dc8c53e2f65`；
`SHA256SUMS`为622字节，SHA256 `89cd9ac515f0161a8f807557ffae33a97445c4e17924f550561d0d67e7266c71`。
正式数据库安全job的collection511、stream backend46、stream Window16、regular TLS24、stream TLS2与stopped13
各自exact manifest/source hash/零skip及已有清理回执已审查，彼此不相加；这些stream执行范围在Linux amd64安全job，
不能写成六平台stream功能或完整UI验收。CI服务清理按回执离线审查，不冒充本机重新inspect CI旧资源。

匿名公开ARM64 DMG另经只读mount后的实际native Cocoa/arm64/version0.12隔离6×4 smoke，
全部driver/crypto/native-JS探针、bundled helper及arm64 image架构通过；未读真实profile，最终detached且未挂载。
证据为`artifacts/release-012/public-macos-arm64-smoke/{verification,runtime}.json`。
这仍不是Windows/Linux人工参考或完整工具验收。

## 已通过的main预检（历史，非正式资产字节）

同SHA预检下载13个artifact；六个提取安装器的实际字节与native/installed两份manifest及sidecar SHA一致，
原生架构及各目标CI的构建、smoke、安装/launch/卸载收据通过。Root另重新hash六安装器，
独立完成检查确认两个自有只读DMG挂载已卸载；两个CI DMG均首试create成功，不能声称实际busy恢复。
主报告为`artifacts/release-012/main-ci-audit-43388068691/`内的`verification.json`、
`root-before-tag-check.json`和`independent-completion-check.json`。以下仅main预检安装器，**不是正式公开资产SHA**。

| 目标 | 字节数 | main预检SHA256 |
| --- | ---: | --- |
| linux-amd64 | 76483378 | `105c6c5118d5ebb3d7e09a2eed69851f86cd45437f9a84a47bccb3f9dcb1e6ee` |
| linux-arm64 | 75126808 | `d96b514cf9af1d2f3aa68759ed6811d6f61feac316c7bfa657674f22c1ad4e69` |
| macos-amd64 | 50456506 | `62e0c4fd69d84e73deab067c18caa5019a8bfd4669e56383912a6c21fbee8953` |
| macos-arm64 | 49793929 | `a4f48c6d08607316718b445a7f6b4b2fe6601b9a35fb3cdfd38900e9c197cd15` |
| windows-amd64 | 43804383 | `ec2f50af307dd732b7f1365f2189a4047a79466e7f75ee6c537bfcebe264c6b5` |
| windows-arm64 | 34749256 | `af9ca674b8e52bdb8449c6c5e2f67d4898b6dfb87603eef750403844485ff8f4` |

后续正式run、独立正式产物及匿名八资产核验已完成，见上节；本表仍只表示历史main预检字节。
源码/安装器CI不关闭完整参考UI、全部工具功能或三平台人工验收；goal仍active。

## 当前打包修复检查点（b6e1da4）

默认全源为**2197通过 / 636条件跳过，82.81秒（stdout；JUnit82.788秒）**，
201份源码前后相同并逐Git blob匹配`b6e1da4`；证据为`artifacts/release-012/final-default-dmg-create-fix/verification.json`。
installer合同独立**79通过 / 零跳过**，含11项新增合同及原失败红证据，见`dmg-create-fix/`。
修复仅匹配观察到的busy stderr，最多三次create，间隔1/2秒；每次命令超时仍为600秒，
create阶段最多1803秒，另有stage/verify工作，不能写成全流程总600秒。
非busy/timeout立即失败；同文件系统私有image目录中处理partial，只有成功且非空regular产物才原子替换目标。

本机native macOS ARM64实际build、包内Cocoa 6×4/all probes，以及新DMG只读mount、隔离安装/launch/卸载全部通过；
`local-installer-dmg-fix/independent-check.json`八项独立检查通过，挂载和自有image临时目录均已不存在。
DMG为51,441,633字节，SHA256 `00732b3c536d17c0e94f6f7a194f37a0e88cb32b3847dffd097b83784d1786da`。
真实create第一次成功；没有实际触发busy重试恢复，重试边界由11项合同测试覆盖。
这是本机ARM64范围，不代替Intel CI、其它目标安装器、匿名公开下载或完整参考验收。
应用UI/DB源码未变；下节Cocoa100、Window16、caller110、DB各scope和recorder79＋1
仍按各自实际执行来源及未变声明源重新绑定，不冒称在`b6e1da4`重新执行。

## 两个实际调用点修复与测试握手检查点（1a6eeed历史测量及未变scope）

Window与Dialog各两行产品改动只安排信号native生命周期：将TaskSignals归属调用widget，
并在既有finished清理之后安排该signals的`deleteLater`。真实回归确认native
`TaskSignals QObject.destroyed`在GUI线程，Task/Signals弱引用最终释放；
QRunnable仍由pool autoDelete，native尾段尚未返回时worker持有Task/Python wrapper属于正常生命周期，
不把它写成所有对象均在GUI销毁。Dialog两项新增回归使用真实有效/损坏SQLite、实际Test按钮，
还在旧native尾段活跃时修复SQLite并排队重试，证明旧signals的deleter不误删新task。

| 范围 | 当前结果 | 源码与执行边界 |
| --- | --- | --- |
| 默认全源 | **2186通过 / 636条件跳过，82.59秒（stdout）** | `1a6eeed`；201份源前后相同且逐Git blob一致，JUnit82.569秒。Python3.13.11、Darwin arm64、offscreen、native helper已构建；未设置外部数据库 opt-in 环境变量。 |
| 原生Cocoa | **100通过 / 零跳过，30.36秒（stdout）** | 16真实Window＋78组件＋4 Window生命周期＋2 Dialog生命周期；actual Cocoa、Darwin arm64、0.12.0，16声明源前后相同；一RS容器/两卷独立absent。实际执行在产品源码`d9bee66`，test-only握手修正未改这16份源，按未变字节重新绑定总SHA。 |
| 相关caller范围 | **110通过 / 零跳过** | Dialog/lifecycle/Mongo及其它连接UI；含真实SQLite红绿回归。生产runner和数据库工作流未因此扩大改动。 |
| 实际Window独立runner | **16通过 / 零跳过** | Dialog修复后fresh执行；11声明源及额外2份Dialog源码/回归文件前后匹配，一RS容器/两卷独立absent。test-only握手修正不改变此范围。 |
| 普通集合runner | **511通过 / 零跳过，声明源资格重新绑定** | 48声明源在Dialog及握手修正后未变，保留原8容器/16卷独立absent证据；没有新执行511项。旧outer198整树快照包含旧Dialog，只作历史，不能写成当前整树匹配。 |
| recorder专项 | **offline79通过 / 3条件跳过；own-RS另1通过 / 零跳过** | 两调用点修复后fresh执行；各79源及补充Dialog回归文件前后相同并绑定`d9bee66`，握手修正未改这些声明源。真实Run得2个typed事件、BSON/UUID及原主连接不变，own-RS容器/两卷独立absent。没有录制GUI。 |
| 未变范围 | 后端**46/0跳过**、普通TLS/SSH **24/0**、stream TLS/SSH **2/0**、stopped cleanup **13/0** | 各声明源未变，按字节重新绑定，不冒称新运行。transport仍非RS事件资格；普通TLS外层卷ID不完整边界继续保留。 |
| 源smoke与握手专项 | 源smoke **6×4**；offline握手模块 **10通过 / 零跳过** | 产品`d9bee66`的Cocoa源smoke exit0，全部驱动/加密/native-JS探针通过；`1a6eeed`的10项测试不启动Docker或DB，不代替包内smoke。 |

新全源回执为`artifacts/release-012/final-default-two-callers-readiness/verification.json`。
其它回执在`final-native-cocoa-two-callers/`、`final-window-dialog-owner-fix/`、
`dialog-task-ownership-probe/green-caller-scopes.xml`及
`final-collections-owner-fix/declared-scope-after-dialog-binding.json`。
recorder两scope为`final-recorder-two-callers-{offline,own-rs}-20261113T071112Z-222717/`；
各`final-revision-binding.json`记录79声明源及补充文件与`d9bee66`的逐Git blob绑定。
各scope重叠，不相加；公开版本仍0.11，六原生目标新资格和完整parity仍未完成。

### 保留的默认全源失败与握手证据

`d9bee66`的首轮全源为**2185通过 / 1失败 / 636条件跳过，88.77秒**，
唯一失败是`test_real_slow_child_receives_interrupt_finishes_finalizer_and_is_reaped`缺少finalized文件；
201份源前后相同并绑定该提交，证据在`final-default-dialog-ownership/`。原node独立重跑1通过，不能抹掉失败。
私有受控file-gate实验使用真实POSIX子进程和生产`stop_process`：ready在try之前时精确中断可绕过finalizer，
try先建立再写ready时finalizer执行；两者均被回收。它证明原握手设计存在缺口，
不证明首轮失败的具体时序。证据为`slow-child-readiness-proof/controlled-gate.json`。
`1a6eeed`仅将ready写入移到try第一行；生产runner、finalized和reap断言未改，独立10项及上述fresh全源通过。
此失败与更早parallel SIGSEGV分别记录，二者原因不能相互代替。

## MainWindow单调用点修复后的历史检查点（479a811）

`479a811`将TaskSignals归属MainWindow，并在既有finished清理之后连接`deleteLater`。
四项真实Qt回归覆盖serial pool与cancel pool各自成功/错误路径、前任务native尾段仍活跃时提交后续任务及worker GC，连同原18项共22项通过；
native `TaskSignals QObject.destroyed`在GUI线程，Task/Signals弱引用最终释放；不声明所有QRunnable/Python wrapper均GUI销毁。下表各scope独立归属、彼此重叠，不相加。
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

该历史修后回执分别为`artifacts/release-012/final-default-ownership/verification.json`、
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
worker GC不能释放。私有候选实验用GUI parent及finished后的`deleteLater`恢复native信号的GUI线程销毁及Task/Signals弱引用释放，
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

## 已完成的里程碑交付与继续开放门禁

旧main预检43384833779已失败并保留历史；b6e1da4的main/正式CI、独立六安装器及匿名八公开资产核验已完成，v0.12.0预发布已公开。
唯一0.12兼容实际应用GIF为31阶段、411帧、68.5秒，1,712,746字节，
SHA256 `d1ec33dd15e03fa38e5749f0ff0efe01051e5b40116c12229a7113d847e33c9e`。
31阶段均检查首/中/尾编码帧（93帧）并补充全尺寸视觉检查，自动逐帧guard为411/411；
未见真实连接/路径/凭据/业务内容，无原版截图。录制使用新认证RS，实际取得2个typed事件、
保留原主连接、精确BSON/UUID与游标清理，自有服务和卷已移除；事件payload未渲染到GUI。
GIF与receipt已复制到`docs/assets/`，录制源码属于`979d052`：77份capture源逐Git blob匹配该提交。
与当前产品`d9bee66`相比，演示运行源码仅Window/Dialog各两行ownership不同；pipeline widgets及recorder未变，
新的两调用点79＋1专项验证修后工作流，兼容回执在`final-recorder-two-callers-compatibility-20261113T071112Z-222717.json`。
`1a6eeed`额外只改测试握手。保留已审GIF，没有冒称当前SHA重新录制或旧capture哈希等于新Window/Dialog；GIF本身不替代安装器或完整参考验收。
main与正式run各自六目标源码/构建/包内smoke/安装运行卸载、独立安装器及公开八资产均按本身字节完成验收，未把预检产物当正式公开资产。

完整UI/工具功能、正常stream预览、全部选项/拓扑/权限、Windows/Linux实际参考和其它矩阵门禁
继续开放。原版故障不取消这些要求；可用里程碑、源码或打包成功均不表示完整goal完成。

## 发布后的独立开发边界

AP18-12参考文档在doc-only提交a189508补充，不改变0.12 tag源码。后续variant工作位于独立分支
`codex_mongodb_stream_variants`、源码`94de386259a443fd8a2ce3e711ade58b93dfe58d`；
19项实际Window及46项backend只是该分支的本地后续范围，未进入b6/0.12安装器或正式资格。
AP18-12的每个已观察游标有2/1/2语义对照，但完整初始调用基数未知、overall仍inconclusive；
不据此关闭完整resume/image/拓扑/权限、成功Preview/Explain、有效事件呈现或Windows/Linux参考门禁。
