# Linux Navicat 来源发现基础与原生格式门禁

本分支只完成**保守的默认来源发现和安全前缀检查**，没有实现 Linux Navicat 原生连接迁移。默认 Linux 路由不会调用 macOS `_convert`，不会构造猜测的连接、读取 JSON 键或值、迁移密码、启动 Navicat 或访问数据库。发现来源时返回明确的 `linux_native_format_review` 和 incomplete-source 诊断，保留既有本地配置与分组。源码基于 `dd7f8ab`，与 0.7 发布候选分离，未改产品版本、UI、README 或发布资产。

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

下一 checkpoint 应基于受控 PascalCase 结构实现显式角色定位、仅非秘密字段的有界读取、空 UUID 命名空间 fallback、重复/冲突保全、附加与活动 profile 门禁和混合分组 LKG。未知 auth/TLS/SSH、opaque 属性或未验驱动继续 requires_review/blocked，且绝不因发现连接而自动访问数据库。默认路径基础不等于完整迁移完成。
