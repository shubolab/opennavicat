# 0.13.1 图标与 Oracle 候选验证

2026-11-14，Asia/Shanghai。完整 goal 保持 active。该里程碑继续实现 Navicat 18 的
完整工具与 1:1 保留界面，不能用本页的有限检查关闭 [parity matrix](parity-matrix.md)。

## 源码与当前交付状态

应用源码冻结为 `ca4d20a858c964d18eff53e97664e4b32d403cc9`，版本字段、项目和 lock 均为
`0.13.1`，已推到 main 与 `codex_oracle_basic_integration`。声明的运行、测试、构建、
打包、workflow 和许可证输入共 242 份；README、文档与演示资产不在此源码哈希范围内。

当前最新公开安装器仍为 [v0.12.0 预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.12.0)。
新 main [run43511903905](https://github.com/shubolab/opennavicat/actions/runs/43511903905)
已经失败：三个必需 job 成功、六个失败、publisher skipped。没有进行完整安装器资格审计。
其后测试/录制/启动诊断修正仍须冻结新源和重新验证，正式 tag 与公开下载资格均未取得。

旧 `v0.13.0` tag 保持在 `81523aa4ff5b8734776f5bf0b6e7217ef0e192f7`；
[run43487011620](https://github.com/shubolab/opennavicat/actions/runs/43487011620) 在 macOS Intel
fixture teardown 失败，publisher skipped，未发布安装器。早期 Git211 main 预检成功不能
抵消该失败，也不能代替本版资格。失败记录和原 SQLite allocation 原因未知的限制均保留。

## 本轮实现

- 独立几何绘制主栏、对象、九驱动、供应商与常用小动作；主栏 28、树/页签 16、查询
  选择器 14、连接网格 50/列表与筛选 16 逻辑像素分别接入。连接、开关、禁用分别处理，
  高 DPI 按目标尺寸绘制，保留未实现功能的禁用状态。
- 原生页签关闭按钮改为灰叉，保留平台侧别和执行/保存保护，并销毁被替换的旧按钮。
- 按实际原版观察将 Oracle 常规端口改为纯文本框，Up/Down 不改变端口，取消额外 stepper。
  Basic 在保存/测试前严格校验端口；TNS 未启用的 Basic 原始值及类型保留，不启用 TNS 连接。
  非法端口输入保护属于本应用行为，原版非法值交互仍未验收。
- 配置栏按钮使用蓝/灰 pane 与灰圈加号。通用连接页复用图形，保留文字、布局和原状态；
  不能据此声称已观察其它数据库的对应参考页。
- 修正固定表单宽度与右对齐共同造成的字段右边缘裁剪，按每层祖先实际区域交集检查，
  保持 300px 字段和外层居中。完整大字、滚动和其它表单仍开放。
- 两次实际搜索确认 Atlas 使用 MongoDB 橙叶、Redis Enterprise Cloud 使用 Redis 粉 R。
  选择器更正为 12 供应商筛选和 2 个直接数据库别名，保留旧 14 组目录中的全部别名。
  完整参考云卡片排列、动态筛选、最近使用和云端认证仍开放。

参考身份、Evidence IDs、独立绘制边界与未观察项见 [图标专项](validation-icons-navicat18.md)。
原版图像和原始证据仅保留在忽略目录，不提交为产品素材或进入包。

## Git ca4 本机资格

| 范围 | 实际结果与限制 |
| --- | --- |
| 默认全源 | 2693 通过、662 条件跳过、零失败/错误，3355 个 collect/run/JUnit 用例精确一致，112.521 秒。`PYTHONWARNINGS=error`；242 声明源及原生 helper 前后相同并匹配 Git。所有外部 DB opt-in 清空，跳过不算真库执行。 |
| 合流 Cocoa 窗口回归 | 七模块 249 通过、3 条既有真库条件跳过、零失败/错误；252 用例 Counter 精确，242 源前后/Git 相同，实际父进程 Qt 平台 Cocoa。生命周期回归另开 offscreen 子 pytest，未混称 Cocoa。 |
| 本机应用 | Darwin ARM64 bundle 构建与隔离 smoke 退出 0，样例 SQL 6×4，Oracle26.1 四项与其它13项探针为 true。传入 Cocoa 环境；smoke 报告本身不记录实际 QApplication 平台，不能借此代替实际窗口证据。 |
| 包内静态审查 | 24 项通过；完整 79 应用模块及入口等同当前/Git 源码，61 Mach-O 均支持 ARM64，4 个 Oracle 扩展机器码匹配官方 wheel ARM64 slice；32 driver Python、55 wheel payload、许可证、95 个包内链接分别核对。不是安装器或六目标验收。 |
| 实际自有界面 | Cocoa 屏幕 DPR2 下生成数据的主栏/查询/表/选择器亮暗 12 张，以及通用 SQLite/PostgreSQL profile On/Off 亮暗8张，共20张。截图范围、源绑定与目视审查分开保存；不是原版或三平台验收。 |
| 实际 Oracle 窗口 | fresh owned Oracle 的实际 Cocoa 三用例通过、零跳过；真实 Test、保存不自动连接、重新打开、临时密码、schema/表/视图/Decimal，以及计算视图 Stop、同会话复用、Disconnect/Shutdown。独立106项结构/资源检查通过，24声明输入前后/current/Git相同；28阶段图/16种唯一画面的目视审查单独保存。 |

私有本机证据位于 `artifacts/release-0131/` 的 `default-ca4/`、`cocoa-targeted-ca4/`、
`native-ca4/`、`native-ca4-static/`、`app-cocoa-ca4/`、`oracle-cocoa-ca4/` 和
`oracle-cocoa-ca4-independent/`。源码运行、条件数据库测试、打包和人工对照是不同门禁，
不将计数相加成更大的覆盖范围。

原 CI teardown 的窄修正把原 cleanup 放到 pytest-qt 的 `before_close_func`，在框架合法
close/deleteLater 之前关闭会话；真实框架送达 DeferredDelete 的子进程回归保持原版本红、
修正后绿。未修改产品生命周期、增加 invalid guard 或放宽预算，也未屏蔽 SQLite warning。

## 演示与尚未完成门禁

35 阶段的新 GIF 已由 ca4 实际录制：471 帧、78.5 秒、1944860 字节，摘要
`14e0d206f81e47873bf7af765ab944a11a8019dac7c5e37e38b25aedc6c79711`。全帧文本保护和独立
解码通过，35 全尺寸中帧及 35 首/中/尾联系表实际看完，无隐私或缺 glyph 阻断；但 Oracle
卡片在 GIF 中明显偏棕，与实际 own PNG 的鲜红不同，**视觉审查失败，未复制 README**。
编码器必须取得颜色、文字、帧序/时长和尺寸对照，再用新源实际录制，不能将离线诊断
重编码称为新演示。生成数据/空 Oracle 表单/空选网格均保持原保护；Oracle 表单不是 DB
执行证明。卷与临时路径清理仍只记 producer 断言，没有升级为逐 ID 独立验证。

main 的 Linux 两架构与 Windows ARM64 失败在旧测试写死96行高，实际字体下长别名需要
113或108；产品自然增高保留全部文字。Windows AMD64 崩溃在测试逐子控件 setStyle，
macOS Intel 的子 pytest 在 setup 前遇到 OpenSSL legacy-provider warning；后者另已证明
既有 packaging hook 测试有环境泄漏，修复后仍待 native CI。Oracle ARM64 失败在服务准备，
未执行 backend14/window3；原回执没有安全原因，超时仅为推断，续作加诊断而不抬预算。
这些失败及修正见[当前 snapshot](snapshot/2026-11-14-goal-resume.md)。

六原生目标、正式 tag、匿名八资产与公开 ARM64 DMG 的本机隔离 smoke 继续单独验收。
Windows/Linux 实际参考界面、完整主题/悬浮/焦点/失活状态、全部菜单/向导的小元素及
Oracle copy/关闭/数据库列表等尚存 Unicode 控件继续开放。

Oracle Query、事务、写入、设计器、Data Pump、Other、其余认证/安全/高级设置、完整类型
及 server-side SLEEP 的及时取消限制继续由 [Oracle 记录](validation-oracle-0.13-candidate.md)
和完整矩阵追踪。本轮端口与图标改进不缩小这些目标。
