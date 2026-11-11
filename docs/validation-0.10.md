# 0.10 候选验证记录

记录日期：2026-11-12，Asia/Shanghai。**0.10.0 正在准备，尚未冻结最终源码、启动本版六目标 CI、创建正式 tag 或公开 Release。**
当前公开安装器仍为 [v0.9.0](https://github.com/shubolab/opennavicat/releases/tag/v0.9.0)。
本文件先记录已经取得的证据与待完成项，后续必须用本版精确冻结 SHA、正式运行和实际公开资产补齐。

完整目标仍为 Windows、Linux、macOS 的独立原生 Qt 实现、完整 Navicat 数据库工具和 1:1 保留 UI。
产品账号、产品登录/注册、激活、订阅和云账号/团队协作继续排除；数据库认证、用户/角色/授权、云数据库连接、
独立 AI provider/API 配置及适用工具继续保留。完成范围以 [parity matrix](parity-matrix.md) 为准。

## 候选归属与实现范围

本候选在独立 `release-0-10` 工作树准备，基点为 **`b358c6216177bc896b3c57f1f7412758463a1f9b`**，
随后将包版本调整为 **0.10.0**。最终冻结 SHA 尚待候选文档、唯一 GIF 及必要验证完成后确定；
原 main 上并行开发的验证器 UI 或其它未整合工作不自动属于本候选。

相对公开 0.9.0，候选包含普通集合设计、集合/时序/普通视图及其已实现管理工作流，以及以下进展：

- 按本机 Navicat Premium **18.0.5 build 321** / MongoDB 8 的实际观察，调整普通集合七页顺序为
  索引、验证、存储引擎、索引选项、集合选项、排序规则、脚本预览，默认选择索引页。
  内嵌列表隐藏 `_id_`，服务器 catalog、元数据和独立索引工具仍保留它；新建集合保存后的空列表行为按实际参考保留。
- 两个集合级存储脚本页接入 `storageEngine` 与 `indexOptionDefaults`。目前限定已验证的 WiredTiger
  `configString` 文档形状，使用有界、不求值的 BSON 字面量解析，进入主窗口串行队列的预览、确认、保存和重开流程。
  空白省略选项，显式空配置字符串保持原值；已有集合的存储页只读，验证规则修改不重写存储设置。
- 普通集合的新建/读取、验证规则修改、永久确认删除/清空、重命名、staged 复制、验证/reIndex/compact，
  以及独立时序集合和普通视图的已实现范围，见 [集合设计说明](mongodb-collection-designer-backend.md)。
  存储默认值、索引覆盖及两种复制模式另见 [存储专项](mongodb-collection-storage.md)。
- 修复非法设计草稿从预览切页/计时器 slot 逸出异常，以及校验失败后遗留关闭/断开 continuation；
  修正后普通手动保存不能复活旧的关闭动作。索引后台读取冻结 namespace，并检查结果回调仍属于当前页面/会话。
- 包含 [纯 BSON 验证器草稿基础](mongodb-validator-model.md)：原始字节、类型/顺序和未知子树保留，
  原子补丁与 required 父级关系、依赖不确定时拒绝自动改名/删除，以及有损 BSON 的只读保留。
  **该基础尚未接入完整 Schema/Query 可视编辑器，不是已交付的完整验证器 builder。**
- Linux 依赖准备保持 Ubuntu archive 签名密钥与 TLS 验证。官方 HTTPS 源失败后可使用已探测的 TUNA 传输，
  每次尝试各用临时 sources/indexes 并清理；预装集合覆盖实际 DEB 的每个 Depends 替代组，安装验证继续保留原门禁。

## 本候选已执行与待完成的验证

| 验证阶段 | 当前证据 | 有效边界 |
| --- | --- | --- |
| 0.10 元数据下的本机全源 | macOS ARM64：**1658 passed、545 条件跳过、0 failed/error，84.93 秒**；JUnit 为 `artifacts/release-010/local-source.xml` | 当前候选工作树，最终冻结 SHA 尚未确定；条件跳过不算已执行真实服务，不是六平台资格。 |
| 候选独有 MongoDB 集合专项 | **226 passed、0 skipped/failed/error**；`artifacts/release-010/collections/summary.json` 记录基点 `b358c62`、源码哈希及六份新 fixture | 六个精确 container ID 已另用 `docker inspect` 确认不存在；这次候选工作树专项独立于下述003运行。 |
| 候选 native JS VM | **101 passed、零跳过**；`artifacts/release-010/js-vm/summary.json` | 本机实际 helper/typed IPC 范围，不含真实数据库或六平台运行。 |
| 候选录制脚本回归 | **28 passed、零跳过**；`artifacts/release-010/recorder-owned.xml` | 包含 owned MongoDB 与真实 FFmpeg 回归，仍不替代最终 GIF 的录制及隐私验收。 |
| 本候选 native helper / Cocoa 源运行 | VM **101/0 skip**；原生 Cocoa 设计器及真实存储路径 **51/0 skip**；Cocoa 源 smoke **6 行 × 4 列**通过，七驱动、crypto 与 native JS 探针通过 | 本版打包 smoke 仍待 CI；这些源检查不替代安装器或三平台参考 UI。 |
| README 唯一 GIF | 实际 Cocoa 完整录制 **17阶段、230帧、38.34秒**，逐阶段视觉隐私检查通过，逐帧可见文字 guard 通过，已替换唯一 GIF | 仅生成示例数据；真实存储保存由独立客户端核验；临时服务和工作区均清理。 |
| 0.10 精确源码六原生目标 CI | **尚未启动** | Windows/Linux/macOS × amd64/arm64 的源码、打包、安装、运行、卸载分别验收。 |
| 正式 tag、Release 与公开资产 | **尚未创建或核验** | 仍缺六安装器、校验和、合并 manifest 的正式及匿名下载验收。 |

上述1658项全源执行早于新增的编码回归；该回归及其它录制用例随后在28项专项中全部执行。
本机不同测试范围不得相加为另一轮全源。私有 JUnit、fixture、构建和录制收据保留在 ignored 的 `artifacts/`，
不会随 Git clone 自动恢复。后续冻结源码与上述工作树不同 SHA 时，应保留各自归属，不能以文件相似替换精确资格。
全源545项跳过比前序544项增加1；本机缺少 `redis-server`，相关 Redis GUI/协议用例有明确的前提缺失 skip，
未将这类跳过当作成功或实际 Redis 执行。其它外部服务及平台条件跳过继续按其各自前提解释。
编码失败已由小样例复现为主窗口 RGB24 与 modal RGBA 切换触发 FFmpeg palette filter 错误；
`fit_capture_image` 统一输出 RGB32 后小样例和上述录制回归通过。1658项全源早于这项最终录制修复，
最终冻结和跨平台验证仍须准确记录源码归属；该次全源不覆盖随后新增的编码回归。

## 003c486 历史六目标预检：独立产物审计通过

这部分对应 **`003c4861840aedf0cb989d06d12c5000294ed660`**，包内版本仍为 **0.9.0**，
不是上述 0.10 最终冻结源码。
[运行 43053509506](https://github.com/shubolab/opennavicat/actions/runs/43053509506) 为 main push、attempt 1：
六个原生桌面 job 与数据库安全 job 共 **7/7 成功**，发布 job 按非 tag 规则 **skipped**。

独立下载了该次运行的13份 artifact，检查六个实际安装器的大小、SHA256 sidecar、安装器 artifact 与 validation artifact 中的
两份 manifest，以及各目标安装/启动/卸载和原生 payload 回执；全部通过。各目标源码 JUnit 为：

| 003 目标，每架构各自 | passed | 条件跳过 | failed/error |
| --- | ---: | ---: | ---: |
| Linux amd64 / arm64 | 1559 | 544 | 0 |
| macOS amd64 / arm64 | 1577 | 544 | 0 |
| Windows amd64 / arm64 | 1470 | 669 | 0 |

每目标 VM **101** 与 Qt **4** 均零跳过，并对照精确 required-cases 与 JUnit。
source → processed → frozen → installed helper 摘要、bootstrap、QuickJS-NG **0.18.0** /
revision **`d7215b0ad3fc2c562e8317fd9bca058fd4863804`** 及目标 OS/架构一致。
Windows 的 `runner.c` 和 bootstrap 摘要与精确 Git blob 的 CRLF 物化字节匹配；Linux/macOS 与 LF 字节匹配。
两份 DEB 和两份 DMG 另直接提取 helper、receipt、payload manifest 和许可证，核对实际字节与原生映像；
DMG 均只读挂载并卸载。没有在 macOS 重运行 Windows/Linux 应用；Windows helper/payload 的运行事实来自该次原生 CI。

003 的集合专项为 **226 passed、0 skipped/failed/error**：精确测试清单和 JUnit 一致，所列源码哈希逐项匹配 003，
六个独有、认证、loopback、原生 MongoDB 8.0.34 fixture 的归属、就绪与清理回执通过。
其它安全范围分别为 TLS/SSH **75**、SQL Server/Redis **131**、MongoDB 协议 **289**、MongoDB TLS/SSH **22**、
JS 协议 **193**、JS Qt **12**，均零跳过/失败/错误；这些是不同范围，不相加成全源。

独立总收据为原 main 工作树的 `artifacts/ci-003c486/verification.json`，`passed=true`。
原始 artifact ZIP 的 GitHub digest 留存但未重新计算；这里逐字节计算的是六个解压后的实际安装器，不能称为 ZIP 摘要复算。
这一预检证明 003 的原生构建及所述安装/运行链，**不证明 0.10 精确源码、正式公开发布、人工验收或完整 Navicat parity**。

## 后续交付与完整目标门禁

本版仍需冻结精确源码并完成六原生安装器验证，随后在正式发布流水线中核验完整六资产、
`SHA256SUMS` 和 `build-manifests.json`，再匿名下载和交叉检查实际公开字节。不能重用 003 的开发安装器作为 0.10 正式资产，
也不移动旧 tag 或覆盖失败历史。

原版内嵌索引/键编辑、完整验证器 UI、全部高级集合/排序规则选项，其它数据库和认证/对象工具、
查询与向导、AI、模型、BI、备份/自动运行和三个操作系统的逐项参考验收均继续开放。
参考运行观察、源码测试、CI、安装器及完整 UI/功能验收分别记录，任一可用里程碑都不构成全目标完成。
用户 Dell Windows/RDP、无桌面 WSL GUI 与既有现场继续按原要求保留。

## 0.10 唯一演示实录

GIF 使用当前0.10元数据的 Cocoa Qt QWidget 实际渲染，SHA256 为
`f9a55bbe0f5d84361c9e5f441d1fd045183d9976002bb220cc5bb27f7fdcf72e`，1,094,508字节。
17个阶段均检查了编码后代表帧，连接/SQL/客户/商品/BSON/GridFS图像均为新隔离工作区生成；
逐帧可见文本检查未发现真实连接、主机、个人路径、凭据或业务数据。未使用原版截图。
SQLite表更名和索引脚本仅预览；MongoDB普通集合的存储引擎与索引默认值实际保存，
另由独立原生客户端读取catalog、WiredTiger与默认索引配置并核验空集合，再重开只读页。
录制曾因缺少ffprobe、后因RGB/RGBA切换导致编码失败，均未替换旧GIF；统一opaque RGB32后完整重录通过。
此录制不等于全部参考UI/功能验收。
