# 0.13.3 开发记录：SQLite 数据库页与附加库

当前公开版本仍是 [v0.13.2](https://github.com/shubolab/opennavicat/releases/tag/v0.13.2)。
本页记录尚未发布的 0.13.3 本地集成，不继承上一版六目标安装器、正式 CI 或 GIF 的资格。
完整目标保持 active；本片段不关闭全部连接、工具或三平台视觉门禁。

## 参考与实现范围

[18.0.5 实际数据库页观察](sqlite-connection-databases.md)分别记录九个草稿检查点，
以及五个普通附加库 Test、保存 Off、显式打开和同进程保存重开检查点。
参考中的空密码加密草稿被接受，只能证明草稿状态，不能证明文件加密或成功连接。

本地实现增加「数据库 / 文件 / 已加密」三列表格、裸加减按钮和独立新增/编辑 sheet。
新增行初始不选中，无选择删除无效果；双击重开编辑，取消不改变原草稿。
加密列显示是/否，文件路径单行省略，密码行单独缩进；默认 sheet 为 600×215。
浏览、编辑和内层确认只修改当前连接草稿，外层取消不保存配置或凭据。
新 SQLite 连接保存后保持关闭，需显式打开；未据此猜测已有活动连接的编辑行为。

普通附加库配置保存稳定行 ID、别名、文件和加密标记。密码及其来源绑定只留进程内存，
不写配置 JSON；文件、加密标记或连接身份改变后不复用旧密码。界面先固定相对路径的
绝对含义，再接受新凭据输入。加密执行仍明确拒绝，未选择或声称兼容某个加密 codec。

会话打开在串行 worker 中执行 main 和附加库初始化，所有步骤成功才发布候选连接。
附加库只打开已有文件，按连接只读状态使用受控 URI；别名按标识符引用，文件值绑定。
现代连接路径拒绝未实现的认证选项，保存的旧配置缺失 open-mode 时按 existing 解释；
显式 legacy 路径仍有历史行为差异，不能据普通连接通过关闭完整兼容门禁。
实际 SQLite ATTACH 数量限制和各 catalog 的有界读取有局部测试；读取结果有界不等于
数据库执行工作或原生打开动作都可及时中断。初始化失败不会猜测性删除用户新建文件。

比较应用另外检查实际 namespace 文件、保留的会话对象和配置指纹，换绑或重连后拒绝
旧预览。备份在 worker 捕获选中 schema 的实际 source，文件选择结束后再次验证，
用 SQLite backup 的对应 schema；不会把选中 aux 的请求静默备份成 main。
关闭 worker 忙时，GUI 不读取已关闭 native connection 的事务状态。
取消代次检查与候选会话发布共用短锁；取消先到时不发布该连接。

这些是有限实现与自有文件验证；同路径文件被替换、同会话 memory alias 换代、原生
open/stat/prepare 的及时中断、完整备份/比较工作流仍需单独验收。

## 保留的失败与后续修复

固定源码 `8005162a8358039afb0af78b51212d8929bf1c9f` 的首次实际 Cocoa 集合为
720 项：711 通过、6 失败、3 个既有数据库条件跳过、0 error。源码 260 个输入前后与
Git 一致；子进程正常结束，隔离数据清理通过。该失败结果保留，不改写成后来通过。

两项失败来自录制器后台 Store 快照未携带新的进程内凭据绑定缓存，实际异常类型为
AttributeError；子录制进程明确使用 offscreen，不能把这个问题归为 Cocoa 样式问题。
录制器增加独立缓存副本，未放宽隐私封装；同字节真实六/七阶段用例从两失败变两通过。
相关两个模块为 144 通过、3 个既有数据库条件跳过，不代替完整新录制或全源资格。

另外四项失败是 QMacStyle 在纯父字体变大时仍给输入框 21px 高度，内容区不足以放下
实际字形。后续输入框根据 font metrics、text margins 和实际 SE_LineEditContents
计算自然高度，并让 name/file 字段占满表单列；没有改成 Fusion 或缩小字体。
新增七项最终同字节测试在旧 f223 源码实际 Cocoa 全红，修复后全绿。
该修复叶的定向 Cocoa 126 通过、offscreen 230 通过，两个缩放档各 22 通过；集合有
重叠，不相加为全源总数。12/20/36px 实测内容区高度 23/31/51，字形高度 15/23/43；
默认 12px 保留 600×215，密码左沿179、右沿580。实际按钮内容区也容纳字形。
全局 QSS 优先级及移除后的父字体继承保留；恢复小字体会恢复控件自然高度，已扩大的
外层窗口按 Qt 常规行为可保留原尺寸，不声称自动缩回。

ROOT 已逐张查看三张原始 Cocoa 组件图片，并独立确认两个捕获进程不存在；图片仅在
私有 artifacts，未进入 Git 或安装器。这不是完整主窗口或 Windows/Linux 对照验收。

两处 SQLite 文件浏览现在共用三点按钮，默认应用深/浅主题实测 20×23，实际内容区
12×15 可以完整容纳三点。主题只按专用 objectName 调整 padding，组件不持有局部 QSS
或复制字体；局部 QSS 曾钉住旧字体的失败候选保留。右侧数据库示意图改为独立绘制的
窄透明轮廓、两道内弧和底弧；只改 SQLite，其他驱动 16 个自有绘制状态字节不变。

固定 `d0a49f238d8a0b2f711e33af578d7b5e49fd21eb` 的首次新整包构建成功，但启动
smoke 真实失败：SQL Server 自定义构造漏掉新增公共取消锁。其完整默认集合为
3762 项、3099 通过、1 失败、662 个条件跳过，唯一失败同为 SSH supplied-socket 的
SQL Server close。四个自定义适配器改用共同初始化，保留原驱动验证、namespace 和
专属状态；close/cancel 方法未改。同最终新增测试旧源 12 失败/9 通过，新源 21 通过。

同一 d0a 的实际 Cocoa 25 模块为 748 项、744 通过、1 失败、3 条件跳过。失败是
General 浏览按钮在 36px 字体下被父表单挤短，内容区 42px 小于字形 43px。
QMacStyle 的 layout-item 外边距与未扩大的父窗口共同导致该问题，单改按钮属性的
失败候选也保留。修复为按钮保留实际 widget rect，以及可见 SQLite 表单在字体变化后
通过自己的 GUI 定时器只扩大到自然最小尺寸；不缩回用户已放大的窗口。默认 20×23
保留，原测试同字节红绿，另有保留用户窗口尺寸回归。该修复叶 Cocoa/offscreen 各
133 通过；它们不与全源结果相加。

## 固定源码本机资格

`10ca18a8bc2cb804b3492c4f3f661131745133ad`，Python 3.13.11、Qt 6.12.0、
macOS ARM64，269 个源/测试/构建输入，83 个自有 Python 模块。

| 范围 | 实际结果 |
| --- | --- |
| 完整默认 pytest | 3784 项：3122 通过、662 个外库/平台等既有条件跳过、0 failure/error；完整收集与执行序列及 JUnit 多集完全对应；实际 offscreen |
| 实际 Cocoa 26 模块 | 749 项：746 通过、3 个既有外库条件跳过、0 failure/error；收集/执行/JUnit 对应，实际 QPA=cocoa |
| 原生整包 | 当前源码重新构建并签署本机测试 bundle；实际 Cocoa 启动、异步 6×4 样例查询、17 项本地驱动/运行探针通过；不是实际 Oracle/SQL Server 数据库连接 |
| 整包静态审查 | 30 项通过：83 个自有模块及入口匹配冻结源码、Oracle 32 模块/55 wheel payload、61 个 ARM64 Mach-O、95 个 symlink 和真实处理后的 JS payload |

各自源码前后/Git、原生 helper、私有工具和隔离清理检查通过。原生 app 整体文件/链接
清单在 smoke 前后相同，ROOT 已查看全尺寸真实查询截图并独立确认构建和应用进程
不存在；临时目录移除由 producer 核实，未额外保留其路径作独立文件系统复查。
raw JS helper 在本工作树新构建，builder/四个 native 输入与最终冻结源码一致；未借用
0.13.2 helper 资格，最终处理/sign/staging 和 payload manifest 在本次 bundle 中重做。

六目标 desktop CI 已接入 `scripts/test-sqlite-window.py`，对 UI、父字体、实际内容区、
浏览/绘制与用户窗口尺寸五个模块做独立收集/执行/JUnit 零跳过资格，并记录逐例实际
windows/cocoa/xcb、源字节、隔离 Store、keyring 禁止和进程清理。case 数从实际收集读取。
macOS/Linux 使用原有受控进程组；Windows 的文件日志 owner 只声明本 leader 的等待与
清理，Python audit 拒绝该范围内 Python 创建子进程，不冒称 native 全树清理。
`.gitattributes` 固定源文件 LF，避免 Windows checkout 换行使 raw Git 字节门禁失真；
GIF/PNG/ICO 保持 binary。源码、CI、安装器与人工参考验收仍是独立门禁。

## 新演示与待交付门禁

冻结 10ca 的新实际 Cocoa 演示完成 38 阶段、522 帧、87 秒、2,015,909 字节，SHA256
`0985e27ee91dc01b1185202a9320f50d17588972c0a82e69c6e07ab93b419711`。
新增真实空数据库页、实际加号打开空 sheet 并取消；没有添加草稿行、秘密或保存 profile，
同一生成文件仍是两行且摘要未变。Test 另用真实后台会话，版本显示实际 SQLite 3.50.4，
不伪装成参考的 3.45.0。Oracle 表单为空且不连接；MongoDB 仍为独占生成服务。
文本隐私保护在每帧保存前执行，GIF 已独立解码全部 522 帧；逐阶段人工和颜色审查另记。

38 张全尺寸阶段代表帧已由专门审查成员逐张查看，ROOT 另看全部 38 张首/中/尾联系表，
以及三个 SQLite 阶段的全尺寸图；未发现真实连接、个人绝对路径、凭据、业务数据或参考
图片混入。不是逐帧人工目视或 OCR 声明。MongoDB messages/output 为参考支持的
text-only 命令、OK 和耗时，不把该画面当可读事件行的证据。
原 GIF 与公开收据保持原字节后替换 README 的唯一一段演示。

独立 QImageReader 解码与 ffmpeg 的 522 帧 RGBA 完全对应；34 个实际成功圆点 ROI 和
198 个驱动/别名卡片 ROI 的主填色均为源码目标色。次要背景、字形和细线 ROI 相对源码
目标分别仍有最大 1/9/20 RGB 标量差，缺少原始录制 PNG，不能归因于编码或声称所有
状态颜色完全相等。Docker 有界历史事件唯一恢复本轮容器及两匿名卷身份，随后按各精确
ID 独立 inspect 证明不存在，daemon 前后健康；未读取环境变量、日志或清理别的资源。
临时工作区和原始帧的移除由 producer 及源码绑定证明，未猜测其已删除的路径或重开文件。

人工审图仍看到 inspector 长标题、部分窄 combo 值和 pipeline 长节点标签截短；选项页
底部为实际可滚动 viewport。默认/焦点/非活动高亮、完整控件比例与长文本呈现继续归 U15
和 U08 的开放项，不能以本次演示关闭全部视觉门禁。

随后六个目标仍须各自在其 OS/架构构建、安装、smoke、卸载；全部安装器和校验和核实
后才可发布。当前没有新六目标或公开资产通过声明。

## 首次主线 CI 与验证脚本修正

主线 `d15c87ce01ce031236457f4dc79e1175b4dac06c` 的
[run43582451879](https://github.com/shubolab/opennavicat/actions/runs/43582451879)
完成为失败：9 个必需任务中 7 成功、2 个 Windows 失败，publisher 跳过；保留 13 个产物
及原始失败回执，不据四份非 Windows 安装器发布不完整 Release。
四个非 Windows 目标的原生 SQLite 58 项均严格零跳过通过，实际 QPA 为 xcb/cocoa；
Oracle 两架构各 backend14/window3 和 ready/cleanup 回执分别通过。只审查了验证 ZIP
及安装 smoke 收据，没有把未下载的大安装器当成字节审查通过。

Windows amd64 的 36 个 setup error 来自 fixture 使用 `aux.sqlite`，触及
[Windows 保留文件名](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file#naming-conventions)；
三处 failure 是测试错误要求字体增大必然增宽。ARM 还观察到默认按钮 81px 而非硬编码
80px，以及三项 async Test 的 Qt teardown 出现 cryptography/OpenSSL provider warning。
新 runner 清环境时误删了 source-build 的 `OPENNAVICAT_LIBPQ_DIR`，又在应用 bootstrap
之前导入 hashlib/pytest；已有 source SDK 初始化因此未生效。

修正把三个真实 owned-file fixture 改为 `generated-` 前缀，SQL 别名 aux 保留；字体合同
继续严格检查实际 glyph、SE_*Contents、右沿、顺序和取消，并保留已观察 Cocoa 几何。
其他 QPA 使用实际 native baseline，另有真实长 caption 必须增宽并恢复原尺寸的回归，
不强迫无必要增宽。runner 只把该明确 SDK 变量保留至现有初始化，早于 hashlib/pytest，
随后消费移除；保留零 DB opt-in、Werror、keyring 和进程清理门禁，没有吞 provider warning。

冻结修正源码 `272f1188b3076ce4590fca45233192413e27c6a7`：270 输入，完整默认
3791 项=3129 通过/662 条件跳过、0 failure/error，完整 collect/run/JUnit 对应，源/Git、
helper、工具和隔离清理通过；实际 Cocoa 原生 runner 的 5 模块 59 项零跳过通过。
三个真实文件名回归在旧 d15 全红、新源全绿；三个 fresh-child bootstrap 顺序回归亦
同最终测试旧红新绿。其 DLL loader 是受控标记，不能代替真实 Windows ARM DLL 运行。

本次只改测试及资格 runner，99 个应用/native/packaging/依赖声明输入与原生整包、
38 阶段 GIF 的冻结 10ca 完全相同，83 个应用模块未变；没有重建或重录并冒充新结果。
新的完整 Windows/Linux/macOS CI 仍须实际执行，通过全部六安装器及正式标签门禁后才可发布。

真实加密、Cipher Name、密码保存策略、进程重启、多附件及无效配置规则、HTTP/备注
完整行为、全量字体/主题/状态和各平台参考保持开放。其余数据库工具门禁继续见
[完整矩阵](parity-matrix.md)，不因本片段变窄。

并行开发分支保持本地；当前 origin 仅 main 与保留 dirty 工作的有限 stream 分支。
验证后的进展与 snapshot 直接进入 main，不再为阶段性记录新建远端分支。

随后 main `9f2104fd1a920adb52db0c83196b657781add381` 的 run [`43585915945`](https://github.com/shubolab/opennavicat/actions/runs/43585915945) 虽通过六目标原生 SQLite UI 门禁，仍在两项 Windows 完整测试中被相同的 `width() < 80` 断言拦下。Windows 36px 字体的实际内容需要 116px；字形内容矩形、右边界及布局约束均通过，因此纠正的是测试的固定宽度预期。当前修订只将“小于 80px”限定于默认主题的 12px 紧凑控件，并在实测字形需求加边距超过 80px 时检查自然扩宽。macOS offscreen 两个相关模块 7 项通过，`git diff --check` 通过；这不是 Windows/ARM 复验。新主线 CI 仍待推送触发并观察，v0.13.3 未打 tag、未发布，公开版本仍是 v0.13.2。

## 10 小时恢复后的新验证（仍未发布）

用户明确恢复并设定北京时间 2026-11-15 08:43:06 的收尾截止，见
[本轮快照](snapshot/2026-11-15-ten-hour-resume.md)。此节记录新观察，前面的失败及旧 GIF
测量保留原来源，不回填成新版本结果。

`b0e407af5f8f8ad4e9fd269270c39e6808d00265` 本机完整默认测试为 3,825 项：
3,152 通过、673 条件跳过、零失败/error。新 runner 分别收集完整默认集合及执行进程
集合，与 JUnit 精确对应；272 份源码输入和七份现有 helper 输入前后相同。真实条件跳过
原因逐项保留，不把它们当功能通过。完整清单与 source/运行回执将在六目标 CI 上传。

首次新 runner 的 3,802 项中三个失败来自它禁用插件自动加载后，既有子测试未继承
命令行 `-p`，缺失 `qtbot`；已通过显式继承插件修正，旧日志保留。Windows 新 owner
使用创建时绑定的 Job 管理普通后代；初次故障注入发现异步 job 终止后的重复终止竞态，
已修正为等待。随后 main
[`run43609330345`](https://github.com/shubolab/opennavicat/actions/runs/43609330345)
六个桌面任务及其原生 67 项 UI 范围均成功，两个 Windows 完整默认 suite 和安装器任务
也成功；Oracle ARM 的 SYSTEM 密码重设仍失败，整条流水线为失败、publisher 跳过。
未以这些成功片段发布不完整 Release。

六模块 67 项真实 Cocoa 零跳过及来源/隔离清理合同见
[原生 UI 门禁记录](native-sqlite-ui-qualification.md)。长信息标题和 pipeline 卡片的
修正、旧红新绿及状态范围见[长名称专项](validation-bounded-names-0.13.3.md)；
[本轮实际参考](sqlite-connection-runtime-2026-11-14.md)另观察了普通多附件、进程重启、
无效草稿及十五个 cipher 候选，不等于应用已接入全部加密功能。

Oracle 初始化顺序修正后的冻结源 `1b4f87173aad1812e41f5a39051b56d3a316c1dc`：
本机原生 Linux ARM 服务的 backend **14** 项及独立新服务的 window **3** 项均零跳过，
完整 source 前后对应、精确容器/临时密码/会话清理通过。客户端为 macOS arm64，window
QPA 为 offscreen，不当作 Linux 客户端或 Cocoa 参考验收。两个回执 SHA256 分别为
`9dc446bdccee9b9cd752fff36c2e74021cb0ac7243ea9721eeb2b7f787ed9aef`、
`2b70fba1a13e514a334fd660bb6552c284f0121971003c10f89277b88fcbb1b1`。
原 360 秒 readiness 预算保留，不宣称能约束所有进行中认证的完整 wall time；
真实两架构 CI 仍需再运行。

### README 实录刷新

唯一 GIF 已替换为源码 `487584d5ea1ee6357c49c46566cf2d4e91b81345` 的真实 Cocoa
38 阶段、522 帧、87 秒实录，1,998,831 字节，SHA256
`030070a8a0866001a4983b89f559737ef1da4444153a51dddfa2f229c1ae61d9`；公开 JSON 收据原样
复制。录制前后源、helper 和私有工具稳定。相关录制测试 159 通过、3 条件跳过。

更新前的审查成员已逐张看过 38 张全尺寸阶段代表帧；应用更新恢复后另看七张全图，ROOT
另看 objects、设计器 SQL preview 和 pipeline 三张全图，未发现真实连接、个人路径、
凭据、业务数据或参考图。不是逐 522 帧人工目视声明。所有帧经录制文本保护，新的 Qt
与 ffmpeg 全部 522 帧 RGBA 一致；独立 bounded Docker 事件确定唯一录制容器及两匿名卷，
exact inspect 证明均不存在。审查 manifest SHA256 为
`9f04e44e64fa552ffdad784f9c354708e2fd912db2ed7c93023f2ad2f15a8b6f`。

本次成功圆点前景 34/34 与源色精确；驱动卡片主色众数 162/198 精确，MongoDB/Atlas
各十八处众数与源色相差 1 个 RGB 标量级，源色在相同区域仍大量存在。因此
all-primary-fill-exact 门禁保持 false。次要背景/文字/线条区域众数距离 1/56/23；混合
抗锯齿文字的最近样本距离为 3。这些众数距离不是最大逐像素编码误差，也不是原始前景色。
原始捕获 PNG 已删除，不能归因于编码。没有改应用、GIF 或筛选区域消掉真实驱动差异。

窄 combo、预览表头与 AND combo、密集页签仍有限制，选项底部为可滚动 viewport；
stream 两页仍是生成命令、OK 和耗时的 text-only 结果。U15/U08、全部功能及三平台参考
继续开放。当前公开安装器仍为 v0.13.2。


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
