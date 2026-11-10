# 0.7：当前文档树、导航与 Windows 连接候选发现

记录日期：2026-11-11，Asia/Shanghai。此记录覆盖 0.7.0 初始集成及失败门禁、0.7.1 修补与后续验证；正式六目标 CI、安装包和公开下载结果另行补充。当前公开可用版本仍为 [v0.6.1](https://github.com/shubolab/opennavicat/releases/tag/v0.6.1)。源码、参考运行、原生 API 测试、最终安装包和用户现场 GUI 的证据各自有明确范围。

## 行为与实际参考

参考仍为本机 Navicat Premium **18.0.5 build 321**。[MongoDB 运行观察 M18-09](ui-reference-18-mongodb.md#m18-09冻结前的网格单文档树与-gridfs-布局复核)记录实际单当前文档树、字段/值/类型、记录导航和 GridFS 下方预览/进度布局。只使用本任务生成的样例库；原版菜单入口、静态声明与已执行行为分别记录。

本版树视图以一份当前文档为根，切换网格、树和 Canonical Extended JSON 保留同一 BSON 身份。`bool` 和整数不使用 Python 的宽松相等来混淆文档身份。树字段双击仍通过独立 Extended JSON 编辑器写入，**原版的行内编辑及类型 popup 尚未实现**，不将本版称为完整树编辑还原。

记录导航使用实际读取成功的偏移历史，避免字节预算产生短页后仍按 `page_size` 前进而跳过记录。过滤、排序、projection、limit、数据库、集合或会话变化时重置对应状态；读取失败不污染历史。已知总数的最后记录按真实总数定位，未知总数时禁用最后记录，不猜最后整页。未保存编辑提供保存/放弃/取消，异步保存期间保持忙状态，取消不跳转。

独立审查用真实 QTest 鼠标事件另发现一项回归：取消离开脏稿后，当前文档已恢复，但 Qt 后续选择事务可把蓝色高亮留在目标行，导致工具条编辑与高亮不一致。现通过同步 `selectionChanged` 收尾校正及显式整行选择修复，没有增加 Timer 或延迟。回归覆盖鼠标及 Shift/Ctrl、方向键变体、工具条编辑/删除、保存进行中和双击；当前索引、高亮、文档与编辑目标保持一致。分支受控 browser/GridFS 检查为 62 passed、8 项 live deselected，与全源及真实服务测试分开记录。

GridFS 预览和进度移至列表及分页下方，可分别开关。全局右侧信息面板与预览独立，底层有界预览、取消、内容检查和原子下载提交沿用既有实现。外部应用打开仍未完成。

## 历史连接与配置保护

Windows 实现和静态/官方证据详见 [Windows 元数据发现](windows-navicat-discovery.md)。普通启动只读当前用户的十个已声明注册表族及 32/64 位视图。`RegEnumValueW` 不提供值数据缓冲区，只有明确白名单元数据才通过有界 `RegQueryValueExW` 查询。密码、密文、私钥和 opaque 配置不进入值读取；不读取凭证库，不自动连接数据库。每条候选都带复核问题并保持 blocked，用户确认实际认证和安全设置后才能连接。

只有当前原生物理键名 identity 及快照一致，才可调和两个视图的来源别名。逻辑来源 ID 不依赖可选 API 是否可用；已有本地 ID、收藏、确认配置与秘密不因重载而被删除或合并。不完整配置读取不能清空已有 host/port/user/database；不完整分组读取保留 last-known-good，首次部分成功的分组仍保留明确有效成员，完整有效空分组则可明确清空。

macOS 既有实际来源支持继续保留。Linux 的现有识别格式与 Linux 18 官方原生文件兼容性仍分开跟踪，不能套用 macOS SSH/TLS 字段来宣称 Linux 迁移验收。

原生 Windows AMD64 的自有 HKCU 机制测试此前实际完成 **2 passed、0 skipped**，范围是合成来源及真实 API，并非 Windows Navicat 18 原始保存格式。Windows ARM64 集成和冻结包探针等待正式原生 CI。本版新增的冻结 smoke 只绑定 `RegEnumValueW` / `RegQueryValueExW`，不打开任何注册表键；两个 Windows 安装目标及发布门禁都要求探针为严格布尔 `true`。旧 0.6.1 校验仍使用该版本原有合同。

## 源码验证与演示

集成前各分支的测试结果保留在相应记录。本轮首次全量集成发现端口布局测试受全局 12px Qt stylesheet 污染，导致其 24/36pt 字体场景失效；夹具现已隔离并恢复原 stylesheet。该次失败记录仍保留，不将其改写为通过。

修补后首次全源集成为 **1003 passed、234 skipped、0 failed，71.17 秒，`-Werror`**。新增 Windows 冻结探针、0.7.0 版本与严格发布合同后，安装/发布/端口布局专项为 **126 passed**；最终全源为 **1012 passed、234 skipped、0 failed/error，73.16 秒，`-Werror`**，启用各测试自有的真实 MongoDB 服务。XML 为 ignored 的 `artifacts/integration-v0.7/source-0.7.0-final.xml`。外部 TLS/SSH、SQL Server/Redis 和手工参考端点未启用的用例按条件跳过，不能算作已执行验收；正式安全 CI 另执行真实服务。

纳入高亮修补及其交互回归后，全源再次执行为 **1023 passed、234 skipped、0 failed/error，73.62 秒，`-Werror`**，三份独有真实 MongoDB fixture 的就绪、归属与最终清理均已验证。XML 为 `artifacts/integration-v0.7/source-0.7.0-post-review.xml`。随后只追加一项双击测试，无产品代码变化，独立执行 **1 passed、43 deselected**；没有将它虚加成另一轮全源总数。最终冻结版本的全部测试由正式六目标 CI 再执行。

0.7.0 源码 smoke 在 macOS ARM64 实际返回 SQLite **6 行 × 4 列**，MongoDB 原生 BSON、TLS context、SQL Server/Redis 适配器与加密探针均通过。报告是 `artifacts/integration-v0.7/source-0.7.0-runtime-smoke.json`。本机不支持 Windows API，因此其探针为 `null`，没有伪造为 `true`；Windows 安装门禁须在真实 Windows 主机获得结果。源码 smoke 未连接外部数据库，也不读取历史来源。

唯一公开 GIF 已由 **0.7.1** 源码重新实际录制：**1440×900、126 帧、21 秒、562,801 字节**，SHA256 为 `2cc55728157a879171dbfa72af1e7ef7800d68bed261369dbaa8c10aae9cbdd3`。九段连续 Qt 帧覆盖 SQLite 对象/数据/查询/设计器及 MongoDB 网格/当前 BSON 树/GridFS 下方预览。树里的 Int64 完整显示；GridFS 帧已目视核验预览在下方。来源和数据均为程序生成，不访问用户 workspace、Navicat 来源或钥匙串；独有 loopback MongoDB 8.0.34 服务、临时工作区和帧文件清理均已核验。公开回执为 [opennavicat-demo.json](assets/opennavicat-demo.json)。这是 offscreen Qt 录制，不代表三平台原生 GUI 已完成对照。0.7.0 的早期录制随其原 tag 保留，未用修改版本字段代替重录。

## 平台和完整目标边界

用户已明确要求保留 Dell Windows 现场，Windows 后续再验收。本轮不继续操作远程应用、UU 防护或自有服务，也不清理该现场。此前 0.6.1 已观察的 SQLite、MongoDB 连接/浏览/类型展示仍是原版本的有界证据；MongoDB 编辑/原生命令/GridFS 补充 GUI 和卸载未完成。WSL 没有桌面，其 GUI 验收也仍延期。托管原生 CI 与用户现场验收分别记录。

六目标安装包、原生依赖架构、已安装应用启动、卸载及实际公开下载尚待本次正式标签运行；未完成前不提供虚构的 0.7 下载链接。产品账号体系持续排除，全部数据库工具和三平台 1:1 UI 的[完整门禁](parity-matrix.md)继续开放。

## 首次 0.7.0 正式门禁及后续修补

正式 tag `v0.7.0` 固定在 `d86170759fc11fba9a5a729b8371b22f899e4b17`，运行 [42853745883](https://github.com/shubolab/opennavicat/actions/runs/42853745883)。Linux 两目标和数据库安全 job 已通过；Windows 两目标在端口布局测试失败，未进入打包。AMD64 结果为 22 failed、926 passed、319 skipped，22 项均属于端口布局；两个真实 Windows 注册表 API 用例已实际通过，不能把整体失败描述成所有功能均通过。

这些测试暴露了两类问题：Windows 原生大字体下实际宽度可比 `sizeHint` 大 1–3px，测试不应要求二者逐像素相等；Windows 11 样式的 36px 字体及动态字体场景则未满足实际可用文本区要求，需要产品布局修补。Mac Intel 当时的应用测试耗时较长，最终已完整通过测试、打包、安装和卸载，没有确认新的卡死问题。为避免保留已知不能发布的重复构建，root 在保留日志、JUnit 和 job 状态后取消剩余任务；Mac ARM64 未执行完，发布 job 未成功。原 tag 不移动，不重跑后冒充首次通过，**没有发布 0.7.0 Release**，GitHub Release API 也已确认 404。

后续修补使用新的 **0.7.1** 版本，先在独立分支执行各原生样式/大字体的定向资格，再重新执行全部六目标发布门禁。用户现场 Dell 验收继续延期，修补验证使用托管 CI，不操作该现场。实际结果完成后再补充本记录。

定向资格已按同一产品及测试文件完成四种真实主机：托管 Windows amd64、Windows arm64 各在 offscreen ×1 和实际 Windows ×1.25 下 **124 passed、0 skipped**；托管 macOS Intel 与本机 macOS ARM64 27.2 各在 offscreen ×1 和 Cocoa ×1.25 下 **115 passed、0 skipped**。覆盖所有可用原生样式、12/24/36px、大字体动态变化、内部编辑器独立 42px、深浅主题及实际光标和 stepper 几何；实际平台后端的最小文字净空均为 8px。root 已独立比对三个相关源码/测试文件的字节哈希与资格 head `5e7f0bd` 一致。托管资格运行 [42857556241](https://github.com/shubolab/opennavicat/actions/runs/42857556241) 的 Windows 双目标和 Intel job 成功；其 Mac ARM64 排队状态没有被记成通过，本机 ARM64 仅补充这次定向源码资格，正式六安装包仍须实际完成该目标。早期资格暴露的 Windows 11 内容区与测试等待问题也保留原记录，没有重跑旧记录冒充首次通过。

修补后的 `_PortSpinBox` 按实际子编辑器字体、原生 stepper 的 `SC_SpinBoxEditField`、`SE_LineEditContents`、文本边距及光标留白反算建议尺寸；保留原生步进控件，不使用固定端口像素宽度。字体、样式、屏幕和 DPI 变化会重新参与布局。测试检查实际文本可见、光标/内容区和布局回流，不要求尺寸建议与实际像素逐点相等；原生 Cocoa checkbox 的对齐也改为检查表单项列与行，保留布局语义。

0.7.1 产品修补集成后的全源测试为 **1025 passed、234 skipped、0 failed/error，78.15 秒，`-Werror`**。三份独有真实 MongoDB fixture 的就绪、归属和清理均核验，XML 为 `artifacts/integration-v0.7/source-0.7.1-final.xml`。随后只修正一处字体收缩测试的等待条件，使“文本适合”和“布局已收缩”同时成立，不修改产品；该文件另执行 **29 passed**。0.7.1 源码 smoke 再次返回 **6×4**，已有数据库/BSON/TLS/加密探针通过，Windows API 在本机为 `null`。新版实际 GIF 也完成临时服务及文件清理。正式冻结 tag 的全套测试仍须在六个目标重新运行。
