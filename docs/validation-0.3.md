# 0.3.0 安全连接与六平台安装包验证记录

日期：2026-11-10（Asia/Shanghai）。本记录区分已运行的测试、已经生成并验证的安装包、
尚未完成的发布门槛。当前是第一版 MVP 里程碑，**完整工具功能和 1:1 UI 验收仍未完成**。
范围继续以 [parity-matrix.md](parity-matrix.md) 为准：保留数据库认证、用户、角色、权限和
云托管数据库连接，排除 Navicat 产品账号、登录、订阅、授权和云账号协作体系。

当前观察参考为本机 **Navicat Premium 16.1.1**。用户计划在第一版含六个安装包的 Release
发布后更新 Navicat；本次尚未重新观察更新后的参考，不能宣称已对照最新版。按用户指示，完成这次 Release
后暂停目标，待用户更新并明确恢复后再继续观察和实现完整目标。

## 独立的真实 TLS / SSH 集成验证

本机 macOS 27.2 ARM64、Python 3.13.10 上实际执行：

```sh
OPENNAVICAT_SECURITY_TESTS=1 QT_QPA_PLATFORM=offscreen \
  .venv/bin/python -m pytest -q -W error -o faulthandler_timeout=20 \
  tests/test_connection_security_integration.py \
  --junitxml=artifacts/security-integration/pytest-results.xml
```

最终结果为 **75 passed，0 failed，0 skipped，41.22 秒**；启用 warnings-as-errors。
JUnit 记录为 41.232 秒。测试直接连接三个独立 Docker 数据库，并通过本进程启动的
Paramiko SSH 服务转发真实数据库协议。没有以构造器、模拟连接或仅导入驱动代替协议验证。

| 项目 | 实际版本 |
| --- | --- |
| PostgreSQL 服务 | 15.19，x86_64 Alpine 容器 |
| MySQL 服务 | 8.0.36 |
| MariaDB 服务 | 11.4.13-MariaDB-ubu2404 |
| psycopg / psycopg-binary | 3.3.8 / 3.3.8 |
| libpq | 18.6（180006） |
| PyMySQL | 1.2.4 |
| Paramiko | 5.1.0 |
| cryptography | 50.0.2 |
| Python ssl | OpenSSL 3.5.4，2025-09-30 |
| pytest | 9.2.0 |

每个数据库驱动执行 25 个用例，合计 75 个。参数化用例中的多次断言未额外计数。

| 验证范围 | 实际断言 |
| --- | --- |
| 服务端 TLS | 有效 CA 的 verify-ca / verify-full；错误 CA 和错误主机名拒绝；verify-ca 允许主机名不同；从服务端状态查询确认协商加密 |
| 客户端 TLS | 客户端证书认证、加密私钥、错误私钥密码、缺失必需证书和服务端撤销客户端证书后的拒绝 |
| CRL | 同一签发者的空 CRL 允许连接，再换成撤销服务端证书的 CRL 后拒绝；保留各驱动原生错误文本差别 |
| SSH 主机信任 | 未知主机和错误 pin 在发送认证前拒绝；明确批准正确 pin 后建立连接；没有自动接受或改写 known_hosts |
| SSH 认证 | 密码、加密 RSA 密钥、缺失/错误密钥口令、独立 ssh-agent、密钥与密码的 partial-auth 组合；需要组合认证时拒绝只有密钥的客户端 |
| 转发与失败清理 | SSH TCP 拒绝、远端转发通道拒绝、实际压缩协商；失败或主动关闭后本地监听端口关闭 |
| SSH 内的 TLS 身份 | 数据库原始主机名 db.test 经过 127.0.0.1 转发仍通过 verify-full，错误主机名失败；客户端证书经 SSH 仍有效 |
| 长查询生命周期 | 独立管理连接先确认长查询实际运行，再取消、复用会话、关闭隧道、重连并检查清理 |
| 凭据编码 | 三个实际数据库均使用含中文字符的测试密码完成协议认证 |

