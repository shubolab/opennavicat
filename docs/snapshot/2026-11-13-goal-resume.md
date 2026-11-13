# 2026-11-13 远端进展核对与继续入口

完整 goal 已由用户恢复为 active。继续独立原生 Qt、完整 Navicat 数据库工具和
Windows/Linux/macOS 的 1:1 保留 UI；本次同步、既有 Release 或下一个里程碑均不表示完成。
全部门禁继续以 [parity matrix](../parity-matrix.md) 为准。

## 当前未发布 Oracle 0.13 候选

本次重新fetch后远端main仍为`6b61871`，主工作树及用户原工作树均clean并在该提交。
当前开发入口改为`codex_oracle_basic_integration`，目录
`/Users/wangshubo/.codex/worktrees/oracle-basic-integration/opennavicat`；
测量时合流提交为`65abe89278462ee62ec31db07207a298c9d44b66`，并非main或公开release。
专属Oracle backend `b8f3f8a`、五页连接/表视图UI `45e57e3`、实际native probe `1cb6bc1`
以及双原生数据库CI修正`6422649`已经合入该候选。版本为**0.13.0未发布候选**，
公开0.12的tag、六安装器和单GIF均未变。完整资格与限制见
[Oracle候选验证](../validation-oracle-0.13-candidate.md)。

后端93纯/14真Oracle、UI焦点246/默认全源2382通过659条件跳过各有自己的精确源码绑定；
它们不是合流后同一整轮，也不合算为真实窗口资格。本机actual ARM64 app构建、隔离6×4
packaged smoke和全部Oracle native探针已过；独立审核77应用模块、四扩展ARM64机器代码及
26.1.0 metadata/三许可证实际字节。actual Cocoa 45项表单/窗口状态测试零跳过，使用严格fake
数据库；packaged CUA另看五页、Save Password状态、保存重开和两次临时密码提示后取消。
该自有进程已正常退出并按精确PID确认absent；全局仍有另一个既有同bundle-ID应用，
其最后一次偶然AX观察不纳入证据或演示，也未在其窗口操作。

