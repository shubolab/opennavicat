# 0.5 候选：连接管理、Redis、SQL Server 与原生发布门禁

记录日期：2026-11-10，Asia/Shanghai。本文件记录拟用于 0.5 的开发期证据，
不代表 0.5 安装包已经完成、六个平台已经验收或版本已经公开发布。
最终源码版本、完整测试、六目标原生安装报告、发布资产和公开下载核验仍须另行补齐。
历史 0.4.0 的六目标安装及 Windows RDP 结果见 [0.4 验证](validation-0.4.md)，
不替代本轮新功能的原生验收。

## 参考来源与实现范围

当前参考为用户指定的 Navicat Premium **18.0.5 build 321**。
本轮实际观察包括新版连接管理器、独立 SQLite 索引/触发器入口和 Redis 数据工作区；
步骤与未执行项见 [18 版运行观察](ui-reference-18-observations.md)。
Redis 参考观察使用自己的 Redis 7.4.9 容器与 7 个示例键，完成连接、导航和 string 键读取，
未完成其它类型编辑、TTL 应用或 PubSub 执行，不能据此声称六类型参考写入验收。

- 新连接管理器显示九个数据库族、14 个供应商及 54 个观察到的别名，提供搜索、图标/列表切换、
  供应商筛选和 URI 交接。驱动可用性由后端 registry 决定，未知或未实现入口保留明确状态。
  供应商别名进入普通驱动设置，专属认证与云服务兼容性仍须分别验证。
- 选择器接受后交给原连接设置窗口；参考中的同窗多步设置及管理器内编辑既有连接尚未完全对齐。
  URI 输入遮蔽，错误不回显原文；未知、重复或无法保留其语义的安全选项会拒绝。
  超长 URI 会拒绝，避免输入框静默截断尾部 SSL 参数。
- SQLite 独立索引/触发器设计通过真实 catalog 快照和受签封的计划保存，DROP/CREATE 在同一事务中，
  保存前后验证依赖。自动约束索引、表达式/partial 索引及无法安全解释的对象保持保护状态。
- Redis 后端使用原生命令和二进制参数，提供 string/hash/list/set/zset/stream 的读取与结构化修改、
  原生查询文本、TTL、重命名和键删除；当前为 standalone，集群、哨兵、PubSub 等仍有开放门禁。
- SQL Server 使用原生 TDS 协议适配器，开发期执行了真实查询、元数据、事务、取消、TLS 和 SSH 测试。
  这不构成全部 SQL Server 工具、认证方式、权限管理和三平台界面验收。

## 本轮主干 CI 与修复