证书、CA、CRL、密钥、数据库账户和 SSH 服务全部为本任务临时生成。数据库只映射到主机
loopback 随机端口，容器带 `opennavicat.test=true` 标记。测试显式隔离 known_hosts、libpq
默认文件和 SSH agent，没有读取用户已有数据库连接、私钥、agent 或证书。最终已检查任务
数据库容器和任务 ssh-agent 全部退出，临时证书、转发监听器及 SSH transport 已清理。

验证过程中发现 PostgreSQL 旧 `cancel()` 经 Python SSH 转发时阻塞。独立进程采样确认调用
停在 libpq 的 `PQcancel` 接收路径；随后改用 `cancel_safe(timeout=connect_timeout)`，
依赖 libpq 17 及以上。该修复先通过单项真实 PostgreSQL 回归，再通过上述完整 75 项。
测试也覆盖了加密 SSH 密钥错误口令分类和 MySQL/MariaDB Unicode 密码的实际认证。

本地证据位于 `artifacts/security-integration/pytest-results.xml`、
`artifacts/security-integration/validation.json`、
`artifacts/security-integration/servers.json` 和 `artifacts/security-test-console.log`。
历史取消阻塞采样和修复后的单项报告保留在同目录。临时密钥和口令不应进入发布资产。
这些结果证明此环境下的安全连接行为，不代表六个平台均完成真实 TLS / SSH 测试。

## 首次原生 CI 的已观察结果

