# 0.13.1 图标与 Oracle 里程碑验证

2026-11-14，Asia/Shanghai。完整 goal 保持 active。该里程碑继续实现 Navicat 18 的
完整工具与 1:1 保留界面，不能用本页的有限检查关闭 [parity matrix](parity-matrix.md)。

## 源码与当前交付状态

应用及测试源码冻结为 `bb4d46964d3568d1dc2f6743f92050e7a43519d9`；正式 `v0.13.1` tag
为 `b9261016de530dda0311683223a1862efdb05d26`，增加已审文档及唯一演示资产，243 份运行、
测试、构建、打包、workflow 和许可证输入与 bb4 相同。版本字段、项目和 lock 均为 `0.13.1`；
README、文档与演示资产不在此源码输入哈希范围内。

当前最新公开安装器为 [v0.13.1 预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.13.1)，
发布于 **2026-11-14 00:18:33 UTC**。[正式 run43531823280](https://github.com/shubolab/opennavicat/actions/runs/43531823280)
在 exact b926 上 10/10 job 成功，15 artifact、六份原生安装器及运行回执独立审查通过；
随后八个公开资产全部匿名下载，并与 GitHub 大小/digest、SHA256SUMS、合并 manifest 和该正式 CI 字节逐项匹配。
公开 ARM64 DMG 另在本机完成隔离复制、真实异步 SQLite 6×4 查询、本地运行时探针及精确卸载清理，
截图经全尺寸目视复核。Cocoa 显式请求，包内 smoke 未自报实际 QPA；不与 bb4 本机应用或 CI 回执混记。

main [run43524907305](https://github.com/shubolab/opennavicat/actions/runs/43524907305) 的 exact bb4
九个必需 job 成功、publisher skipped 与六安装器字节审计继续记为预检，摘要单列如下；
正式 tag 和公开下载未复用这些 main 安装器。

旧 `v0.13.0` tag 保持在 `81523aa4ff5b8734776f5bf0b6e7217ef0e192f7`；
[run43487011620](https://github.com/shubolab/opennavicat/actions/runs/43487011620) 在 macOS Intel
fixture teardown 失败，publisher skipped，未发布安装器。早期 Git211 main 预检成功不能
抵消该失败，也不能代替本版资格。失败记录和原 SQLite allocation 原因未知的限制均保留。

## 本轮实现

- 独立几何绘制主栏、对象、九驱动、供应商与常用小动作；主栏 28、树/页签 16、查询
  选择器 14、连接网格 50/列表与筛选 16 逻辑像素分别接入。连接、开关、禁用分别处理，
  高 DPI 按目标尺寸绘制，保留未实现功能的禁用状态。
- 原生页签关闭按钮改为灰叉，保留平台侧别和执行/保存保护，并销毁被替换的旧按钮。
- 修正主栏每次刷新都移动“其它/用户”而重建 QToolButton 的空白：只在顺序不正确时插入。
  纯自有 fake fixture 旧代码8/8立即隐藏、事件后显示；最终同字节测试旧产品16失败、新代码
  相关39通过，保留各驱动的顺序、可见/禁用规则。新真实Oracle截图中缺块已消失。
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

## Git bb4 本机资格

| 范围 | 实际结果与限制 |
| --- | --- |
| 默认全源 | 2725 通过、662 条件跳过、零失败/错误，3387 个 collect/run/JUnit 用例精确一致，107.837 秒。`PYTHONWARNINGS=error`；243 声明源及原生 helper 前后相同并匹配 Git。所有外部 DB opt-in 清空，跳过不算真库执行。 |
| 合流 Cocoa 窗口回归 | 十一模块 334 通过、3 条既有真库条件跳过、零失败/错误；337 用例 Counter 精确，243 源前后/Git 相同，实际父进程 Qt 平台 Cocoa。生命周期回归另开 offscreen 子 pytest，未混称 Cocoa；StyleChange 子进程请求同父平台，完整保留 factory 检查且不改变父 Qt 状态。 |
| 本机应用 | Darwin ARM64 bundle 构建与隔离 smoke 退出 0，样例 SQL 6×4，Oracle26.1 四项与其它13项探针为 true。传入 Cocoa 环境；smoke 报告本身不记录实际 QApplication 平台，不能借此代替实际窗口证据。 |
| 包内静态审查 | 24 项通过；完整 79 应用模块及入口等同当前/Git 源码，61 Mach-O 均支持 ARM64，4 个 Oracle 扩展机器码匹配官方 wheel ARM64 slice；32 driver Python、55 wheel payload、许可证、95 个包内链接分别核对。不是安装器或六目标验收。 |
| 实际自有界面 | 当前bb4的35阶段实际Cocoa GIF和下列Oracle窗口均已目视；先前ca4的20张亮暗/通用profile图片保留为历史，不改名成bb4全源资格。不是原版或三平台验收。 |
| 实际 Oracle 窗口 | fresh owned Oracle 的实际 Cocoa 三用例通过、零跳过；真实 Test、保存不自动连接、重新打开、临时密码、schema/表/视图/Decimal，以及计算视图 Stop、同会话复用、Disconnect/Shutdown。独立106项结构/资源检查通过，24声明输入前后/current/Git相同；28阶段图/15种唯一画面的目视审查单独保存。 |

私有本机证据位于 `artifacts/release-0131/` 的 `default-bb4/`、`cocoa-targeted-bb4/`、
`native-bb4/`、`native-bb4-static/`、`oracle-cocoa-bb4/` 和 `oracle-cocoa-bb4-independent/`。
`combined-bb4-source-verification.json` 另外核对默认、Cocoa、本机包和实际录制各自的243源
前后/current/Git全部相同，并绑定Oracle runner的24份输入；不把24称作runner测过全部243。
源码运行、条件数据库测试、打包和人工对照是不同门禁，不相加成更大的覆盖范围。

原 CI teardown 的窄修正把原 cleanup 放到 pytest-qt 的 `before_close_func`，在框架合法
close/deleteLater 之前关闭会话；真实框架送达 DeferredDelete 的子进程回归保持原版本红、
修正后绿。未修改产品生命周期、增加 invalid guard 或放宽预算，也未屏蔽 SQLite warning。

## main 原生预检（不是公开安装器摘要）

run43524907305 的六目标源码测试、103项原生JS VM和4项Qt组件分别核对其动态清单；
Linux默认3369项（2704通过/665条件跳过）、macOS3387项（2722/665）、Windows3405项
（2609/796），均零失败/错误。QStyleFactory按平台展开，不能把本机3387写死给全部OS。
两native Linux架构各自Oracle backend14与Window3均零跳过、source/fixture/精确资源清理通过。
Mongo collections511，有限stream的backend/window/security/cleanup为各自独立scope，未相加。

| main预检目标 | 实际字节 | SHA256 |
| --- | ---: | --- |
| linux-amd64 | 78683284 | `1cc2bac660cdd33f20b5e6e10434dec5a89c82ef6cad717ce13c93b6c8b3a254` |
| linux-arm64 | 77188924 | `4f8c1327255bc6d9348409bbaeba4c9e777b93cb4f162172bd6d8a4631ac164c` |
| macos-amd64 | 53700687 | `dd0919b61f65ecc9c3c6c5a3b6cfa700c56d45fa36f5057c8ee3eed316c35cbd` |
| macos-arm64 | 52771290 | `d801e9585e53f001e90a8bd2fbae6f66ee641c25d5af9f6ace2bfd793f89f59b` |
| windows-amd64 | 45431276 | `bef067845f78f207bde934265a04b966006d1d60f8bd4b87aa89d9a4df281e27` |
| windows-arm64 | 36080408 | `62f23b91e2fdac42465d3edbda78b98491fd86912abdacfc66a9ba13fa44b0d3` |

这些只绑定bb4 main预检，不是后续tag或公开下载摘要。15项首次批量下载发生600秒transport
超时，保留13目录原文件；同run/ID补齐的两ZIP按API digest/size/CRC核对，原13文件hash未变。
完整成熟审计随后通过，两Mac只读提取挂载已独立确认detach。证据为
`artifacts/release-0131/main-audit-43524907305/{verification,delivery-handoff}.json`。

## 正式 tag 与匿名公开下载

正式 run43531823280 的六个原生 OS/架构 job、TLS/SSH、Oracle native amd64、Oracle native arm64
和 publisher 共 10 项均成功。15 份正式 artifact、六安装器实际字节、sidecar、打包与验证两份
manifest 及安装/启动/卸载回执均绑定 b926；未沿用 main artifact。
Linux 源码 3369 项（2704 通过/665 条件跳过）、macOS 3387 项（2722/665）、Windows 3405 项
（2609/796）均零失败/错误；103 项 native-JS VM 与 4 项 Qt 组件按各自动态清单核对。
两个原生 Linux 架构的 Oracle backend 各 14、window 各 3 项，均零跳过且精确 fixture 清理通过。
Mongo collections 511、stream backend 74/window 25/security 2/cleanup 13 分别记账，未相加冒充完整覆盖。

| 正式及公开目标 | 实际字节 | SHA256 |
| --- | ---: | --- |
| linux-amd64 | 78680894 | `cb270629965402fc4654de7d154ba947b7caf5bb85405a139e9d2d44516d651a` |
| linux-arm64 | 77189062 | `59c8939d7669608d25e71616ab5e4e1c299ba378423f30d4d9f116f48d4e9df8` |
| macos-amd64 | 53699609 | `f983fc48551d36e6e4f1ef09002a7f39ad05b94742dfe6ef6d44c6f04bb0bfaf` |
| macos-arm64 | 52689861 | `8aeb2fd1d3f6b506d82a55f8d232be045a974e7bac6ccfa6842e5be904ee9739` |
| windows-amd64 | 45428983 | `8a34abfee619ad7c6914ad365f251e41db9cf478a1e0ee1f246a6f0bf1d9f6ca` |
| windows-arm64 | 36070717 | `c2fa23f1bf11b73d023bed0f879d302a9d1f70562078966101d82a633d687d11` |
| SHA256SUMS | 622 | `f7ae0ebe88ab15bdb397111375aa41ce14c45905f276913f4a6e4083c3c060c5` |
| build-manifests.json | 211923 | `5ce67df23286d01c52fa93a13f6d4039c556c3b0c10e36aa66e7cd35ba71c3c8` |

公开 Release ID 为 `444872196`，非 draft、prerelease=true；八个规范资产均返回匿名 HTTP 200，
关闭 curl 配置、未使用 Authorization 或认证回退。首次匿名 API 调用因 core quota 耗尽返回
403，尚未请求资产；原失败记录保留，配额重置后的 r2 八资产审计通过。
私有正式证据为 `artifacts/release-0131/tag-audit-43531823280/{verification,delivery-handoff}.json`，
其中 verification SHA256 为 `fb58682e4b5d47f42f43691250140a66a3e9e475ebda6a05d52e63a64fbc6a72`。
公开证据为 `artifacts/release-0131/public-43531823280-r2/{verification,delivery-handoff}.json`，
verification SHA256 为 `258e9a29a485e393e5797fa675a4331fbe8e2e553e31662da92fac17647f884b`。
公开字节一致及 CI 原生安装通过不等于三平台人工验收；下列公开 ARM64 DMG 本机结果单独记账。

## 公开 ARM64 DMG 本机隔离验收

使用上述唯一公开 DMG（52689861 字节，SHA256
`8aeb2fd1d3f6b506d82a55f8d232be045a974e7bac6ccfa6842e5be904ee9739`），只读挂载后用 ditto
复制到自有临时安装位置，不改动 `/Applications` 或既有运行程序。404 个文件、103 个 symlink
的内容/模式/目标与挂载包一致；原生 ARM64 bundle 版本为 0.13.1，主程序字节匹配正式 manifest。
完整 79 个应用模块及 desktop_entry 与 exact b926 Git 源码编译结果相同，仅归一化 co_filename。

隔离 Store/config/cache/TNS/TMP 后，实际应用执行 `--smoke-test --smoke-report --screenshot`，
真实后台 SQLite 查询返回六行、四列（customer/city/orders/revenue）。Oracle 26.1.0 四个
canonical flag 及其它十三项驱动/加密/native-JS 本地探针均为 true；Oracle 探针未连接数据库。
应用 PID/PGID 3823 正常退出 0、未超时或要求强制结束，所有 owned 进程均 reaped、组不存在且
reader 已关闭。精确 `/dev/disk4` detach 后挂载与 image 不存在，安装副本及自有临时根目录删除；
root 另行核对全部记录 PID/PGID、exact public image 和临时根目录均不存在。

Cocoa 只是明确传入的请求值；包内报告未自报实际 Qt 平台，保留 actual_qpa_reported=null。
3024×1840 PNG 已检查编码完整性，root 实际全尺寸目视看到生成示例 SQL、六行四列及图标，
未见缺字或隐私阻断；禁用动作低对比、连接名与 SQL 状态省略、深色空白页签条仍为可见限制。
截图 SHA256 为 `6ab51182319c587e9c660d2f5aca061e91669f2f73b1dae0de6d038b87bf380a`。
该结果是有界包内运行与画面检查，不是三平台人工验收、完整参考 UI 或数据库工具验收。

私有结果为 `artifacts/release-0131/public-arm-cocoa-43531823280-r2/verification.json`，SHA256
`b61d62ab848b78ea2331f1c163a4627539e6c8653e5b4a7b7c61a56414cb0fed`；原报告保持不变，
人工目视及独立清理补证另存 `root-visual-cleanup-review.json`。
首轮 `public-arm-cocoa-43531823280/` 在 ditto/启动应用之前，因 hdiutil 返回完整 XML 后附加
弃用警告导致解析失败；当轮精确设备卸载、临时根删除及 PID/PGID 清理均通过，失败记录保留。
私有 parser 仅允许完整闭合 XML 后这一条精确警告，记录原输出、XML/尾部及排除行摘要；
未知前后缀、截断、重复 XML/警告仍拒绝。实际捕获输出的红绿回归与原计划/清理测试共 42 项
纯测试通过、Counter 一致后，另用 fresh-r2 实际执行取得上述通过结果。

## 演示与尚未完成门禁

bb4 新实际 Cocoa GIF 已审查通过并精确替换 README 中同一资产：35阶段、471帧、78.5秒、
1833002字节，摘要 `8954f63340dba160cebe81f175dd575a2306c619a41389cc9000a7df27204587`。
全471帧文本保护、独立解码通过，35个全尺寸中帧及35首/中/尾联系表实际看完；未宣称逐帧
目视或全帧OCR。11张卡片首中尾共33个实际区域的主色与本应用源码RGB误差均为零。
完整录制源码243份前后/Git相同；公开GIF和回执逐字节等同已审产物。

新编码器使用完整帧统计的固定调色板，并保留本应用九个驱动主色、透明槽和黑白；只调整
调色板，不往画面添加元素。真实图标/文字/帧序对照中，原diff/full策略有受控红证据，
保色方案通过。此前ca4实际35阶段GIF出现Oracle偏棕，仍保留为失败证据；其精确发生点
没有原始中间帧，不能反推已完全证明。本次新实际像素及目视通过单独记录。

演示仅含生成SQLite/MongoDB、空Oracle表单和空选网格，Oracle表单不是数据库执行证明。
精确owned容器生命周期/destroy/不存在已独立核对；原卷ID和temp路径未保留，相关清理仍只
记producer断言。私有证据在 `demo-bb4/` 和 `demo-bb4-review/`。私有wrapper的原limits字符串
遗留“242”，已保留原回执并另作annotation；实际计数、完整清单/控制循环与源码包围为243。

先前ca4 main43511903905的六项失败全部保留：三平台旧96行高断言、Windows逐子控件
setStyle、MacIntel子进程OpenSSL导入，以及OracleARM服务准备失败。现改为实际文字适配、
独立进程中Qt公共样式传播及原hook测试环境恢复。新run上述jobs成功不能代替artifact审计，
也不能据此回填原Oracle失败原因；新fixture增加有限启动阶段/reason/driver-code/owned
State诊断，原360秒预算不变，原超时仍仅是推断。详细历史见[当前snapshot](snapshot/2026-11-14-goal-resume.md)。

六原生 CI 目标、正式 tag、匿名八资产及公开 ARM64 DMG 的本机隔离 smoke 已按上述边界分别通过。
Windows/Linux 实际参考界面、完整主题/悬浮/焦点/失活状态、全部菜单/向导的小元素及
Oracle copy/关闭/数据库列表等尚存 Unicode 控件继续开放。

Oracle Query、事务、写入、设计器、Data Pump、Other、其余认证/安全/高级设置、完整类型
及 server-side SLEEP 的及时取消限制继续由 [Oracle 记录](validation-oracle-0.13-candidate.md)
和完整矩阵追踪。本轮端口与图标改进不缩小这些目标。
