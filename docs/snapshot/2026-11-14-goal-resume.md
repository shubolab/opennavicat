# 2026-11-14：图标专项合流，0.13 候选继续验收

完整 goal 保持 **active**，没有暂停或完成。当前重点来自用户新增要求：尽可能对齐
所有图标和小型视觉元素。完整数据库工具、1:1 UI 和 Windows/Linux/macOS 参考验收
继续由 [parity-matrix.md](../parity-matrix.md) 追踪，不能将本轮图标或安装包视作产品完成。

## 当前恢复点

**最新状态：正式 `v0.13.0` 尝试失败，尚未发布。** tag 固定在
`81523aa4ff5b8734776f5bf0b6e7217ef0e192f7`，应用/测试/build 声明240源仍等同 Git211；
[正式 run43487011620](https://github.com/shubolab/opennavicat/actions/runs/43487011620)
最终八 required success、macOS Intel failure、publisher skipped。失败发生在真实 designer
用例成功后的 fixture teardown，访问已被 Qt 销毁的 QThreadPool；不是发布成功。
独立真实 pytest-qt 合法 close/deleteLater 后送达 DeferredDelete，复现相同错误；原 fixture
把 cleanup 放在 yield 后，依赖删除落地时机。窄测试修正正在 `codex_qt_fixture_teardown_fix`
中验证，把 cleanup 移至 before_close_func，不用 invalid guard 或修改产品/预算绕过。
另有原 CI 的 SQLite unclosed warning，缺 allocation traceback，仍不作同因果结论。
保留失败 tag/run；后续修正及小控件续作合流后，以新补丁版本重新资格，不改旧 tag。

原版新补充观察也已保存：Oracle General 端口为纯文本框，Up 不改1521，没有 stepper；
配置栏开关是蓝/灰 pane 图形，新增 profile 是灰色圆圈加号。原型 `_PortSpinBox` 的额外
箭头并非参考行为。`codex_small_controls_18` 正在隔离实现纯文本端口与专用图形。
新证据已 export/close REA，未 Test/Save/连接任何数据库，pane/grid 状态还原后正常 Quit。
以下 main 预检、本机/演示记录依旧有效，但不能抵消正式失败或冒充后续新源资格。

- 当前应用源码检查点：`211fef0ec0c7edbca2c016d0fd887e4acd977c90`，已推到 main 和
  `codex_oracle_basic_integration`；工作目录为
  `/Users/wangshubo/.codex/worktrees/oracle-basic-integration/opennavicat`。
- 版本字段为未发布的 **0.13.0 候选**。最新公开版本仍为
  [v0.12.0 预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.12.0)，精确发布源码
  `b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`。不要修改旧 tag 或借用其安装器资格。
- 前一完整历史见 [11月13日交接](2026-11-13-goal-resume.md)；其中后续工作、候选检查点、
  GUI 与源码测试待办，以上述当前源码及本页的新证据为准。机器索引见
  [2026-11-14-state.json](2026-11-14-state.json)。恢复时仍需重新 fetch 核对实际 refs。

## 本轮图标与状态

独立重画主工具栏/对象、九驱动、十三个已观察供应商及常用动作，修正原型沿用的
圆柱/四格占位、错误动作语义和尺寸。实际参考使用 Navicat 18.0.5 build 321 与生成的
SQLite 连接，进入表、查询和连接选择器；退出前该连接 Off/会话 0，正常退出并清除临时
SQLite 文件。原始参考图像只留忽略目录，未提交或进入产品。身份、Evidence IDs、
颜色空间转换、未观察项与验证范围见 [图标专项](../validation-icons-navicat18.md)。

主栏 28、树/页签 16、查询选择器 14、连接网格 50/列表与筛选 16 逻辑像素分别使用；
连接状态、开关与禁用分开，高 DPI 重新绘制。七个页面的专注与 MongoDB/管线动作
接入相应语义。原有未实现功能保持禁用。页签关闭符号改用灰叉，保留平台侧别和保存/
执行保护，替换旧关闭按钮时明确释放，独立 100 次循环没有隐藏控件残留。

仍开放：全部小元素清点、未观察的 Redis Enterprise Cloud 供应商符号、失活主窗口
工具栏变灰行为、完整主题/悬浮/焦点/禁用差异、Oracle 表单少数 Unicode 控件、全部
对话框/向导及 Windows/Linux 参考。自绘近似形状和组件通过不能关闭 U15 或其余 UI 门禁。

## 当前源码的本机资格

- 默认全源：**2633 通过、662 条件跳过、零失败/错误**，139.274 秒；3295 个收集、执行和
  JUnit 用例精确一致。240 个运行/测试/build/packaging/workflow 输入前后与 Git211 精确
  匹配，原生 helper 前后相同。条件跳过不是实际数据库执行。
- 图标工厂各 offscreen/Cocoa 104/0；连接选择器 72/0；七页动作 217/0，但明确排除 24 个
  真库用例；实际窗口状态 Cocoa 5/0 与既有生命周期 45/0 是各自独立范围，不相加冒充全覆盖。
- 当前源码 macOS ARM64 应用已构建，隔离 Cocoa 请求的 packaged smoke 退出 0，6×4
  生成查询与 Oracle26.1 四项原生/thin 探针通过；静态 23 项通过，完整 79 应用模块与源码
  一致、61 Mach-O 均 ARM64、95 链接有效。只构建本机 app，未将其称作安装器或六目标资格。
- 当前源码 fresh owned Oracle 的实际 Cocoa 窗口专项 **3/0**，包含真实 Test/不保存密码/
  保存重开/Open/schema/表/视图/精确 NUMBER，以及服务器计算视图 Stop、同会话复用、
  Disconnect/Close 两条退出路径；24 份专项输入前后匹配 Git211。原 SLEEP 及时取消、
  其它认证/安全/高级设置和完整 Oracle 类型、Query/写入/设计器仍开放。
- 图标前的全源 `6af3109` 曾发生 GridFS 预览 15 秒超时；本轮最终全源通过，但原原因仍未
  证明。测试 host 信号生命周期另有独立红绿证据，不据此推断历史超时已因它修复。

私有证据分别在本树 `artifacts/icon-parity-18/default-source-211/`、`native-211/`、
`oracle-cocoa-211/` 与 `oracle-cocoa-211-independent/`。原本 app 和真实数据库配置未被
替换或导入；macOS 本机副本与用户正在运行的旧应用是不同交付状态。

## README 与 CI

新 GIF 使用冻结源码实际 Qt Cocoa 画面、隔离生成的 SQLite/MongoDB 数据和空 Oracle
连接表单，34 阶段、457 帧、76.17 秒；只保留 README 的同一份 GIF。摘要及逐阶段隐私
审查与复制状态见机器索引/演示回执，不将 Oracle 表单画面当作 Oracle 数据库执行证明。

当前 main 新 CI 为 [43473238808](https://github.com/shubolab/opennavicat/actions/runs/43473238808)，
exact `211fef0`，启动于北京时间 2026-11-14 01:29:07；已完成：九个必需 job 成功、publisher 按 main 规则 skipped。
六原生桌面 + security + 两原生 Linux Oracle 的实际 runner provenance 已核对。15 artifact
已下载：六安装器、六桌面 validation、security、两个含后端/窗口子目录的 Oracle combined。
六份安装器实际字节、两类 manifest、sidecar、native/installed/QuickJS 与 Oracle26.1 四项
探针分别通过；两原生架构各 backend14/Window3 required/JUnit/source/cleanup 单独通过。
两个只读 DMG 挂载均已 detach。详见 [0.13 验证](../validation-0.13.md)。这仍是 Git211 main
预检，不能把其安装器摘要代替下一正式 tag 或公开下载。

## 继续顺序

1. 完成 `codex_qt_fixture_teardown_fix` 的实际 framework teardown 红绿与独立审查；
   完成 `codex_small_controls_18` 的纯端口/profile 图形及实际 Cocoa/原版边界验证。
2. 合流后采用新补丁版本，先冻结新源码、全源/Cocoa/本机 bundle 与变化后的同一份 GIF
   各自资格，再做六原生目标和正式新 tag。`v0.13.0`/81523/43487011620 保留失败历史。
3. 正式发布前重新核六安装器、checksums、合并 manifest、匿名八资产，另做公开 ARM64
   安装器隔离 Cocoa smoke；不沿用旧main预检或失败正式tag的文件。
4. 继续未对齐小元素与完整数据库工具。Oracle Query 后续要先处理窗口关闭前事务决策、
   DDL 隐式提交等原有审查事项；不能把普通 SELECT 支持包装成完整查询/事务实现。

文档、图标、演示、本机 app、main CI、正式 tag、公开下载及完整参考人工验收分别记录。
不清理未合并分支或带改动工作树，不在一个里程碑完成后再次暂停 goal。
