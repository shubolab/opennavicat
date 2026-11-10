# Linux Navicat 原生元数据发现与复核门禁

本独立分支先以 `ea2cd34` 完成保守的默认来源发现和安全前缀检查，随后基于 Linux18.0.3 的自有离线 save-only 记录，实现 **PascalCase 原生 JSON 的非秘密元数据投影**。默认 Linux 路由不调用 macOS `_convert`，不迁移密码、启动原版或访问数据库。已识别记录会显示为待复核连接，**全部 `blocked=True`，必须由用户编辑、确认并保存设置后才解除导入门禁**；未知认证、SSH/TLS、附加或活动配置不会被猜测为可用连接。源码基于 `dd7f8ab`，与 0.7/0.7.1 发布候选分离，未改产品版本、UI、README 或发布资产。

## 路径与 API

官方支持资料给出的连接来源为 `~/.config/navicat/Common/connections.json`；Linux18 手册 p360 给出的分组文件为 `~/.config/navicat/Premium/Profiles/vgroup.json`。大小写是 `Common`、`Premium`、`Profiles`。这与 macOS `Navicat Premium/Profiles` 不同。[官方连接存储说明](https://help.navicat.com/hc/en-us/articles/219566088-How-secure-is-Navicat)、[Linux18 手册](https://www.navicat.com/manual/pdf_manual/en/navicat_18/linux_manual/navicat_en.pdf)

`opennavicat.navicat_linux` 提供：

- `linux_source_candidates(home=None, environ=None)`：只生成默认目录、有效绝对 `XDG_CONFIG_HOME` 和一个已由官方 flatpakref 确认的 `com.navicat.premium.en` 候选，不扫描目录或猜测其他 app ID/Wine 前缀。相同字面路径去重，`candidate.source_id` 不依赖它来自哪个路由。
- `inspect_linux_source(candidate)`：使用 POSIX `dir_fd`、`O_NOFOLLOW` 和普通文件检查，拒绝父/最终链接、非普通文件、超限和读取竞态。只读取 BOM/有限空白后的一个容器前缀字节；不会读取 JSON 键或值。32 MiB 文件上限、64 KiB 前导前缀上限并不代表任何 schema 已接受。
- `discover_linux_sources(home=None, environ=None)`：返回路径隐藏的 inspection/report 与脱敏问题；路径不存在、不可安全读取和需要复核分别表示，不以读取失败冒充有效空来源。

Flatpak 的额外候选是 `~/.var/app/com.navicat.premium.en/config/navicat/Common/connections.json`。它由官方 app ID 与 Flatpak `XDG_CONFIG_HOME` 规则推导，尚未做 Navicat Flatpak 运行验收。环境变量相对路径、父目录回退、NUL 和 UNC 形式被拒绝，不展开环境变量或跟随链接。[Flatpak 官方目录规则](https://docs.flatpak.org/en/latest/conventions.html)

全局 Linux Profiles Location 的可配置键和值仍未知；Windows `ProfilesPath` 不能移植。每连接 Settings Location 主要容纳查询/备份等文件，不等于全局连接 JSON 或分组目录。[官方分平台迁移说明](https://help.navicat.com/hc/en-us/articles/218281627-How-to-get-the-connection-information-when-migrating-Navicat-to-new-computer)

显式 `discover_navicat_profiles(root=..., platform='linux')` 保留原来的 recognized-shape **legacy/synthetic 兼容 API**，来源改标 `linux-legacy-json`，始终 `blocked=True`、包含 `linux_native_format`、`config_complete=False`。它不从默认原生发现调用，不能用来绕过默认门禁，也不代表 Linux18 写入该形状。既有 local-review/config/groups 保全契约继续生效。

## 受控参考实证：Linux18.0.3

2026-11-11 在唯一自有 ARM64 Ubuntu24 容器中，以只读官方 AppImage 解包内容、隔离 HOME/XDG 和完全断开的容器网络，运行原生 Qt/XCB 应用。没有安装桌面，也未操作 Dell、WSL、用户现存 profile 或许可。首次可行性探针停在 Trial Reminder 并清理；获得普通继续按钮属于参考运行范围的明确授权后，新建独立环境继续既有评估窗口。没有接受新条款、注册账号、付款或导入许可。

- 当前下载页面指向 `navicat18-premium-en-aarch64.AppImage`，347,193,624 字节，SHA256 `d89cc67ea31587053d07deb2cffbf3b1d96a572d3f58b143eab491f398aa01bf`。
- 运行 splash 实际显示 **Version18.0.3 - Unregistered**。macOS 指定参考仍为 18.0.5 build321，不能混用平台 patch 号。官方 Linux 发布记录也列出 18.0.3。[官方下载入口](https://www.navicat.com/en/download/navicat-premium)、[发布记录](https://www.navicat.com/en/products/navicat-premium-release-note.html?layout=blog)
- 通过 UI 的 OK 保存一个生成的 PostgreSQL profile、一个使用自有临时 SQLite 文件的 profile，密码留空并取消保存密码；未点击 Test Connection 或 Open Connection。
- 真实写入路径是上述 `Common/connections.json`，结构为 `Users[] → {UserUUID, Projects[]} → {Name, ProjectUUID, Servers[]} → server`。
- server 使用 PascalCase：`ConnType`、`Name`、`Hostname`、`Port`、`InitialDatabase`、`Username`、`ConnectionUUID`、`UseSSL`/`SSL`、`UseSSH`/`SSH` 等。SQLite 使用 `DatabaseFile`、`IsEncrypted` 等。两个生成记录的 `ConnectionUUID` 都是空字符串，`Profiles=[]`、`ActiveServerSettings=''`。
- 创建一个 `CONNECTION` 组并通过 Manage Group → Move To 加入 PostgreSQL profile。实际 `Premium/Profiles/vgroup.json` 为 `version='1.1'`、`vgroups[]`、`vgroup_name/vgroup_type/items`，成员为 `name/type/server_type`；另有 `connections=[]`。
- File → Exit 后以相同隔离 HOME 重启，UI 显示两条 profile 和已保存分组；两个元数据文件 SHA256 在重启前后保持一致。随后再次正常退出，精确核验并删除容器、操作 HOME、评估状态和临时 SQLite 文件，只保留脱敏结构、生成数据回执及私有证据图。

这些受控记录证明了两种生成 profile 的容器结构和一个连接分组的保存/重载。它们**不证明九驱动字段、SSH/TLS/认证枚举、附加/活动 profile、非空 UUID、跨命名空间身份、Flatpak 或完整迁移已经验收**。尤其是包内静态小写 `usessh` 字符串不能替代实际 PascalCase 文件写入证据；已有 macOS reader 不能直接用于该文件。

原始 REA 与运行证据保留在 ignored `artifacts/navicat-linux-discovery/`。静态 REA 已导出6条记录并关闭；两个受控运行环境均已按完整容器 ID、名称和 ownership label 清理。没有将 Navicat 图像或原始用户配置放进本分支。

## 验证与后续门禁

新增40项合成反例通过，均使用临时生成的 HOME/文件：精确路由、稳定来源 ID、链接/非普通文件/大小限制、权限与读取竞态、仅前缀读取、默认路由禁止 macOS 转换器，以及 Store 的既有配置、秘密、review 和分组保全。元数据相关回归共234项通过，`-Werror`。

基于旧 `dd7f8ab` 的全量运行结果为955 passed、315 skipped、7 failed；7项属于该基线已知的 port 样式/字体测试隔离问题，由其他分支的独立修复处理，本分支没有修改对话框或该测试来掩盖结果。最终集成仍需在更新后的基线和原生 Linux CI 重跑；未宣称六平台发布通过。

上述数量是基础 checkpoint 的历史结果，不能代替后续原生 parser 的测试。默认路径基础和原生元数据可见，都不等于完整迁移、认证或六平台验收完成。


## 原生投影 checkpoint

后续在另一唯一自有 ARM64 Ubuntu24 容器中，使用相同只读官方 artifact、全新隔离 HOME 和断开的网络，额外通过 UI 只保存 MySQL、MariaDB、SQL Server、MongoDB 和 Redis 五条生成记录。没有输入密码，也未点击 Test Connection、Open Connection 或访问服务器。正常 File → Exit 后重启，五条记录均可见且 `connections.json` SHA256 完全相同。此轮证据独立存放，没有覆盖先前 PostgreSQL/SQLite/分组记录。

| UI 家族 | 实际 `ConnType` | 本 checkpoint 映射范围 |
| --- | --- | --- |
| MySQL | `MYSQL` | 名称、主机、端口、用户名 |
| MariaDB | `MARIADB` | 名称、主机、端口、用户名 |
| PostgreSQL | `PGSQL` | 名称、主机、端口、用户名、初始数据库 |
| SQLite | `SQLITE` | 名称、未加密本地文件路径；不打开文件 |
| SQL Server | `MSSQL` | 名称、主机、端口、用户名、初始数据库；认证/TLS 仍须复核 |
| MongoDB | `MONGODB` | 基本端点；仅实存的 `None`/`Standalone`/`Primary` 和 retry 布尔值被识别，其他设置不猜测 |
| Redis | `REDIS` | 基本端点；仅实存的 `None`/`Standalone` 被识别，默认数据库 0 仍须复核 |

新增五条记录同样使用 `Profiles=[]`、`ActiveServerSettings=''`、`ConnectionUUID=''`。Mongo 的 `AuthMechanism='None'`、`ConnMethod='Standalone'`，Redis 的 `AuthenticationMode='None'`、`Type='Standalone'` 是 save-only 元数据观察，不是认证协议验收。SQL Server 准备设置页时两次出现原版可恢复 `SIGSEGV / L80003134130` 提示，普通 OK 后仍可保存；不能把该隔离环境中的错误推广为所有 Linux 安装问题，也不能把其 `AuthType='SQLServer'`、`UseEncryption=True`、`TrustServerCertificate=False` 当成完整 TLS 策略已接受。

实现入口为 `navicat_linux_native.discover_native_linux_profiles(home=None, environ=None)`，由默认 `discover_navicat_profiles(platform='linux')` 调用。`navicat_linux.read_linux_projection(candidate, spec)` 复用与前缀检查一致的普通文件、父目录和最终 entry 的前后绑定；解析结果只有在文件与整条目录链仍匹配时才返回。显式 root 的旧 `linux-legacy-json` API 保持隔离，不能绕过原生安全门禁。

`navicat_linux_json.project_json(stream, spec, ...)` 是白名单投影扫描器。JSON 是单文件，所以扫描器必须读取并校验整个字节流；**未选中的值只接受语法、UTF-8 和 escape 校验，不构造、解码或保留其字符串值**。`Password`、`EncryptPassword`、不透明设置以及整个 SSH/SSL 子树不会进入连接配置、错误、日志或 workspace。未知 key 只以解码后 Unicode 码点的 SHA256 检测重复，不构造原 key 字符串；转义等价重复 key 同样被拒绝。只有预先声明的元数据字符串会调用标准 JSON 字符串解码器。

- 总输入最多 32 MiB，单次底层读取最多 8 KiB，最多读取 cap+1 字节辨别超限；先检查普通文件和大小再读。
- 最多 32 层容器、100,000 个值；单个允许标量的编码 token 最多 64 KiB，整数另限 128 位十进制数字，不将 float/exponent 强转成端口。
- 完整语法、EOF、重复 key、非法 UTF-8、NaN/Infinity、全局超限或读错误会丢弃整个投影。`ParseError` 为常量文本且没有保留原异常的 `__context__`/`__cause__`。允许字段的类型/长度错误变为脱敏 invalid 标记。
- 只按 `Users[] → Projects[] → Servers[]` 的显式角色定位记录，不递归搜索“像连接的字典”。未知家族仍显示为不支持且待复核的记录，未知 wrapper 不会被当成有效空来源。
- 身份包括精确来源文件、UserUUID、ProjectUUID、ConnType 及非空 ConnectionUUID 或空 UUID 的名称 fallback。相同字面路径来自默认/XDG 路由时身份不变；不同命名空间不合并。重复记录或命名空间保留独立待复核项，并保护首次记录的身份；无法区分的重复项不承诺任意重排后身份稳定。缺失/无效命名空间使用位置 fallback 且保持 incomplete-source 门禁。
- 不完整记录仍可首次显示，`config_complete=False` 阻止它覆盖已经知道的配置；解析恢复后才允许更新未手工复核的行。既有手工配置、review 状态、进程秘密和收藏 ID 按原 Store 契约保留。
- 分组只读同一来源的确切 `Premium/Profiles/vgroup.json`；只接受已实存的 version1.1、空 `connections`、平面 CONNECTION 结构及确切家族 token。不使用 macOS 家族别名放宽接受范围。未知嵌套/成员、同名同驱动跨命名空间歧义、损坏/缺失/链接/竞态都令 `groups_complete=False`。首次可保留已知且不歧义的 membership；有权威历史时保留 last-known-good，完整有效空分组可清旧分组。

原版新增运行的 exact container ID/owner、正常退出/重启、断网、五条计数和清理回执位于 ignored `headless-drivers-20261111/`。容器、操作 HOME 和评估状态均已删除，只保留脱敏结构/生成样例与私有证据图。未触碰 Dell、WSL、用户现存配置或任何业务数据库。

尚未闭合：Linux SSH/TLS 和各认证模式的主动 save-only 枚举、附加与活动配置、非空 UUID 原生行为、复杂/嵌套分组、Profiles Location 可配置来源、Flatpak 运行、从实际用户存量元数据到手工确认后的连接闭环，以及后续六平台安装包验证。所有原生记录的统一复核门禁保留；此分支没有宣称完整 Linux 迁移或原版功能/UI 已对齐。


本 checkpoint 最终元数据回归结果：macOS ARM64 / Python3.13.10 为 **368 passed、0 skipped、0 failed**；独立原生 Linux ARM64（Debian13.7、Python3.13.18、无架构仿真参数）为 **367 passed、1 skipped、0 failed**，均使用 `-Werror`。唯一跳过是 root UID 会绕过 POSIX 文件权限的测试，未伪称已验证拒绝读取；链接、非普通文件、读取竞态、原生 JSON/分组/Store 保全均实际运行。73项流式投影测试和71项原生适配测试包含在这些总数中，不另加算。

Linux 测试从冻结的只读源码快照执行；回执核对最终五个相关源码/测试 SHA256，无工作树漂移。依赖仅装进唯一自有测试容器，测试时断网，结束后完整 ID/owner 核验并移除该容器及其中依赖，remaining-owned 为0。第一轮最小环境缺 `keyring` 导致33项既有 Windows 合成测试无法 import，该失败日志保留；补入项目已经声明的同版本依赖后通过，没有修改测试期望掩盖环境失败。以上是解析与状态保全测试，不是 OpenNavicat Linux 安装包或桌面 GUI 的本轮发布验收；六平台正式安装验证仍留到后续集成里程碑。
