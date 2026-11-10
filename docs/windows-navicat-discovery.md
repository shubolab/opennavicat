# Windows Navicat 元数据发现：待复核的原生读取

这项实现把 Windows 当前用户的 Navicat 存量连接作为**待复核配置**显示，不代表已经验收 Windows Navicat 18 的原生存储格式。它不运行 Navicat、不打开数据库、不自动连接、不解密密码、不查凭证库，也不写入来源。用户在现有连接设置中编辑并保存后，既有本地编辑保护会保留其确认后的配置。

此变更在独立 `work/windows-navicat-discovery` 分支开发，并已随 0.7.1 发布；原生来源格式和用户现场验收仍按下文边界记录。

## 证据边界

- [官方支持资料](https://help.navicat.com/hc/en-us/articles/219566088-How-secure-is-Navicat)确定 Windows 连接信息位于 `HKEY_CURRENT_USER\Software\PremiumSoft`，没有公开每个值的类型和语义。
- [官方 Windows 18 手册](https://www.navicat.com/manual/pdf_manual/en/navicat_18/win_manual/navicat_en.pdf)第 364 页说明连接分组使用 Profiles Location 下的 `vgroup.json`，Profiles Location 可配置，不能照搬 macOS 路径。
- 2026-11-10 从官方 `dn.navicat.com` 下载的 Windows 安装包版本资源是 **18.0.6**，SHA256 `3b50774197d1881b70041bc3c51acbdb427f8082b1a83bfd94bca5fd84c2cfb0`；选定的 macOS 运行参考仍为 **18.0.5 build 321**。
- 该 Windows 包只离线解包，未执行或安装。REA 导入证据 `ev_3041954df892e1deb13cd217aaee6b05d3b9a91be15c1fce4d7be3926085f02f`、`ev_7ea576000ea9858194c02c7cd1272e79e6aff64c7b968d6995a8cdc3b5a83d04` 和同一文件的有界字符串检查，确立了下面的注册表族、原生查询 API 与候选字段。静态声明不能证明其当前活跃读取路径，不能排除旧格式迁移代码。
- Dell 当前检查用户没有 PremiumSoft 根或 Navicat 卸载登记，因而没有取得真实 Windows 18 profile 的字段类型/值/枚举实证。未枚举其他用户，也没有读取私人连接或凭证值。

## 原生读取范围

`navicat_windows.py` 仅打开 HKCU，访问权限只有查询和枚举，分别检查 64/32 位视图。以下静态声明的 `Servers` 族可产生可见配置：

| PremiumSoft 子键 | 显示驱动族 |
| --- | --- |
| `Navicat\Servers` | MySQL |
| `NavicatPG\Servers` | PostgreSQL |
| `NavicatORA\Servers` | Oracle，保留未实现状态 |
| `NavicatSQLITE\Servers` | SQLite |
| `NavicatMSSQL\Servers` | SQL Server |
| `NavicatMARIADB\Servers` | MariaDB |
| `NavicatMONGODB\Servers` | MongoDB |
| `NavicatREDIS\Servers` | Redis |
| `NavicatSnowflake\Servers` | Snowflake，保留未实现状态 |
| `NavicatDameng\Servers` | Dameng，仅保留未实现状态 |

字段名称、类型和尺寸通过 `RegEnumValueW(lpData=NULL)` 枚举，不提供值数据缓冲区。Python `winreg.EnumValue` 会同时返回值数据，因此实现中不用它。单独的有界 `RegQueryValueExW` 再次检查白名单，只读取以下候选明文元数据：`Host`、`Port`、`UserName`、`Database`、`DefaultDatabase`、`DatabaseFile`、`DBFileName`。匹配只允许 ASCII 字段名的大小写变化；不把 Unicode 相似字符映射为白名单字段。[Microsoft API 契约](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regenumvaluew)

密码、密文、密码保存标记、SSH 密码/私钥、SSL 密钥材料、认证枚举及 `serverSettings` 等 opaque blob 均不进入值查询。四个 OpenNavicat secret 字段保持空。二进制和可展开文本不被当成普通字符串读取；未知类型、过大值、改变中的快照和不可读记录产生复核问题，不回显原始异常。

Windows 的静态 `usessh` 与已观察 macOS 的 `usetunnel` 不同。实现不会将它们直接等同，不套用 macOS 的 `_convert`，也不猜测 TLS、SSH、认证或拓扑编码。候选 host/port/user/database 只用于预填；**每一条 Windows 原生配置始终带 `windows_native_settings_review`、`windows_native_format` 和 `blocked=True`**。显式注入的旧 `RegistryProfile` 入口同样保留复核门禁。

远程预填配置采用 `verify-full`，不从未知源状态选择明文 TLS 或创建 SSH 隧道。这个保守初值不代表来源本身使用该模式。SQLite 路径只显示，不打开文件；加密类型等仍须人工复核。

## 去重、分组与来源变化

- 两个视图中的相同来源键，只有取得非空、相同的原生 key identity，并且白名单值、全部字段的名称/类型/尺寸、键修改时间和读取错误状态一致时，才合并为一条。相同 metadata 和时间不能单独证明 alias；opaque 值本身不会为了比较而读取。
- 原生 identity 使用 `NtQueryKey(KeyNameInformation)` 获取同一已打开句柄的 canonical key-name 元数据，仅保留其 SHA256，不记录 SID 或原始键名。Microsoft 明确说明用户态使用 `NtQueryKey`，结构为字节长度加非 NUL 结尾 UTF-16 名称。实现固定 64 KiB 上限并检查实际长度；API 缺失/失败时保留两个 view 来源。它不是已观察的 Navicat UUID，也不证明配置格式已验收。[官方契约](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-zwquerykey)、[名称结构](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/ns-ntddk-_key_name_information)
- 已调查 [CompareObjectHandles](https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-compareobjecthandles)：TRUE 是同一 kernel object 的直接证据，最低 Windows 10/Server 2016；FALSE 不能用来猜 alias。当前实现没有用它代替稳定的 key-name 元数据；原生机制的 Windows AMD64 测试见下文，Windows ARM64 的实际 API 和冻结初始化结果见文末 0.7.1 正式门禁。
- 逻辑来源 ID 始终由驱动、键路径和视图的结构化输入生成，不包含可选原生 identity API 的返回值；同一视图在 API 可用/不可用之间切换不会改变 ID。视图标识不会和合法连接名中的 `#view=64` 等文本碰撞。
- 当前确证两个视图是同一物理键且快照相同时，报告附带显式 `source_aliases`。Store 可沿用此前仅在 32 位视图发现的本地 ID、原始来源 ID 和收藏引用，不因后来优先显示 64 位视图而新建项。当前双视图缺少 proof 或存在冲突时撤回合并绑定，保留先前确认项及另一待复核来源；API 暂时不可用可能因此显示两个来源，不凭旧 metadata/时间推断 opaque 值相同。
- 如果两个来源已经各自存在，之后取得 alias proof 也不删除或合并任何本地记录、确认配置或秘密，不新增第三条，并将两条均标记为仍可见来源及 alias 待复核。视图不一致时保留两条，并发出 `windows_registry_view_conflict`。
- 不按连接显示名合并不同驱动/命名空间，也不对用户键名应用 Python 的 Unicode `casefold`。缺少已验收的原生 UUID 时，键名变化会改变来源身份；旧本地项由既有 Store 保留为来源缺失，不自动删除。
- 只在 `NavicatPremium` 根明确存在普通 `ProfilesPath` 值、并指向本地绝对目录时，尝试该目录下唯一的 `vgroup.json`。该字段与已支持 JSON 结构仍属于候选兼容读取，不是 Windows 原版验收结论；配置始终处于复核状态。
- 不猜默认目录，不扩展环境变量，不读取 UNC 路径，不递归扫描 Documents/Profiles，不跟随已知符号链接或 junction。缺少位置、视图位置冲突、文件缺失、不可读或未知 JSON 结构都产生明确问题。
- recognized `vgroups` 结构只按唯一的驱动/名称匹配。视图冲突或重复来源导致成员歧义时保持未归组，不把连接分到错误分组。
- 记录或族枚举失败不使已读连接消失，并发出既有的 `registry_read_failed`，让 Store 保留先前来源状态。`ImportedProfile.config_complete=False` 的记录可首次显示为待复核项，但不会覆盖已有配置：读取 Host、Port、UserName 或数据库字段失败不能将已有值清空。完整读取恢复后才更新未经本地编辑的来源配置。重新加载不会覆盖已经由用户编辑确认的本地配置与秘密，也不会重新阻止该本地覆盖项。
- `ImportedProfile.groups_complete` 默认 True，保留原有调用契约。Windows、macOS、Linux 的分组文件缺失、读取/解析/结构失败或成员歧义时设为 False：Store 保留已有的 last-known-good `group_paths`，首次导入仍保留已解析且不歧义的有效归属。尚无完整分组历史时，保留并累积这类已知归属；一旦有过完整分组来源（包括有效空分组），不完整重载只保留该 last-known-good 分组。只有完整有效的分组来源才可改变已有完整分组；明确有效的 `{"vgroups":[]}` 可以清空旧分组，且不回退用户确认的本地连接配置或秘密。

## 测试与尚未完成的验收

跨平台合成测试覆盖白名单查询、禁止读取 secret/opaque 值、全部静态族可见、来源只读、跨视图去重与冲突（包含同长度 opaque 值不能证明 alias）、私密 repr、受控分组位置、链接拒绝和 Store 本地编辑保护。跨 macOS/Linux/Windows 覆盖分组的成功→缺失/坏 JSON/未知结构/部分无效成员、首次导入、有效空分组及本地确认后重载。既有元数据、Store 和 MainWindow 门禁回归继续运行。

`test_navicat_windows_native.py` 提供 Windows-only 实际 API 测试：它只创建唯一的 `HKCU\Software\OpenNavicatTests\NavicatDiscovery-<UUID>`，从不写 PremiumSoft 真根。它用真实 `RegEnumValueW`/`RegQueryValueExW` 验证 NULL 数据枚举、白名单查询、双视图来源处理、分组文件和待复核配置，最后删除精确的自有测试根。

2026-11-10 已在 **Windows 10.0.26100 AMD64** 运行这两个原生用例：**2 passed、0 skipped，0.63 秒，`-Werror`**。运行环境使用 [python.org 官方 Windows 3.13.7 发布清单](https://www.python.org/ftp/python/3.13.7/windows-3.13.7.json)指定的嵌入版 ZIP，SHA256 为 `e201b2da753a88c1af29d87f9f48af4d64a0fc8522a204ae672bd2c382496701`；`python.exe` 与 `python313.dll` 的 Authenticode 均为 Valid，签名者是 Python Software Foundation。pytest、sqlparse 及其依赖共七个纯 Python wheel 从 PyPI 获取并逐个核对版本 JSON 的 SHA256，只解包到独立自有临时目录，未运行安装器、WindowsApps Python 别名，也未修改系统 PATH。

两个测试根及本轮新建的空 `OpenNavicatTests` 父键已删除，临时运行目录已移除，自有进程为零。没有读取 PremiumSoft、既有 profile 或凭证值，没有数据库连接，也未操作现有 GUI 应用和其他任务夹具。macOS 全源回归为 **878 passed、314 skipped**，其中两个原生 Windows 用例按平台跳过；元数据/Store/MainWindow 相关合成回归为 **194 passed、2 skipped**。这些计数不包含未启用的外部协议夹具验收。原生 API 的两项实跑对应 `939fa9e`；后续配置保全和 Store alias 调和由跨平台合成测试验证，仍需最终 Windows 原生 CI 验证集成。

0.7.0 正式 tag 的 Windows amd64、arm64 源码 job 中，这两项原生 HKCU 用例也已各自实际通过；整体 job 因另外的原生端口布局测试失败，未进入安装包阶段。修补后的冻结包和最终六目标集成已由新的 0.7.1 门禁实际通过，不把原生 API 单项成功表述为整个旧 job 成功。

**这些结果证明原生 API 和自有合成来源读取机制，不能替代 Windows Navicat18 save-only 格式或 Windows GUI 验收。用户现场 Windows 后续验收已延期，不因本机制测试推进现场操作。**

完成 Windows 18 来源验收仍需要受控原版 save-only 样例：逐驱动验证实际活跃路径、REG 类型、身份稳定性、32/64 视图行为、认证与 SSL/SSH 枚举、可配置 Profiles Location、分组格式及重启后的存量加载。只有这些证据允许以后逐项缩小复核门禁。六目标安装包、实际用户 Windows GUI 和完整工具/UI 对齐仍是独立门禁。
