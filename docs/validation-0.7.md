# 0.7.0：当前文档树、导航与 Windows 连接候选发现

记录日期：2026-11-11，Asia/Shanghai。此记录在 0.7.0 源码集成阶段建立；正式六目标 CI、安装包和公开下载结果另行补充。当前公开可用版本仍为 [v0.6.1](https://github.com/shubolab/opennavicat/releases/tag/v0.6.1)。源码、参考运行、原生 API 测试、最终安装包和用户现场 GUI 的证据各自有明确范围。

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

唯一公开 GIF 由当前 **0.7.0** 源码实际录制：**1440×900、127 帧、21.17 秒、556,561 字节**，SHA256 为 `3bfcf6408145305433c7181426b00a7b6497f65ef6df625d6caf43a3030268a2`。九段连续 Qt 帧覆盖 SQLite 对象/数据/查询/设计器及 MongoDB 网格/当前 BSON 树/GridFS 下方预览。树里的 Int64 完整显示；GridFS 帧已目视核验预览在下方。来源和数据均为程序生成，不访问用户 workspace、Navicat 来源或钥匙串；独有 loopback MongoDB 8.0.34 服务、临时工作区和帧文件清理均已核验。公开回执为 [opennavicat-demo.json](assets/opennavicat-demo.json)。这是 offscreen Qt 录制，不代表三平台原生 GUI 已完成对照。

## 平台和完整目标边界

用户已明确要求保留 Dell Windows 现场，Windows 后续再验收。本轮不继续操作远程应用、UU 防护或自有服务，也不清理该现场。此前 0.6.1 已观察的 SQLite、MongoDB 连接/浏览/类型展示仍是原版本的有界证据；MongoDB 编辑/原生命令/GridFS 补充 GUI 和卸载未完成。WSL 没有桌面，其 GUI 验收也仍延期。托管原生 CI 与用户现场验收分别记录。

六目标安装包、原生依赖架构、已安装应用启动、卸载及实际公开下载尚待本次正式标签运行；未完成前不提供虚构的 0.7 下载链接。产品账号体系持续排除，全部数据库工具和三平台 1:1 UI 的[完整门禁](parity-matrix.md)继续开放。
