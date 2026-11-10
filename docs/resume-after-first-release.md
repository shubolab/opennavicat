# 首个可用版本交付后的恢复说明

状态更新：用户已于 **2026-11-10 明确恢复目标**，指定参考为
`/Users/wangshubo/Downloads/Navicat Premium.app`，实际确认 **18.0.5 build 321**。
新版的 [运行观察](ui-reference-18-observations.md) 和 [静态调查](reference-18-static.md)
已重新建立。下文暂停交接保留为历史，不能据此再次暂停当前工作。

用户要求在首个最小可用 Release 发布且六份安装包实际可下载后暂停目标，
准备把本机 Navicat 更新为最新版。v0.3.0 是早期可用里程碑；完整 Navicat
数据库工具功能、1:1 UI 和三平台逐项对齐目标保持未完成。

已交付版本：[v0.3.0](https://github.com/shubolab/opennavicat/releases/tag/v0.3.0)。
源码标签指向 `b73eb02a3f0e961e743b8a6882e6d3f647910ff9`，
[发布流水线 42753518840](https://github.com/shubolab/opennavicat/actions/runs/42753518840)
包括六个原生目标、安全连接测试和发布作业。安装包及其清单保持版本不可变；
后续文档提交不重新替换 v0.3.0 安装包。具体证据见 [验证记录](validation-0.3.md)。

当前参考为 Navicat Premium 16.1.1 build 51，只代表本轮实际调查的版本。
收到用户明确恢复指示后，按以下顺序继续：

1. 检查本机实际安装的 Navicat 版本、build 和架构，重新读取
   `reverse-engineer-anything` skill 的适用规则，用 REA 调查新版发布产物。
2. 实际打开新版 Navicat，使用本任务创建的 SQLite/一次性数据库夹具，
   逐状态观察主窗口、菜单、连接、表数据、查询、设计器和向导。
   不导入既有连接配置、密码、私钥或工作区，也不连接用户的生产数据库。
3. 将新版观察与历史 [运行记录](ui-reference-observations.md) 分开归档，
   修订 [完整对齐矩阵](parity-matrix.md)，保留所有尚未观察或未通过的功能。
4. 继续完整界面和功能还原。排除产品账号、产品登录/注册、订阅、激活及云账号协作；
   数据库认证、数据库用户/角色/权限和云数据库连接继续保留。
5. 及时提交并推送到 `git@github.com:shubolab/opennavicat.git`。
   每个后续可发布使用的里程碑仍须提供 Windows、Linux、macOS 各自的 amd64/arm64
   六份实际安装包，并验证安装、运行、卸载和公开下载。

安装包验证和当前功能测试通过不代表 1:1 还原通过。恢复前不启动下一轮功能开发，
也不把这个暂停状态标记为完整目标完成。