[首次运行 42741474393](https://github.com/shubolab/opennavicat/actions/runs/42741474393)
对应提交 `d942fdb48afdd7ab1e54fec59fb9a697625406fb`。下表为首次运行中已查询到的状态，
不是最终修复提交或 Release 的验收结果。

| 作业 | 已观察状态 | 证据 |
| --- | --- | --- |
| Linux amd64，Ubuntu 24.04 | 测试、原生构建、打包后运行、真实安装/卸载和安装包上传成功 | [job 129583302089](https://github.com/shubolab/opennavicat/actions/runs/42741474393/job/129583302089) |
| Linux arm64，Ubuntu 24.04 ARM | 同上，成功 | [job 129583301607](https://github.com/shubolab/opennavicat/actions/runs/42741474393/job/129583301607) |
| macOS amd64，macos-15-intel | 测试、原生构建、打包后运行、DMG 隔离安装/移除和安装包上传成功 | [job 129583302091](https://github.com/shubolab/opennavicat/actions/runs/42741474393/job/129583302091) |
| macOS arm64，macos-15 | 同上，成功 | [job 129583301868](https://github.com/shubolab/opennavicat/actions/runs/42741474393/job/129583301868) |
| Windows amd64，windows-2022 | 测试步骤失败，未进入安装包构建 | [job 129583302190](https://github.com/shubolab/opennavicat/actions/runs/42741474393/job/129583302190) |
| Windows arm64，windows-11-arm | 原生 libpq 18.4 / OpenSSL 3.6.5 编译成功；cryptography 源码构建未找到 OpenSSL，依赖安装失败，未进入测试/打包 | [job 129583301799](https://github.com/shubolab/opennavicat/actions/runs/42741474393/job/129583301799) |
| Ubuntu 实际数据库 TLS / SSH | 独立安全集成作业成功；以该次上传的 JUnit 计数为准 | [job 129583301482](https://github.com/shubolab/opennavicat/actions/runs/42741474393/job/129583301482) |

Windows amd64 首次失败由测试临时文件名包含 Windows 禁用的 `?` 引起。后续修复为在
Windows 使用合法的空格、中文、`#` 文件名，仍在其它平台验证 `?` 文件名；需新提交重跑
后确认结果。首次失败不能直接证明数据库适配器失败，也不能跳过 Windows 测试。

Windows ARM64 首次成功构建的 vcpkg revision 为
`9a5dfef5d1a0438a7d24856a85bd60f309d355e9`，libpq 18.4#2 和 OpenSSL 3.6.5#2
均为 `arm64-windows`。后续修复显式导出这些本地库的 OpenSSL/Rust 构建路径，并为源码
注册 DLL 目录；打包后与安装后检查移除该开发库目录，验证随包携带的运行库。

最终本地默认测试另发现 Python SQLite 上下文管理器只结束事务而不关闭连接。配置、
历史、样例创建及测试夹具已补上显式关闭，重新执行完整 warnings-as-errors 检查通过。
当前默认套件为 **129 passed / 133 skipped**（需要显式启用的外部服务用例不在默认执行范围），
18.70 秒，零警告和错误；真实安全连接的 75 项另行实际执行，并非以这些跳过项代表通过。

第二次运行 [42743679346](https://github.com/shubolab/opennavicat/actions/runs/42743679346)
开始使用 Cocoa、Windows 和 X11/xcb 实际桌面后端验证安装程序。两个 macOS 原生安装作业
均成功；安全连接 JUnit 再次为 75 / 0 failures / 0 errors / 0 skipped。
Windows amd64 的实际桌面程序已启动成功，随后 Inno Setup 指出动态 AppId 必须设置
`UsePreviousLanguage=no`，已据此修复。两个 Linux 作业在安装前 smoke 暴露构建主机缺少
`libxcb-icccm4`、`libxcb-keysyms1`、`libxcb-shape0`；DEB 已声明这些库，CI 构建依赖已补齐。
上述修复须由后续作业证明，不能以失败作业中的局部成功替代完整安装包验收。
第二次 Windows ARM64 已成功构建 `cryptography-50.0.2-cp313-abi3-win_arm64.whl`，
随后测试收集指出 OpenSSL legacy provider 没有被发现。后续构建显式发现发布版
`legacy.dll`，配置源码进程的 `OPENSSL_MODULES`，将模块打入包内并设置内部运行路径。
独立程序验证清除构建机 provider 环境，还执行公开固定向量的实际 cipher probe，
避免仅导入成功而遗漏 provider。该修复仍需后续原生作业证明。

第三次运行 [42745695032](https://github.com/shubolab/opennavicat/actions/runs/42745695032)
中，Linux 两架构、Windows amd64 和 macOS arm64 均已完成实际桌面后端的安装流程。
Windows ARM64 的模块路径已经发现正确，但系统报告入口点缺失；调查确认 CPython
自带 OpenSSL 3.5.9 与 vcpkg 3.6.5 具有相同 DLL 名称。后续启动在任何 SSL/hash 导入前
按完整路径预加载 SDK DLL，并核对 Windows 实际选中的模块路径；打包时强制同名 DLL
使用 SDK 来源。Intel macOS 的独立实际 cipher probe 也发现了动态 Homebrew OpenSSL
模块遗漏；构建已改为从实际链接库查询 MODULESDIR、收集正确模块并设置包内路径。
这些后续修复仍须对应原生 CI 作业确认；保留原先失败记录。
本轮修复后，本机默认套件为 **138 passed / 133 skipped，12.71 秒**，`-W error`
下零警告/错误；真实数据库安全测试仍单独执行。动态 provider 的本地冻结小程序也已在
移除开发库环境后完成实际固定向量运算。最终 Windows ARM64 与 Intel macOS 状态以新
原生作业及安装包中的实际加载路径/版本报告为准。

本机另外生成并验证了 0.3.0 macOS ARM64 DMG，56,051,780 bytes，SHA-256
`9bb4dbeae8b5c42cf74a25c2432f2e30d6679e609db0d211b8b1fb61835209a3`。
`release-assets/OpenNavicat-0.3.0-macos-arm64.manifest.json` 记录 65 个原生 ARM64
Mach-O 镜像，以及只读装载、隔离复制安装、实际 SQL 六行与全部驱动加载、移除、卸载卷。
这是本地验证资产，最终公开 Release 的资产及摘要以发布流水线为准。

## 0.3.0 的六个强制发布资产

| 系统 | amd64 | arm64 |
| --- | --- | --- |
| macOS | `OpenNavicat-0.3.0-macos-amd64.dmg` | `OpenNavicat-0.3.0-macos-arm64.dmg` |
| Windows | `OpenNavicat-0.3.0-windows-amd64-setup.exe` | `OpenNavicat-0.3.0-windows-arm64-setup.exe` |
| Linux | `OpenNavicat-0.3.0-linux-amd64.deb` | `OpenNavicat-0.3.0-linux-arm64.deb` |

Windows 使用 Inno Setup 安装器，格式为 EXE，当前没有 MSI。ARM64 安装引导器可以是
Inno Setup 的 x86 包装器，应用、Python、Qt、数据库及加密原生库必须是 ARM64；清单
分别记录两者，不能把模拟运行的 x64 应用当成 ARM64 构建。

每个原生 runner 必须完成测试、二进制架构审计、打包程序运行、真实安装后的 smoke 和
卸载验证。打包脚本检查原生库头部；发布脚本检查六个文件齐全、版本/标签、SHA-256、相应原生库清单、运行时架构、
libpq 版本，以及 installed / launched / uninstalled 三项结果；源码和 ZIP 不能替代安装包。
完整约束见 [release-policy.md](release-policy.md) 与 [packaging/README.md](../packaging/README.md)。

**最终 CI / Release 结果：待修复提交的六个原生作业和安全作业完成后补录。**
首次运行中四个已验证安装包不构成六目标发布完成，上传 CI artifact 也不等于已发布 Release。

## 运行环境与当前边界

- Linux 当前交付 Debian 格式，原生构建环境为 Ubuntu 24.04 家族；安装包 `Depends` 按
  实际构建主机写入最低 glibc，并声明 Qt 所需 OpenGL/EGL、X11/xcb、xkbcommon、DBus、
  fontconfig、freetype 等系统库以及 `fonts-noto-cjk`。使用 `.deb` 的实际依赖为准，不能将
  之前 Debian 13 本机构建或 ZIP 的环境当成当前包的兼容性承诺。Alpine/musl、旧 glibc、
  RPM 发行版尚未验收。
- Windows 当前验证目标为 Windows Server 2022 amd64 runner 和 Windows 11 ARM64
  runner；没有独立证明更低 Windows 版本的支持范围。安装采用当前用户目录，无需系统级
  安装权限。Windows ARM64 的原生 libpq/OpenSSL 由 vcpkg 构建并随包携带；源码运行需要
  可发现的同架构 libpq DLL，不能直接假设有 psycopg-binary ARM64 wheel。
- macOS 当前原生 CI 目标为 macOS 15 Intel / Apple Silicon，较低系统版本尚未独立验收。
  DMG 通过只读装载和隔离目录验证，不覆盖用户已有安装和工作区。
- 当前没有发布者签名或 Apple 公证凭据；macOS 的临时/ad-hoc 签名不等于 Developer ID
  签名或公证，Windows 也未完成 Authenticode 发布者签名。
- 自动测试验证当前实现的行为；原生 UI 的全部状态、三平台人工桌面操作、完整视觉一致性
  和 Navicat 工具功能逐项对照继续开放。Oracle、SQL Server、MongoDB，以及完整对象
  设计器、导入导出向导、结构同步、建模、自动运行、图表等能力仍以 parity matrix 的具体
  证据门槛验收，不能由本轮 TLS / SSH 或安装器测试替代。

第一版 MVP Release 发布且六个安装包可用后，按用户要求暂停继续开发。完整目标保持未完成，
等待用户更新参考端并恢复后继续。
