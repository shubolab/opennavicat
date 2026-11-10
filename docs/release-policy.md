# 里程碑发布约定

用户于 2026-11-10 指定远程仓库 `git@github.com:shubolab/opennavicat.git`，
要求开发过程中及时提交、推送，并在每个可发布使用的里程碑发布六种原生安装包。
这些要求与完整 Navicat 功能/UI 的最终目标同时有效。

| 系统 | amd64 | arm64 |
| --- | --- | --- |
| macOS | `OpenNavicat-<version>-macos-amd64.dmg` | `OpenNavicat-<version>-macos-arm64.dmg` |
| Windows | `OpenNavicat-<version>-windows-amd64-setup.exe` | `OpenNavicat-<version>-windows-arm64-setup.exe` |
| Linux | `OpenNavicat-<version>-linux-amd64.deb` | `OpenNavicat-<version>-linux-arm64.deb` |

源码和 ZIP 不能替代安装包。Windows arm64 安装引导器可使用 Inno Setup 支持的
x86 引导器，但安装的应用、Python、Qt、驱动和本地库必须为原生 arm64，不能使用
x64 模拟版应用充数。manifest 分别记录引导器和应用架构。

发布前在各自原生主机运行测试、打包后 smoke、安装后的实际应用 smoke 和卸载检查。
安装包清单记录版本、平台、架构、SHA-256、原生二进制清单和安装验证结果。
macOS 使用只读 DMG 装载、隔离复制安装、运行并移除；Linux 验证真实包管理器安装/卸载；
Windows 验证安装器/卸载器及独立验证安装路径。验证不会覆盖用户已有安装或工作区。

六种安装包、六份通过的 manifest、真实数据库 TLS/SSH 测试及校验和齐备后，才公开
对应 Release。当前阶段发布说明必须准确列出已实现能力与剩余差距，不得把可用的早期
里程碑表述为完整 Navicat 还原。所有签名、公证和平台验收状态单独记录。

发布入口为 `v<version>` tag。`.github/workflows/build.yml` 在全部构建门禁通过后
调用 `scripts/publish-release.py`；脚本先核对完整资产集合，再创建草稿、上传并公开。
已公开版本不自动覆盖；修复后使用新的版本/tag。普通源码提交和 CI 不自动代表一次 Release。