Oracle双原生backend CI [43415040482](https://github.com/shubolab/opennavicat/actions/runs/43415040482)
报告amd64/arm64成功，正在独立下载核对source/JUnit/image/cleanup；不得只凭job成功关闭资格。
前三次失败保留，只有第三次保留了明确`libEGL.so.1`缺失trace；前两次仅知collect阶段失败。
现正以独立owned Oracle补真实Dialog Test、Save/Edit/Open、schema/table/view、实际Stop后复用及
Disconnect/Close，并将该Window runner接入两native job后重新验证。暂未合入main，合流默认全源、
actual Cocoa真库窗口和六目标新安装器均仍待做；server SLEEP及时取消和完整Oracle工具/认证等门禁仍开放。
独立审计另复现共用Remove在异步Close完成前删profile、失败后留下session的问题；
`codex_oracle_delete_close_fix`正在做成功continuation/失败保留/同ID替换保护的最小修复，
合流前不得进入main或release资格，既有原生包和Cocoa收据只绑定各自旧源码。

## 当前已完成的发布与继续入口

**最新公开版本为[v0.12.0预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.12.0)**，非draft、prerelease=true，
发布于2026-11-13 **16:17:20（北京时间；08:17:20 UTC）**，annotated tag精确绑定
`b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`。
main预检43388068691为7/7，正式tag run43390347592为8/8；两轮各自13 artifact/六安装器已独立审查，
正式六SHA全部不同于预检，未拿main字节代替正式产物。八个公开资产已匿名HTTP200下载，API size/digest、
SUMS/合并manifest/正式CI字节全部匹配，并完成第二次独立八文件重hash。
公开ARM64 DMG另已只读mount后实际native Cocoa/arm64/0.12隔离6×4及全部驱动/加密/native-JS探针，
helper bundled/image arch通过、未读真实profile，最终detached且不再挂载。正式摘要与SHA见[0.12验证](../validation-0.12.md)。
这是有限stream及生命周期/打包修复里程碑，完整UI、所有工具和三平台参考/人工门禁继续开放，goal仍active。

### 发布后的当前集成（单独记录）

后续`codex_mongodb_stream_variants`的测试/文档提交`94de386`已与main `33b0df7`合流为`3012d3e`。
应用源码、CI、版本、GIF和公开安装器均未改变。三条实际工具栏回归以read角色、独立majority生产者和barrier
验证默认update、默认fullDocument字段过滤及updateLookup过滤的2/1/2事件；精确typed BSON、原serial session复用、
文本页不含事件数据和cursor清理均有各自断言。19 Window/46 backend的11份声明源在合流后逐Git blob复核未变。
新集成默认全源为2197通过/639条件跳过，96.92秒；215份运行/测试/构建/packaging/workflow输入前后相同且匹配3012d3e。
默认跳过不算真库执行。集成已以`dfbbf5e`推到main，新CI
[43395784419](https://github.com/shubolab/opennavicat/actions/runs/43395784419)
在stopped cleanup断言失败；下载的Window19/0、backend46/0已独立核对exact source/node/JUnit/owned cleanup，
这些局部成功不算整轮成功。两次Docker inspect同一个容器和两个卷，仅Mounts次序相反，测试错误要求同序。
已仅将该断言改成exact record/CID及Counter卷身份；受控新owned fixture的第二次inspect逆序使旧断言红、新断言绿。
完整cleanup13/0、零容器start/零DB连接，15CID/30卷逐ID独立确认absent；实际cleanup runner和应用未改。
此修正需新CI资格，原失败保留，不能沿用0.12 tag的六安装器证据；此变更不构成新发布。
详见[variant验证记录](../validation-mongodb-stream-variants.md)。

修正推送`5fcf99c`的独立新CI[43397137741](https://github.com/shubolab/opennavicat/actions/runs/43397137741)
仍失败：security和五native job成功，Mac Intel fullsource为2195通过/1失败/640条件跳过，
pending Promise语义断言与100ms总启动/执行预算竞争；产品parent deadline确在spawn/bootstrap之前。
受控180ms启动延迟使旧pending测试红、新测试绿，实际七错误case绿、独立VM103/0（不含修改模块）。
只给语义诊断1500ms，infinite/独立watchdog100ms及elapsed/cleanup/memory断言不变，runtime不放宽。
本轮security artifact另独立核Window19/backend46/TLS24/streamTLS2/cleanup13/collections511各scope exact source/node/cleanup，
局部通过不算整轮成功；新测试源仍需新CI。两次失败与各自归因均保留。

[AP18-13](../ui-reference-18-mongodb-pipeline.md#ap18-13-collection-images-offon-and-required-image-errors)
已在自有pre/post-images OFF/ON集合完成六次实际Run：WA1/2、required-pre/post ON各2、OFF各getMore47＋0/0 UI哨兵。
三个成功ON的Output像素确为空、原因unknown，首WA_OFF仍script+OK；完整initialcallunknown/overallinconclusive，
normalQuit后4idle/0active，exactCID/两卷清除，不复制遗留行为。不是完整image/resume/Preview/三平台通过。
后续未发布源码`b53f60e`已实际补六种toolbar/serial-owner回归，并只给已观察的两种required-image getMore47显示typed 0/0哨兵；真实script_location仍None，安全errmsg/状态优先及正坐标校验未松。
backend74/0、Window25/0分别在fresh auth nativeARM64 RS严格执行，默认2243/645、94.07秒/2888 exact cases和296源前后相同且绑定b53。
源检查点与JS仅测试预算修正合为`53cce32`，11份声明源/全部runtime和native源未变；默认全源未在合流测试预算下重跑，七native错误case资格另记，不冒称同轮。
独立审查另核4CID/8卷absence。合流后以`fab0419fd47ad49378f3c2481bee0dca46fe7a43`推送，新main CI
[43399815386](https://github.com/shubolab/opennavicat/actions/runs/43399815386)已completed/success：六native+security成功、publisher按main规则skipped。
13artifact inventory已绑定exactrun/source，独立下载七validation小包核六目标native build/packaged/installed launch/uninstall/helper收据；
未下载/重hash本轮六安装器或ZIP字节，不算新公开安装器资格。各Linux2224/646、Mac2242/646、Win2134/772，均零fail/error；
七native语义诊断case在每目标真执行通过。security独立核backend74/Window25/TLS24/streamTLS2/cleanup13/collections511各自0skip和exactsource/owner/cleanup。
本机exactfab另actualCocoa Window25/0、36.43秒、11源匹配Gitblob、fresh auth nativeARM64 RS，两个错误0/0像素已看，exactCID/两卷独立absent。
版本/GIF/tag/公开0.12安装器保持不变，这是未发布源码进展；完整ON Output/Preview/恢复/平台参考等门禁不关闭。
详见[image验证与集成](../validation-mongodb-image-modes.md)。

非Mongo源码审计确认Oracle/Snowflake适配器缺失，而九族/14供应商选择器及SQLite七页设计器已存在。
[OR18-01](../ui-reference-18-oracle.md)已实际枚举Oracle五页、Basic/TNS/service/SID/三角色/OS认证切换、
247字符集、三SSH方法及Oracle工具栏/15Other和28/13对象列；仅Basic/Default密码路径实际Test成功、保存重开、
新schema/table/view实际读出合成数据；一次ORA12545来源unknown，之后On及grid成功，不改server掩盖。
只新profile Off/0后Quitfalse，三轮研究/参考CID逐ID清除、零数据卷、DB/secret bridge均端口拒绝。
另行oracledb26.1六目标wheel实际字节/架构与ownedThin/service/TNS/SID/SYSDBA研究不等于OpenNavadapter或六平台执行。
完整Oracle工具/设置/安全/事务/类型、Snowflake及三平台UI/功能门禁保持开放。

上述参考研究之后的专属session/config/deps/owned测试与五页连接、密码状态、Store、
schema/table/view窗口路由，现已在本页开头记录的0.13候选合流；尚未合入main。
service identifier与schema继续分离，lossless NUMBER/LOB、精确cancel token、stale绑定、
无明文secret和unsupported active选项failclosed各自保留验证；写入/PK冲突/DDL等工具仍未实现。
不得把专属连接成功或后端测试当完整Oracle工具、真实窗口或安装器完成。

### 历史0.11基线

**v0.11.0已公开**，源码`87df5cd70f9293c4585637b1eab6e00e37810097`，
北京时间2026-11-13 **13:07:54**。修复后的main预检
[43367059557](https://github.com/shubolab/opennavicat/actions/runs/43367059557)7/7成功并独立审查六安装器后，
才创建正式tag；[43368650962](https://github.com/shubolab/opennavicat/actions/runs/43368650962)8/8成功。
八个公开资产已匿名下载，GitHub大小/digest、SHA256SUMS、合并manifest及正式CI六个实际安装器字节一致。
公开ARM64 DMG另完成本机原生Cocoa隔离6×4查询和全部驱动/加密/native-JS探针，未读真实profile，挂载已卸载。
正式摘要、证据限制和私有回执索引见 [0.11验证记录](../validation-0.11.md)。

公开0.11的唯一真实GIF为28阶段、371帧、61.84秒，逐阶段隐私审核通过。
本次发布加入已保存MongoDB Pipeline的四页Batch/独立会话/错误策略/持久历史，并修复正时限和启动取消。
完整工具、其余Batch类别/Scheduler/SMTP、参考UI和三平台门禁仍开放，goal保持active。

### 已发布0.12源码与后续入口

本轮发布文档收尾在独立工作树 `/Users/wangshubo/.codex/worktrees/mongodb-finite-stream/opennavicat`、
分支`codex_mongodb_finite_stream`，**0.12.0预发布已完成**，以其正式产物资格独立记录。有限UI/GIF检查点为`979d052`，
MainWindow调用点修复为`479a811`，实际Dialog Test按钮同类局部修复为`d9bee66`，
仅测试子进程ready握手修正为`1a6eeed`。本版限定DMG CREATE busy重试修复后的完整发布源码为
`b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`；默认全源2197/636，201份文件前后相同并逐Git blob匹配，应用源码字节仍与`d9bee66`相同。
main预检[43388068691](https://github.com/shubolab/opennavicat/actions/runs/43388068691)已completed/success 7/7、publisher skipped；
13 artifact/六安装器的实际字节、native/installed双manifest、sidecar和原生/安装执行收据独立审查通过，root另重新hash六文件。
两个CI DMG均首试create成功，不冒称实际busy恢复。主回执在`artifacts/release-012/main-ci-audit-43388068691/`。
据此annotated tag **v0.12.0**已创建并push，精确绑定b6e1da4；正式run
[43390347592](https://github.com/shubolab/opennavicat/actions/runs/43390347592)为push/v0.12.0，同SHA，已completed/success 8/8。
正式与公开字节均已按各自真实产物验收，正式六SHA与main六SHA不同；doc-only提交不改tag源码。
正式、公开及公开ARM smoke回执分别在`artifacts/release-012/tag-ci-audit-43390347592/`、
`public-43390347592/`和`public-macos-arm64-smoke/`。
旧`1a6eeed`的main预检[43384833779](https://github.com/shubolab/opennavicat/actions/runs/43384833779)
保持completed/failure：五原生目标和DB security成功，macOS amd64在hdiutil CREATE报Resource busy，安装后launch未到、publisher skipped；不是六目标资格。
修复前`f0b558e`预检[43383205925](https://github.com/shubolab/opennavicat/actions/runs/43383205925)
终态为cancelled，未tag/发布，partial产物不作新资格。

AP18-11在新owned RS同页确认busy两页、空/错误三页、事件四页、再次空跑移除Output；
稳定消息/输出仅为script+状态+实际elapsed，无事件网格。源码已接该有限text-only路径、
当前页Stop、private typed BSON/partial、独立no-retry client/nonce清理，以及owner校验的
有界errmsg和可选冻结脚本位置；getMore或无可靠位置时不制造line/column。
Batch手动任务已区分row/time/byte/cleanup失败，不读取errmsg写历史。
参考Preview/PreviewAll曾在独立sample后stream各出现一次崩溃，AP18-11未重放，
不能复制原版故障或据此移除正常预览门禁。原版已退出，自有fixture/卷精确清理。

当前各scope独立测量（不相加）：默认全源**2197通过/636条件跳过，82.81秒**，201份源前后相同且匹配`b6e1da4`；其余下列UI/DB scope产品源码未变，按原执行来源及未变声明源重绑，不冒称本次重跑；
实际Cocoa **100/0**（Window16+组件78+4 Window/2 Dialog生命周期，actual Cocoa、Darwin arm64、0.12、16声明源相同），
修后fresh Window **16/0**（11声明源及额外2 Dialog文件），caller连接UI **110/0**。
Window22项生命周期及Dialog真实有效/损坏SQLite两项回归均通过，native TaskSignals在GUI线程destroyed、Task/Signals弱引用最终释放；
QRunnable仍由pool autoDelete，不能泛称所有Python wrapper/QRunnable均GUI销毁。
collection **511/0**仅重新绑定48份未变声明源，未重新执行；旧198份outer快照含旧Dialog仅历史，原8容器/16卷独立absent资格有其范围。
后端**46/0**、普通TLS **24/0**、stream TLS **2/0**、stopped cleanup **13/0**同样按未变声明源重绑，不冒称新执行；
stream TLS仅standalone transport及40573拒绝，cleanup零start/零DB，普通TLS外层卷ID不完整限制保留。
两调用点后fresh recorder offline **79通过/3条件跳过**、own-RS另 **1/0**，各79源及补充Dialog回归文件绑定`d9bee66`，
握手修正未变这些字节；真实Run2个typed事件，BSON/UUID/主连接不变，自有容器/两卷独立absent，没有录制GUI。
Cocoa源smoke6×4 exit0。完整回执见 [0.12候选验证](../validation-0.12.md)。


打包合同79/0skip含11新增，per-command timeout600秒、最多3次create与1/2秒backoff，create最多1803秒另加stage/verify，不是总600秒。
本机真实native ARM64 build＋包内Cocoa6×4/all probes＋新DMG只读mount/隔离安装launch卸载均绿，
`local-installer-dmg-fix/independent-check.json`八检查通过，owned mount及image目录不存在；
DMG51,441,633字节，SHA256 `00732b3c536d17c0e94f6f7a194f37a0e88cb32b3847dffd097b83784d1786da`。
真实create首试成功，不冒称实际busy恢复；这是local ARM64，不代替IntelCI/其它目标或公开下载。
旧1a6eeed默认2186/636继续保历史，不能替代b6打包源资格。

更早parallel collection SIGSEGV失败仍保留，串行507或后续资格通过均不能证明其原因；
真实发现的两个TaskSignals调用点缺陷分别局部修复，不宣称与SIGSEGV同因。
MainWindow单调用点修后2184/636、Cocoa98属于479历史；更早2180/636、94和507也不冒称当前源资格。
`d9bee66`首轮默认全源另有2185通过/1失败/636跳过（慢child缺finalizer），原node独立重跑通过仍保留失败；
受控file-gate证明ready在try前可绕过finalizer，移入try则执行且进程回收，不证明该次失败的具体时序。
`1a6eeed`仅修测试握手、未改生产stop或放宽断言，独立offline10项及fresh默认全源均通过。
唯一0.12兼容GIF已完成31阶段/411帧/68.5秒、1,712,746字节并复制到`docs/assets/`。
31阶段首/中/尾93帧及额外全尺寸视觉检查完成，自动逐帧guard411/411通过；
SHA256为`d1ec33dd15e03fa38e5749f0ff0efe01051e5b40116c12229a7113d847e33c9e`。
录制为新认证RS的真实工作流，不渲染事件payload；演示本身不替代安装器或完整参考验收。
GIF capture源码归属`979d052`，77份源逐Git blob核定；当前产品Window/Dialog各两行ownership不同，
widgets及recorder未变并有两调用点后的新79＋1工作流资格，握手修正仅改测试。保留已审capture，不冒称当前SHA重新录制。

本轮0.12交付已完成。下一开发入口为独立工作树`/Users/wangshubo/.codex/worktrees/mongodb-stream-variants/opennavicat`、
分支`codex_mongodb_stream_variants`，源码`94de386259a443fd8a2ce3e711ade58b93dfe58d`；
实际19 Window/46 backend仅为后续本地范围，不进入已发布b6或本版正式资格。
AP18-12参考文档提交a189508保留已观察游标语义及整体inconclusive/初始调用基数未知边界；继续variant/完整工作流和全部矩阵门禁。
原始崩溃因果未知与完整资源门禁仍保留。
正常stream预览、完整partial/恢复/options/拓扑、其它工具与三平台参考门禁继续开放，goal保持active。

以下各节保留本轮恢复、初次预检和修复的时间顺序；其中0.10仍为最新/0.11待发布等
**恢复时描述已被本节及新state覆盖**，不能重复当作当前状态。

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

## 恢复时的公开版本与main核实（历史起点）

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

## 恢复时继续顺序（历史）

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

## 后续分支的已推送后端检查点与正在进行的UI

`codex_mongodb_finite_stream` 的后端检查点 **1841c01** 已提交推送，
随后合入仅文档的main/ee876b5，合并提交 **068b1dcd60964f5e5188df25ebc4fe5bb371137a**。
在UI整合开始前，构建固定native JS helper后的全源为 **2073 passed / 619条件跳过**，74.50秒；
新fresh独立RS模块26项（12纯合同/14真实RS）、普通Mongo TLS24项、stream transport2项、
stopped cleanup13项均零跳过，另10项离线strict-gate检查包含真实慢子进程SIGINT/finalizer/回收。
四scope源码哈希仍匹配该后端检查点；20个exact container ID和36个已记录卷ID独立确认不存在。
普通TLS fixture自己确认rm--volumes，但未记录单卷ID，不把这些卷加进独立计数。
私有回执在新树 `artifacts/finite-stream-freeze-20261113/`；这些范围重叠，不能相加或借用0.11安装器资格。

后端禁止初次aggregate隐式重试，owner按nonce清理未知初次cursor，字节超界明确失败并保留partial/undelivered事实。
清理脚本的named64hex卷边界已红绿实测，显式/继承挂载在任何rm前拒绝；测试仅新建stopped资源，零start/零DB。

UI正在该分支继续：依据AP18-10接text-only消息/输出、当前页Stop与typed返回路由，
并让Batch明确处理row/time边界，不能把generic事件表或无条件success当作参考验收。
本节的全源/源码哈希资格属于UI修改前的冻结后端；后续UI需重新真实Window/RS、
本机与六目标CI验收，演示变化时重新录制/审查唯一GIF。该分支仍未发布，完整goal保持active。
