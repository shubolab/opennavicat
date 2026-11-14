# 10 小时恢复工作记录

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
