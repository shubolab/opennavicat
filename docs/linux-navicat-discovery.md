# Linux Navicat 连接元数据加载

这是已发布 0.8.1 中的 Linux 迁移范围，最终组合代码与六平台原生安装资格见 [0.8.1 验证记录](validation-0.8.md)。它加载连接的非秘密字段和已确认的分组结构；**所有 Linux 原生来源连接都保持待复核、禁止直接连接的状态**。用户检查连接设置、补齐认证和传输配置并保存确认后，才解除支持驱动的导入门禁。完整 Navicat 迁移与界面、工具功能还原仍在进行。

## 参考版本与证据范围

各平台的参考版本分别限定，不能把同名字段或版本号视为相同存储协议。

| 平台参考 | 已确认范围 | 未据此确认的范围 |
| --- | --- | --- |
| Linux Premium 18.0.3，官方 ARM64 AppImage | 隔离、断网、只保存样例的七类驱动；原生 JSON 容器、字段大小写；一个连接分组；退出并重启后的文件稳定性 | 主动 SSH/TLS、完整认证枚举、附加/活动配置、复杂 namespace 和非空连接 UUID、Flatpak 运行、完整存量迁移 |
| macOS Premium 18.0.5 build321 | 独立观察的 macOS 元数据与界面参考 | Linux reader 不采用 macOS 的 SSH/TLS 枚举或 plist 形状 |
| Windows Premium 18.0.6，官方 PE 静态参考 | 存储/字段入口线索；注册表读取机制另有隔离原生 API 测试 | 当前 Windows18 实际活跃存储、真实保存后的完整格式仍未受控接受；静态声明和 API 测试不能替代这一资格 |

Linux 参考通过 UI 保存生成的连接，密码留空，没有执行 Test Connection 或 Open Connection。七类样例退出、重启后的结果来自受控参考运行，不能扩大为真实数据库认证成功。SQL Server 设置页准备时出现过可恢复的原版错误提示，关闭提示后保存成功；本模块没有据此承诺其 Linux 认证或 TLS 行为。