首轮主干源码 `f97df6fccc36f52f4b9d1419c8bd59ce2f602e40` 的
[42795955737](https://github.com/shubolab/opennavicat/actions/runs/42795955737) 已终态失败，没有发布。
macOS与Linux四个原生目标通过；Windows两个目标各507项通过、207跳过，仅一条SQLite URI测试
错误比较Windows斜杠/反斜杠字符串。已改为路径语义比较，保留信号与不创建文件等断言，产品解析器不变。
新协议安全作业120项通过、11个setup错误源自同一Redis TLS测试容器：官方入口降为UID999后
无法读runner私有证书目录。已在独立Linux卷复现EACCES并用显式redis-server入口修复，
没有降低TLS、CA或客户证书验证。真实Redis50项重新通过，失败日志先保存、最终只清理自有容器。

后续主窗口导入、枚举映射和两项CI修复合并后的本机完整套件实跑
**624 passed / 133 skipped / 0 failed，30.22秒，-Werror**，其中已显式启用真实SQL Server和Redis专项。
跳过项仍是其它未启用的远程/平台范围，不作为协议通过。完整JUnit为
`artifacts/validation-0.5/source-final.xml`；该源测试不是六平台最终安装包证明。

README继续仅保留一段实际Qt录制GIF，0.5源码演示为1440×900、88帧、14.67秒、326571字节，
SHA-256 `b30decae253f4164f42600d4842ebc7db90122101fae8c6d5b2a344c80705500`。各阶段已目视复核，全为生成示例数据，没有读取真实Navicat/OpenNavicat工作区或Keychain。

## 已执行的开发期验证

以下专项来自不同测试范围和执行时点，存在覆盖重叠，不相加为完整套件结果。
Qt 专项在本机 macOS/ARM64 的 offscreen 环境执行；不是 Windows/Linux 的实际界面证明。
本机记录的运行环境为 Python **3.13.10**、Qt **6.12.0**，新增客户端依赖为
python-tds **1.17.1**、redis **8.1.0**、pyOpenSSL **26.4.0**、certifi **2026.10.23**。

| 已执行范围 | 结果 | 证据边界 |
| --- | ---: | --- |
| 连接管理器、既有连接设置及真实 MainWindow 索引/触发器流程 | 87 passed，`-W error` | 选择/搜索/筛选/URI、真实 SQLite 保存与窗口生命周期 |
| Redis 后端及页面组件专项 | 74 passed，`-W error` | 50 个后端、24 个页面用例的专项记录；不等同于参考 UI 全部行为 |
| SQL Server 适配器专项 | 45 passed，0 skipped，`-W error` | 28 个单元用例与 17 个真实协议用例 |
| 发布门禁专项 | 37 passed，`-W error` | GitHub 全流程为 mock，没有执行上传或公开发布 |
| 安装器与发布门禁合同联跑 | 64 passed，`-W error` | 包含上述 37 项；核对新增运行时字段的生产/消费要求 |

最终新增字段合并后，另实际运行源码 `--smoke-test`，返回 **0.5.0 / Darwin / arm64 / SQL 6×4**，
`extra_adapters_constructed=true`、`sqlserver_tls_context_ready=true`、
`sqlserver_default_ca_present=true` 和 `crypto_legacy_probe=true`。
该过程使用隔离工作区，实际经过 Qt 后台查询路径，构造两个真实动态适配器并建立 SQL Server TLS context，
没有向外部数据库建立连接。报告为 `artifacts/packaging-review/source-runtime-smoke.json`；
它是源码运行证据，最终打包程序仍需各自执行同一探测。

真实 SQL Server 为自己创建的 **SQL Server 2022 CU27-GDR 16.0.4305.5 / Developer / X64**
容器。测试包括 require/verify-ca/verify-full、真实 `encrypt_option=TRUE`、错误 CA/默认不信任 CA 拒绝、
SAN 主机名不匹配拒绝、TLS 下 ATTENTION 取消保留事务，以及真实 Paramiko SSH 转发保持数据库原主机名。
主机名仅在 CA 验证通过后按各 SSL 模式处理，没有通过降低验证要求消除失败。
原始 JUnit 为本机忽略目录 `artifacts/sqlserver/sqlserver-session-junit.xml`，
不含凭据的汇总为 `artifacts/sqlserver/fixture-receipt.json`。
Windows→WSL 的任务转发曾出现旧端口和连接超时；重新核对实际端口、独立探针和新转发后完成最终测试，
这些中间故障没有计作成功。

SQLite 主窗口回归覆盖索引改属后保留原数据页目标、原/新所属表任一页有待提交编辑时拒绝保存、
触发器改属视图并实际执行 INSTEAD OF、关闭保存/不保存/取消、取消保存预览、保存后断开连接、
隐藏系统索引、自动索引只读及真实只读连接。
开发期发现并修复了改属后错误改变数据页目标，以及刷新新所属表时丢失待提交编辑的问题。

Redis 独立审查发现 Lua 运行时错误不会回滚已完成写入：尾部 PEXPIRE 被 ACL 拒绝时会丢失 TTL，
列表 marker 写入后的 LREM 被拒绝时会丢失原项。修补使用原生 SET KEEPTTL、去除不必要的尾部 PEXPIRE，
并在复合操作首写前检查每个实际命令的权限；缺少安全预检能力的版本明确拒绝复合操作。
独立新 Redis **7.4.9** 容器复核确认：拒绝 PEXPIRE 时字符串编辑仍保 TTL，分别拒绝
LREM/LSET/SREM/SADD/ZREM/ZADD 时原值、成员、score 和 TTL 均保留，无 marker 残留。
复核容器均已移除，证据为 `artifacts/redis-review/acl-lua-loss.json` 与
`artifacts/redis-review/acl-lua-fixed-independent.json`。
键删除、TTL 和重命名当前按键名执行，不提供完整键修订号 CAS。

## Navicat 既有连接资料的证据边界

macOS 的 Common/conn.plist 与分组 JSON 已完成实际格式调查，并接入普通启动的后台加载、
“Navicat 连接”分组、多分组成员、按来源身份去重、用户编辑保护和安全配置复核门禁。
本机真实来源在隔离Store中实际加载两次，第二次无新增；全部profile的四个secret字段为空，
没有构造数据库会话或调用凭据hydrate，所有会话保持未连接。原始连接名称、主机和来源文件不公开。
回执为忽略目录 `artifacts/validation-0.5/navicat-local-metadata-acceptance.json`。

导入器78项合成测试、存储18项和主窗口10项分别覆盖读取/归一化、单次原子保存、手工配置保护、
来源删除保留、未知字段阻止连接、多个分组显示同一连接，以及目录回调迟到和已连接配置延迟更新。
保存导入设置后须显式打开，不自动连接。demo/smoke/screenshot模式不执行来源发现。

另通过Save-only合成Navicat配置实际确认macOS SSH auth 0/1/2为密码/公钥/组合；
PostgreSQL五TLS模式以mode为权威，allow保存为“允许”，其余为libpq名称。
useauth控制四文件是否生效；关闭后来源仍保留旧路径，导入时不会误用。
密码存储迁移与SSH compression的完整原版验证仍开放，未读取既有密码。
空原生UUID的fallback包含来源namespace；完全缺少该键的未知来源形状仍保留为待处理。
合成profile与临时key/cert均已清理。

Windows/Linux 实际资料验收。

Windows 当前用户的 PremiumSoft/Navicat Servers/Connections 以及 HKCU/HKLM 的 32/64 位安装记录，
在本轮授权的 Dell 主机上均未发现；四个 AppData 候选目录也不存在，元数据错误计数为 0。
因此没有实际 Windows 连接资料可验证原生 registry 字段映射；注入的合成记录测试不作为此项已验。
调查没有读取 profile 值、密码或密文，也没有输出连接名与主机。

WSL Ubuntu 默认用户 UID 为 0，但当前 SSH 上下文的两条 Linux 文件系统入口均不可访问，
官方候选 `~/.config/navicat/Common/connections.json` 的存在性保持未知。
没有读取配置内容，也没有安装桌面或执行 Linux GUI 验收。
本机元数据收据位于 `artifacts/navicat-profile-sources/`。

## 发布门禁与尚待取得的原生证据

发布器继续验证六份安装包的名称、大小、SHA-256、native host 架构、全部原生映像与实际安装/运行/卸载报告。
安装报告须包含新客户端依赖、CA bundle 和真实 OpenSSL cipher probe；另外严格要求
`extra_adapters_constructed=true` 与 `sqlserver_tls_context_ready=true`，不能用字符串或数字代替布尔结果。
这两个字段必须来自最终打包程序实际构造适配器和建立 TLS context 的执行结果。

上传只发生在 draft 中。公开前，远端资产必须恰好为六份安装包、SHA256SUMS 和 build-manifests.json：
拒绝缺失、额外或重复名称，逐项匹配字节长度、`state=uploaded` 和服务端 `sha256:` 摘要。
任一检查或上传失败都不会执行公开步骤；已有公开版本拒绝再次上传。
完整 mock 回归覆盖复用草稿保留旧附件、错误/缺失摘要、未上传状态、上传中断、正常新建及复用草稿。
JUnit 位于 `artifacts/packaging-review/release-gate-junit.xml`，
与安装器合同联跑记录为 `artifacts/packaging-review/gates-contract-junit.xml`。

| 本轮最终目标 | 0.5 原生安装与运行 | 最终公开资产下载核验 |
| --- | --- | --- |
| Windows amd64 | 待本轮最终 CI/报告 | 待发布后核验 |
| Windows arm64 | 待本轮最终 CI/报告 | 待发布后核验 |
| macOS amd64 | 待本轮最终 CI/报告 | 待发布后核验 |
| macOS arm64 | 待本轮最终 CI/报告 | 待发布后核验 |
| Linux amd64 | 待本轮最终 CI/报告 | 待发布后核验 |
| Linux arm64 | 待本轮最终 CI/报告 | 待发布后核验 |

尚无本轮六平台最终安装报告、tag 流水线及公开资产校验记录时，不标记原生发布门禁通过。
需要检查本轮 CI 是否实际启用了新增协议专项，不能把默认跳过的外部服务测试或旧安全作业当作新协议通过。

完整目标仍为三操作系统上的 1:1 界面与全部数据库工具功能。
九驱动全部专有对象、Oracle/MongoDB/Snowflake、Redis 集群/哨兵/PubSub、全部数据库认证/权限、
完整导入导出与同步向导、AI、Profiling/Masking/Dictionary、自动任务、模型与 BI 等门禁继续开放。
产品账号、订阅/激活和云团队账号协作维持既定排除范围；数据库认证和云数据库连接仍在范围内。
完整清单见 [parity-matrix.md](parity-matrix.md)，本轮专项和原生包不替代这些验收。
