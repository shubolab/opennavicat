# 10 小时恢复工作记录

> **最新收尾要求**：用户已将预算改为半小时。原十小时截止被 **2026-11-14 17:16:42 UTC / 北京时间 2026-11-15 01:16:42** 取代。以下早期十小时计划为历史；最终状态见文末收尾记录。

用户于 2026-11-14 再次要求继续，给出 10 小时预算，并要求到时无论进展如何都收尾更新 snapshot。
本轮开始时间为 **2026-11-14 14:43:06 UTC / 北京时间 22:43:06**；硬截止为
**2026-11-15 00:43:06 UTC / 北京时间 08:43:06**。此前因额度不足的停止要求已被本次继续取代。

完整目标和开放门禁仍见 [parity-matrix](../parity-matrix.md)。上一轮交接见
[小控件 snapshot](2026-11-14-small-controls.md)，其中 CI pending 状态需以下述新观察为准。

## 恢复时核对

- 当前远端主线 `adf9221ecac6b51d27ccc56cea9acc6d4d8cc819`；工作目录为本机
  `/Users/wangshubo/.codex/worktrees/sqlite-databases/opennavicat`，本地分支跟踪 `origin/main`。
- main CI [43603141977](https://github.com/shubolab/opennavicat/actions/runs/43603141977)
  已完成失败：六个桌面任务及数据库 TLS/SSH 任务成功；Oracle amd64、arm64 均在启动 fixture
  阶段失败，backend 14 项只完成收集。没有据此创建 tag 或发布 v0.13.3。
- 两个 Windows 目标的上一轮字体断言修正已在该次实际 CI 中通过。六目标原生 SQLite UI
  scoped 门禁与安装器构建/安装 smoke 任务成功；尚未审查这次全部六安装器字节。
- 只下载九份小型 validation ZIP 并核对传输身份；未据 Oracle 失败尝试发布。
- origin 仍只保留 main 和有未提交工作的 `codex_mongodb_finite_stream`；本轮并行分支留本地。

## 正在推进

- 定位两个 Oracle fixture 退出 24 / ORA-04021 / ORA-65048 的实际阶段；保留原失败，不加盲目重试。
- 修复现有画面中确定复现的 inspector 标题及 pipeline 源节点裁字问题；完整 U15 继续开放。
- 参考 Navicat 18.0.5 build321 的自有 SQLite 多附件与无效配置规则，保留运行 Evidence ID。
- 完整默认测试增加独立 collection、run collection、JUnit 对应及源码身份记录，避免只凭 JUnit
  或部分静态 membership 声称完整集合已验收。

本文件是恢复中的工作记录，最终截止 snapshot 将补充提交、测试、CI、发布和剩余项；
不能将正在推进的项目当作通过证据。

## 首个整合节点（仍在继续）

Oracle 有限日志阶段分类已整合；本机全新 pinned 原生 ARM server 的 14 项通过，客户端是
macOS，未复现 CI 的退出 24，不能当作 Linux CI 根因已修复。新 Windows 完整测试 owner
通过创建时 JOB_LIST 绑定、挂起后验证再恢复线程，给出 Job 后代清理和句柄回收记录；
本机仅能验证非 Windows 合同，11 项 Windows 实测留给新主线 CI。

Pipeline 卡片保留原尺寸和原文，改为明确右省略并保留完整提示与可访问文本。参考又实际
观察到信息栏长连接名右省略且 AX 保留全文，因此信息栏标题也复用既有纯文本省略控件。
本机相关 48 项通过；后续含 Windows owner 合同的一组为 22 通过、11 条件跳过，未冒称
Windows API 已本地执行。完整 U15、三平台参考和全部状态仍开放。

新默认 suite runner 的首次完整运行在冻结 `be2d2c9` 下得到 3,802 项：3,137 通过、
662 跳过、3 失败；完整 collect/run collection 和源码前后相同。三个失败均为新 runner
禁用插件自动加载后，既有隔离子测试没有继承命令行 `-p`，找不到 `qtbot`。已用显式
`PYTEST_PLUGINS=pytestqt.plugin` 传给后代，真实隔离 pytest 回归和三个受影响用例通过；
原失败 JUnit/log 留在 `artifacts/release-0133/resume-10h/default-collection`，不回填。

默认输入枚举现含 JS、native C、JSON fixture 和打包资源，并拒绝未跟踪相关输入；
raw/processed JS helper 共七份现有输入单独校验来源和前后哈希。CI 必须具备 raw helper。
collect/run runtime 另记录该 host 的 Qt style 清单；这不替代 actual QPA 或参考验收。

## 新增验证与 Windows 竞态

冻结 `b0e407af5f8f8ad4e9fd269270c39e6808d00265` 的本机完整默认 suite 已通过：
3,825 项=3,152 通过、673 条件跳过、零 failure/error；两个 collection 序列与 JUnit
完全对应，272 源输入和七份 helper 输入前后相同且与 Git/receipt 对应，隔离目录已清理。
新增 11 项跳过为 Windows Job 原生测试，不把本机结果当 Windows API 验收。

同源 CI `43607997557` 的 Oracle 两架构已成功，原失败不能据此推断具体锁成因。
Windows amd64 完整 suite 为 3,843 项=3,031 通过、811 跳过、1 失败：只在 membership
故障注入的主进程回收断言失败；外层真实 Job 的 334 个进程均已归零、句柄已关闭。
故障路径先请求异步 TerminateJobObject，又尝试 TerminateProcess 同一个正在终止的 leader，
可能因 AccessDenied 跳过等待。修正记录成功的 job 终止请求后直接等待自有 leader；
保留故障注入并增加完整 result 失败详情。相关本机 46 项通过、11 原生 Windows 条件跳过，
真实 Windows 修正后结果仍待下一 CI，不假装本机能复验。

六模块 67 项的 Cocoa 新门禁已在隔离工作树实际零跳过通过，93 项 scope 源输入与 Git
对应、collect/run/JUnit 相同，actual QPA=cocoa，keyring/spawn/可见窗计数均零，进程和
Store 已清理；参见 [原生门禁](../native-sqlite-ui-qualification.md)。新主线会执行此扩展
scope，并上传完整 helper 字节以便独立审查默认测试的全部输入。

源码 `487584d5ea1ee6357c49c46566cf2d4e91b81345` 已新录 38 阶段/522 帧的真实 Cocoa
演示，源与 helper 前后核对通过；GIF SHA256 为
`030070a8a0866001a4983b89f559737ef1da4444153a51dddfa2f229c1ae61d9`。
录制成功与 README 素材验收分开，完整画面/隐私/颜色审查尚在进行，暂未替换旧 GIF。

## 第一小时后的整合检查点

上述素材后续已完成新的全部 522 帧 Qt/ffmpeg RGBA 对照、38 阶段代表帧人工审查及
精确录制容器/两匿名卷不存在复核，现已原样替换 README 唯一 GIF 与公开 JSON 收据。
圆点前景 34/34 源色精确；驱动主色众数 162/198 精确，其余 MongoDB/Atlas 众数偏差 1，
源色在同区域仍大量存在。all-primary-exact 仍 false，未修改应用/GIF/区域来消除差异，
无原始 PNG 不能归因。详见 [最新开发验证](../validation-0.13.3.md)。

Oracle 新初始化顺序在冻结 `1b4f87173aad1812e41f5a39051b56d3a316c1dc` 上，本机新
原生 ARM 服务 backend 14 项、另一个全新服务 window 3 项均零跳过通过；macOS 客户端，
window actual QPA=offscreen，来源/会话/容器/密码清理通过，不等于 Linux 客户端或三平台
参考验收。后续再清除未知 healthcheck exit 后的旧合法 code，新增 (5,127) 回归，相关
72 项通过；这项小诊断修正未回填至原 1b4 结果。360 秒仍是原 readiness 预算检查，
不扩大称所有进行中认证都有严格相同 wall-time 上限。

新实际加密参考已完成：十四个 provider 生成配置各在 Navicat 中查询两行；AES256 一个
参考写出文件又由独立 provider 只读 `quick_check`/两行核对、错 key 和普通 SQLite 拒读
验证。Ascon legacy 在现有及新建模式均真实报配置失败，新建留下零字节文件；不据此
删除参考候选或宣称所有输入都不适用。两个保存密码控件、进程内历史与 fresh 草稿默认
分开记录；fresh cipher 当前值为空，未默认选择算法。
见 [加密实际互读](../sqlite-cipher-runtime-2026-11-14.md)与
[fresh 默认](../sqlite-connection-fresh-defaults-2026-11-14.md)。

本轮新建的两个参考测试 profile 均在各自当次用户确认后精确删除，临时数据库/副本/
缓存目录清理通过，参考执行文件与 provider 原件哈希未变；未修改既有用户连接。
provider 原生适配层的独立本地叶 `d4b9103a497fe3371c3f79eeba81f22ec96ff8b5`
有 89 项真实 macOS ARM 测试及冻结 probe，尚未接入产品 backend/UI/依赖，未合入 main；
十四份生成加密原件是后续实验资产，保留在该本地叶工作树的忽略目录，不随包分发。
其后发现的跨 cipher 备份损坏探针和拒绝边界必须保留，不能把未集成实验当产品支持。

main `4855e8f` 的六个桌面任务（含 67 项原生 UI、完整默认 suite、打包及安装 smoke）
均成功，但 Oracle ARM 仍失败，整条 run43609330345 为失败，publisher 跳过。
当前修正/素材/文档的下一轮主线 CI 尚待此检查点推送；没有 v0.13.3 tag 或 release。
完整目标继续推进，原硬截止不变，到时必须提交与 snapshot 收尾。


## 完整 helper 字节审查发现的问题

冻结 `e942ab0b15b721d4fdda237cb54e4a6f5981b12d` 本机完整默认测试为
3,829 项：3,156 通过、673 条件跳过，零失败；源码、helper 与两次 collection/JUnit
对应。远端 main run [43613934365](https://github.com/shubolab/opennavicat/actions/runs/43613934365)
的九个必需任务全部成功，包含六目标安装 smoke 与两架构 Oracle，publisher 按 main 跳过。

九份 validation ZIP 已逐份核验 API 身份、大小、摘要、CRC 和解包文件树。安装器下载
前的独立审查发现 Windows 默认测试记录的 raw helper 与末尾上传的 helper 字节不一致，
因此停止在小收据阶段，未开始大安装器下载、未打 tag、未发布。这不否定测试前后相等
的原记录，但末尾文件不能冒充测试实际使用的文件。原失败与原 ZIP 全部保留。

runner 现增加只写一次的 `helper-inputs/` 实际字节快照与映射；它在任何 collect/run
子进程开始前核对并保存原 helper，结束时再核对快照、映射和原路径的前后哈希。
后续打包重新编译不再覆盖测试使用的字节证据；独立审查必须按新源码能力读取快照，
历史 e942 不回填。新增真实文件篡改/路径/覆盖控制，runner 23 项通过。下一冻结候选
仍需重新执行完整本地与六目标 CI，以及全部安装器字节审查。


## 按最新半小时预算收尾

用户于16:46:42 UTC将预算改为半小时，原十小时硬截止被17:16:42 UTC／北京时间01:16:42取代。本轮停止新增功能，整合后代码已在main冻结 `6d6f906573829934e43109584e2a43d278161c05`，版本0.14.0开发候选；工作提交及随后文档/GIF收尾均推送同一main，不新增origin任务分支。完整还原目标未完成，按用户要求停止本轮，后续恢复先查远端CI。

已接SQLite加密高级页、15候选/14实际格式、主/附件独立key和来源绑定、OS keyring选择、后台独占创建/关闭及取消回滚。真实AES256 Good→Store→Open→两行→readonly/wrongkey/标准库拒读及密钥不落盘窗口链已通过；早先暴露的GUI关闭已修到加密会话串行worker。外层事务若被SQLite native interrupt整体回滚则明确报告，不重放历史。未实现的产品加密备份在创建目标前明确拒绝，独立helper不代表产品备份完成。SQLite2/user-auth/rekey/附件继承/设置位置/自动连接/初始查询、全部工具及三平台参考继续开放。

最终完整默认验证为4,294项：3,621通过、673条件跳过，零失败/error；286源输入、149模块、七份helper、collect/run/JUnit与Git身份/清理通过。c00原六失败及录制失败保留，修正后288通过/3条件跳过和完整重跑分别记录，未回填。[0.14验证](../validation-0.14.md)及[有界收据](../evidence/014-default-6d6f906.json)含范围。

0.13.3候选4f5a914的run43615785473九必需任务成功，15份产物/六原生安装器独立字节审查通过，但未正式tag/发布。新0.14 [run43619065100](https://github.com/shubolab/opennavicat/actions/runs/43619065100)在收尾捕获时3项成功（两Oracle与TLS/SSH），六桌面任务仍进行中；不得借旧包关闭新六目标门禁。最新公开版仍v0.13.2预发布。

README唯一GIF已更新为6d6实录，38阶段/521帧/约86.84秒，SHA639be8c6ec9276b93dac0601a55ff34b30e85f931c81db32e9b2203ec3b80a06。38全尺寸代表帧已分工逐张目视、全帧双解码通过；primary色mode全匹配，secondary及裁切等限制见验证文档。新容器/两卷额外独立exact-ID不存在核验仍待，不复用旧487证明。

参考中本轮两个获当次确认的测试连接及其临时文件/副本已精确清理，未再创建参考profile。14份provider生成原件/六wheel/原失败证据和本地研究分支保留为唯一实验资产；两次capture及default的owned进程均已回收，source收据记录临时Store/帧目录清理。origin仅main和有dirty工作树的codex_mongodb_finite_stream，后者保留。

恢复顺序：先核当前main与6d新CI结果；补0.14六安装器字节/实际各平台scope和正式发布检查；新审查readerprepared已有精确6d源码计划（base4240+18×实际Qt styles），但没有对0.14执行完整六包审查。继续剩余SQLite设置/备份和完整parity矩阵，不把局部通过当产品完成。

最终新GIF审查manifest SHA256：`85b5941ffe5efed464e8e390b166fe8c5238c099172c3cbce5acea8dd75284c0`；私有完整审查在本机 visual-clipping-0133 工作树的 `artifacts/gif-review-6d6f906/`，不随clone自动取得。
