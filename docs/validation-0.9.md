# 0.9 候选验证记录

记录日期：2026-11-11，Asia/Shanghai。**0.9.0 仍是候选，同源完整六平台预检已经通过，尚未创建正式 tag 或公开发布。** 当前公开里程碑为 [v0.8.1](https://github.com/shubolab/opennavicat/releases/tag/v0.8.1)，其正式安装与公开资产结果保留在 [0.8.1 验证记录](validation-0.8.md)，不能替代本候选的新代码验收。

本轮增加 MongoDB JavaScript 索引 helpers、命令错误与写入结果表达，并修复连接设置在异步保存文档后丢失的问题。完整 Navicat 工具和三平台 1:1 UI 目标保持不变；产品账号、产品登录、激活、订阅及云账号协作仍排除。

## 完整预检与交接状态

精确冻结源码 **`92079b928f41d00c2b5ed2deeaabd7c20d200d44`** 的
[42901942839](https://github.com/shubolab/opennavicat/actions/runs/42901942839) 为 workflow_dispatch、attempt 1；
六原生安装目标及安全任务全部成功，发布任务按 dispatch 正确跳过。
各目标源码通过/条件跳过分别为 Linux 1440/440、Windows 1351/565、macOS 1458/440，均零失败/错误；
每目标 VM 101 与 Qt 4 均零跳过，source/processed/frozen/installed helper、bootstrap、pin、架构与实际安装运行卸载链均已核验。
安全专项 75/131/Mongo 274/Mongo TLS-SSH 22/JS 193/JS Qt 12 均零跳过，14 个独有 MongoDB fixture 已清理。

用户要求本次收尾后继续交接，未启动正式 tag 流程。920 已正常合入 main，普通集合后端与静态参考收敛为唯一 next 分支，旧分支已清理。
main 合并提交的 SHA 不等于冻结 920；产品目录字节相同也不把精确源资格换成新 SHA。
完整提交/分支、证据路径、剩余门禁和恢复顺序见 [2026-11-11 快照](snapshot/2026-11-11-handoff.md)。

## 源码归属和实际范围

产品代码 checkpoint 为 **`f9c492362afe750510b3c01057bc794991d35ad1`**，以已发布 0.8.1 的产品基础继续整合。随后 `c1d6b21` 合入 0.8.1 发布后文档，未引入新的产品行为；本轮待冻结工作分支为 `work/0.9-release`，版本元数据为 **0.9.0**。

候选保留正确的 Windows helper `.exe` 路径，以及看门狗测试的平台终止契约：阻塞 host 返回前记录取消状态和子进程退出，检查 Windows `1` / POSIX `-9`，继续检查 cancelled、hard-killed、回收和期限。0.8.0 及 d948 预检失败记录不移动、不覆盖。

本机 source helper 是实际编译的原生 ARM64 Mach-O。独立结果审查涉及的桥、运行时、错误分类、显示模块和页面源码哈希与整合 checkpoint 一致。源码执行原生 JavaScript 仍需先运行 `scripts/build-quickjs.py`；最终安装包应沿用随包分发和冻结路径校验，不能在用户查询时下载或编译。

## 八个索引 helpers

当前集合对象提供下列入口。表中的返回值属于本实现及已测试的 mongosh 对照，不是原版 Navicat helper 返回值已经核实的结论。

| Helper | 成功返回 |
| --- | --- |
| `createIndex(keys, options, commitQuorum)` | 单个索引名称 |
| `createIndexes(keyPatterns, options, commitQuorum)` | 索引名称数组 |
| `ensureIndex(keys, options, commitQuorum)` | 索引名称数组 |
| `getIndexes()` | 索引定义数组 |
| `getIndexSpecs()` | 索引定义数组 |
| `getIndexKeys()` | 索引键模式数组 |
| `dropIndex(nameOrKeyPattern)` | 实际服务器结果文档 |
| `dropIndexes(selection)` | 实际服务器结果文档；省略选择表示删除全部可删除索引 |

键顺序及显式 Int32/Long、日期、二进制子类型通过已有 BSON 桥传递。普通 JavaScript Number 仍是 Double；索引方向等明确控制参数按其控制语义处理，不把数据文档的普通数值统一改成整数。常见 unique、sparse、hidden、TTL、部分过滤、排序规则、文本、地理空间等选项经过白名单校验；未知选项明确拒绝，不静默丢弃。命令级超时、comment、写关注和 quorum 不混进索引定义。

只读连接在发送写命令前拒绝写 helpers。列表使用受管理的原生游标和会话，超过交付预算明确失败，不冒充完整数组。`dropIndex('*')` 和数组参数拒绝，调用者需要显式使用 `dropIndexes`；默认 `_id` 索引由服务器保护。helpers 按集合名发送命令，**不等于设计器“执行脚本（集合 UUID）”的身份保护范围**。

真实对照使用新建、独有 MongoDB **8.0.34** 服务中的 **mongosh 2.14.0**，在生成的 namespace 下执行，未使用用户数据库或配置。返回形状、常见选项、缺失对象及错误码有实际服务器结果。已测试 standalone 对显式 quorum/不可满足写关注的拒绝；副本集 quorum、分片、正在构建的索引和 Atlas Search 没有因此获得验收。实现和更多限制见 [JS 索引 helpers](mongodb-js-index-helpers.md)。

**原版 Navicat 18 的这些 helper 行为尚未实际观察。** mongosh 的协议和返回值对照不能替代 Navicat 的重载、默认值、全局/API、类型和 UI 对照，也不能把本轮称为完整 shell 兼容。

## 错误、确认与部分写入

已收到的服务器错误保留有界 `code` / `codeName`，通过适配器包装后仍能在 JS 错误和命令回执中识别。`acknowledged_error` 表示收到错误回复，不能仅因回复已到就把失败 DDL 计为已确认修改。服务器没有提供 `n` 时，每条命令的影响数量保持 unknown；汇总数字为零也不证明没有发生 DDL。

传输中断、丢失写入确认和写关注不确定性使用 `uncertain`，与明确的服务器拒绝区分。较早已确认的写入保留，失败不意味着整个脚本回滚，也不自动重试。JS 可以捕获服务器错误后正常返回；此时脚本成功完成与每条命令成功是两回事，命令回执和警告仍保留。

真实服务用例包含先成功创建索引、再触发唯一索引错误，以及后续语句不执行、原文档不变。丢确认和 concern-error 的两个边界是在实际索引创建后**显式注入**对应回复/异常，不声称它们是实际副本集 concern 或物理网络故障资格。

另有 **7 项独立检查**使用真实原生 helper 和合成父端适配器，未打开数据库网络连接，检查 unknown DDL 影响、已确认前缀、收到错误、确认未知、被 JS catch 的错误及显示警告。它们不是数据库协议测试或实际 GUI 操作；非规范的合成 code 64 缺少 codeName 仍是明确边界，不能外推为支持服务器已复现的错误。相关源码哈希匹配，但这 7 项不加入下面的真实服务或全源总数。

## 连接设置与异步保存

真实临时 SQLite 复现了以下问题：连接设置窗口已经接受并关闭，用户选择第二个文件和只读模式；随后保存脏表设计文档并确认 DDL，表修改完成、原连接断开，但已接受的新设置未继续应用。

修复在 GUI 线程一次捕获配置和四类凭据保存选择，沿现有保存/断开回调继续同一设置操作；**只有精确的原会话真正关闭后才持久化并采用新设置**。手工连接按原行为重连，已复核的 Navicat 导入连接保持断开，等待用户明确打开。取消、文档保存/关闭失败、新编辑替代或窗口关闭都会撤销旧 continuation，后来的保存不会复活它。

普通同 ID 设置修改原本就会切换 SQLite 文件并执行数据库只读限制，本轮没有把未复现的“普通修改仍保留可写旧会话”描述为已修漏洞。已确认 DDL 也不会因后续设置或密钥服务操作失败而自动撤销。

测试使用实际 Qt 设置/DDL 对话框、串行后台队列和独有 SQLite 文件；MongoDB 异步关闭用受控 client 检查生命周期，不打开实际网络端点。四类秘密保存选择使用进程内假 keyring，工作区不含生成秘密；未读取真实 Navicat 来源或系统钥匙串。实现与复现见 [连接设置生命周期](connection-edit-lifecycle.md)。

## 本机已完成验证

下列结果来自 macOS ARM64。协议测试使用新建原生 ARM64 MongoDB 8.0.34，原始 JUnit、collection 和清理回执保留；它们是明确的不同执行范围，不能相加为另一轮全源。

| 范围 | 实际结果 | 限制 |
| --- | --- | --- |
| f9 产品 checkpoint 全源，`-Werror` | **1462 passed、436 skipped、0 failed/error，73.71 秒** | 未启用的外部服务和不适用平台用例按条件跳过。 |
| 0.9.0 版本元数据更新后的最终本机全源 | **1462 passed、436 skipped、0 failed/error，72.18 秒** | 同一产品代码与发布后文档；仍非最终托管六目标快照资格。 |
| 原生 JS 协议 runner 的八模块完整范围 | **193 passed、0 skipped、0 failed/error** | 包含 VM/类型/显示/门禁和真实数据库模块；三份独有服务，不是 193 项都在访问数据库。 |
| JS Qt 生产链 | **12 passed、0 skipped** | 两份新服务，实际组件和主窗口；不是全平台 GUI。 |
| MongoDB 七模块协议 | **283 passed、0 skipped** | 精确 collection 为 fixture 120、非 fixture 163，七份新服务。 |
| 连接设置生命周期，原生 Cocoa | **12 passed、0 skipped、0 failed/error，9.46 秒** | 真 SQLite、受控 Mongo 关闭；不是 Windows/Linux 或原版对照验收。 |
| 独立结果语义检查 | **7 项真实 helper + 合成父端** | 无数据库网络，不是协议或 GUI 资格。 |

全源中 `test_js_native` 的 53 项均实际通过；`test_js_engine` 为 18 通过、22 条件跳过，`test_js_host_reference` 的 16 项及 `test_js_index_helpers` 的 36 项在未启用数据库的默认全源中跳过。后者已在上述零跳过 JS 协议 runner 中完整执行；没有使用“未构建 helper”来掩盖它们。连接设置 12 项也已包含于全源，不重复加总。

三份 JS、两份 Qt 和七份 Mongo 协议服务共 **12 个不同容器身份**均有归属、loopback、就绪及清理回执，另外逐完整身份核验已不存在。测试没有使用手工参考 endpoint 或业务库，没有对 Dell/WSL 执行动作。

本包 `uv.lock` 版本同步为 0.9.0，保留原有公共 PyPI URLs/wheels；`uv lock --check --offline --default-index https://pypi.org/simple` 已通过。该检查不替代六目标实际依赖安装。全源及协议的精确范围、哈希和最终本机结果分别保存为 ignored 的 checkpoint/final 收据，未把待运行结果预写为成功。

README 的唯一 GIF 已用 0.9.0 真实 Cocoa 窗口单次录制完成：**1440×900、179 帧、29.84 秒、705220 字节**，
SHA256 `39a12fa63a6d1cdc057952d52379d0062675ad24d78575f4f110721856b1d01c`。
13 阶段保留 SQLite、MongoDB 网格/树/GridFS、JS 查询和索引草稿/脚本预览；全部资料为生成样例。
JS 结果树和显示 Canonical EJSON 与三份生成 BSON 做字节检查，包含 Int64/Decimal128；
部分结果单元格使用省略号，不能把该数据检查说成屏上始终显示所有字符。
索引预览未执行，GIF不替代新增 helpers 的协议测试。独有示例服务、工作区及帧文件均清理，未访问真实连接、钥匙串或参考应用。

## 尚未完成的候选门禁

- 920 已统一冻结版本和演示并完成同源完整预检；仍须后续正式 tag 运行和公开包验证，当前记录不是公开 0.9.0 Release。
- 六原生目标的完整预检和安全 scope 已由该次独有 artifacts 审计，发布任务正确跳过；不拿它替代尚未启动的正式发布，不拿旧 0.8.1 替代新版本资格。
- 公开发布仍须六安装器加 SHA256SUMS/build-manifests 共八项精确资产、上传状态/字节/digest/SHA、匿名下载及各平台回执；目前没有公开 0.9.0 安装资产可据此验收。
- 原版 helper runtime、全局/API、完整 BSON 与代理/原型兼容、全部拓扑/认证、索引选项和并发边界、三平台实际 UI，以及全部数据库工具继续按[总矩阵](parity-matrix.md)推进。mongosh 不是 Navicat。
- 用户 Dell Windows/RDP 与无桌面 WSL GUI 验收仍延期，现场保持原状；托管 CI 和 Cocoa 组件检查不替代该现场验收。