[官方连接存储说明](https://help.navicat.com/hc/en-us/articles/219566088-How-secure-is-Navicat)、[Linux18 手册](https://www.navicat.com/manual/pdf_manual/en/navicat_18/linux_manual/navicat_en.pdf)、[官方下载入口](https://www.navicat.com/en/download/navicat-premium)

## 自动查找范围

默认 Linux 路由只检查以下固定候选，不递归搜索用户目录：

| 来源 | 连接文件 |
| --- | --- |
| 标准原生目录 | `~/.config/navicat/Common/connections.json` |
| 有效绝对 `XDG_CONFIG_HOME` | `$XDG_CONFIG_HOME/navicat/Common/connections.json` |
| 已确认官方 app ID 的 Flatpak 候选 | `~/.var/app/com.navicat.premium.en/config/navicat/Common/connections.json` |

同一字面路径来自不同路由时只读取一次。Flatpak 路径由官方 app ID 和其配置目录规则推导，尚未完成 Navicat Flatpak 运行验收。[Flatpak 目录规则](https://docs.flatpak.org/en/latest/conventions.html)

每个连接文件对应的分组候选为同一 `navicat` 配置根下的 `Premium/Profiles/vgroup.json`。`Common`、`Premium`、`Profiles` 的大小写保留。Linux 全局 Profiles Location 的可配置来源仍未知，不套用 Windows 的 `ProfilesPath`，也不把单连接的 Settings Location 当作连接 JSON 或全局分组目录。[官方分平台迁移说明](https://help.navicat.com/hc/en-us/articles/218281627-How-to-get-the-connection-information-when-migrating-Navicat-to-new-computer)

相对路径、父目录回退、NUL、UNC 形式、父目录或最终文件链接、非普通文件和读取竞态均不成为可信来源。没有扫描旧 Wine 前缀、未知 Flatpak app ID、日志或 `ui_connections.json`。

## 可见的连接字段

原生文件按 `Users[] → Projects[] → Servers[]` 的显式角色读取，字段使用 PascalCase。已确认家族与基本投影如下；表中每一类仍要求手工复核。

| 原生 `ConnType` | 驱动 | 基本可见字段 |
| --- | --- | --- |
| `MYSQL` | MySQL | 名称、主机、端口、用户名 |
| `MARIADB` | MariaDB | 名称、主机、端口、用户名 |
| `PGSQL` | PostgreSQL | 名称、主机、端口、用户名、初始数据库 |
| `SQLITE` | SQLite | 名称、未加密本地数据库文件路径；发现时不打开数据库文件 |
| `MSSQL` | SQL Server | 名称、主机、端口、用户名、初始数据库；认证和加密设置继续复核 |
| `MONGODB` | MongoDB | 基本端点；只识别实存的 None/Standalone/Primary 与 retry 布尔值，其他认证/拓扑不猜测 |
| `REDIS` | Redis | 基本端点；只识别实存的 None/Standalone，默认数据库0仍须检查 |

未知家族在可识别的 server 容器内保留为不支持、待复核的条目。未知外层格式不会被递归猜成连接，也不会当成权威空来源清理旧条目。云供应商专属设置、未经确认的认证/SSH/TLS/HTTP/套接字和兼容选项不自动转成可用配置。

原生记录中的 `Profiles`、`ActiveServerSettings` 和不透明属性没有得到完整资格。非空、缺失或不明的 active/variant 状态保留不完整快照门禁，不从嵌套记录覆盖基础身份或连接参数。关闭传输开关留下的 inactive 子树不会被误当作启用设置。

数据库密码、SSH 密码/口令以及 TLS 私钥口令需要按实际连接要求重新填写。本功能不会解密、提取或迁移 Navicat 密码，不读取其钥匙串，也不复用未知的密钥设置。已经由用户在 OpenNavicat 手工保存的配置和凭据继续走既有本地保护机制。

## 去重、部分失败与分组保全

- 正常重读按来源文件、UserUUID/ProjectUUID namespace、驱动及连接 UUID 或名称 fallback 建立身份。同一来源反复导入不增加重复行；不同 namespace 不因同名而合并。重复或无法区分的记录保持独立、待复核，不承诺不明重复项任意重排后的身份稳定性。
- 来源缺失仅标记来源状态，不硬删除本地连接。解析失败、未知结构或不完整记录不会把已有连接参数覆盖成空值。缺失的 `AuthSource` 与明确存在的空字符串有区别。
- 手工检查保存过的连接参数、review 状态、进程秘密和收藏引用按原有本地保护契约保留。来源重新出现、且基本配置快照完整时，只更新仍由来源管理的配置。
- 分组只接受已确认的 version1.1、空 `connections`、平面 CONNECTION 结构及确切家族 token。同名同驱动跨 namespace 不能唯一定位时，不猜测目标。
- 未知 group-root、group 或 member 选项不授权未经确认的新归组；其他明确有效的成员仍可在首次部分导入中保留。缺失、损坏、链接或竞态保留 last-known-good 分组；完整有效的空分组可以清旧来源分组。
- 界面显示 fallback 不是源名称，不用于分组匹配。分组仍由完整有效来源更新驱动，手工保存连接参数不会把来源分组变成永久不变的快照。

`config_complete=True` 只表示本次基本配置投影完整，不能据此解除 review 或宣称全部源设置已支持。空缺 namespace、非空连接 UUID 的真实运行行为、复杂/嵌套组与活动配置仍保留后续验收门禁。

## 只读与资源边界

连接 JSON 是单文件，扫描器读取并校验整个有界字节流，仅解码白名单元数据。未选中的密码、不透明值和 SSH/SSL 子树只接受 JSON、UTF-8 与 escape 校验，不构造或保留其字符串值。未知 key 只用 Unicode 码点摘要检测重复，不进入诊断；转义等价的重复 key 同样拒绝。

文件最多32MiB、容器最多32层、值数量最多100,000；单次读取最多8KiB，单个允许标量最多64KiB，端口不会从浮点或指数数值强转。完整语法、EOF、重复 key、类型与范围错误都不冒充成功导入。普通文件和整条父目录链在读取前后核对，解析结果仅在绑定仍有效时返回。发现过程不启动 Navicat、不打开数据库、不写回来源。

显式指定 root 的旧 recognized-shape API 保留为 `linux-legacy-json` 兼容入口，始终 blocked 且不完整；默认原生路由不调用它，也不会绕到 macOS 转换器。

## 当前资格与剩余门禁

Linux 模块独立集成在0.7.1源码基线上完成 `-Werror` 验证：

| 范围 | 结果 |
| --- | --- |
| macOS ARM64 全源，Linux模块修补后 | 1155 passed、315 skipped、0 failed |
| 元数据相关六模块 | 395 passed、0 skipped |
| 原生 Linux ARM64 / Debian13.7 / Python3.13.18，同范围 | 394 passed、1 skipped、0 failed |

全源跳过的是未启用的自有 DB/TLS/SSH/GUI 专项及 Windows 原生 HKCU 测试；Linux唯一跳过是 root UID 会绕过 POSIX 读权限检查。文件/目录竞态、链接、非普通文件、流式投影、身份与 LKG 保全已实际运行。自有 Linux 测试容器已清理，源码快照无漂移。

七类驱动的既有生成、脱敏导出也完成基本字段投影和重复导入检查。导出中的 namespace ID 已重新合成，早期样例还故意省略了部分字段；它们不是完整原始配置文件，不能扩大为真实 namespace、认证或完整迁移验收。

最终组合已按 0.8.1 固定源码完成统一全源、六平台原生构建与安装验证，公开安装包及其边界见 [发布验证记录](validation-0.8.md)。主动认证/SSH/TLS、活动配置、复杂分组、可配置 Profiles Location、Flatpak 运行及实际存量到确认后连接的完整闭环继续开放。本模块和这些测试均不代表完整 Navicat UI/工具功能已经还原。
