# 10 小时恢复工作记录

用户于 2026-11-14 再次要求继续，给出 10 小时预算，并要求到时无论进展如何都收尾更新 snapshot。
本轮开始时间为 **2026-11-14 14:43:06 UTC / 北京时间 22:43:06**；硬截止为
**2026-11-15 00:43:06 UTC / 北京时间 08:43:06**。此前因额度不足的停止要求已被本次继续取代。

完整目标和开放门禁仍见 [parity-matrix](../parity-matrix.md)。上一轮交接见
[小控件 snapshot](2026-11-14-small-controls.md)，其中 CI pending 状态需以下述新观察为准。

## 恢复时核对

- 当前远端主线 `adf9221ecac6b51d27ccc56cea9acc6d4d8cc819`；工作目录为本机
  `/Users/wangshubo/.codex/worktrees/sqlite-databases/opennavicat`，本地分支跟踪 `origin/main`。
- main CI [43603141977](https://github.com/shubolab/opennavicat/actions/runs/43603141977)
  已完成失败：六个桌面任务及数据库 TLS/SSH 任务成功；Oracle amd64、arm64 均在启动 fixture
  阶段失败，backend 14 项只完成收集。没有据此创建 tag 或发布 v0.13.3。
- 两个 Windows 目标的上一轮字体断言修正已在该次实际 CI 中通过。六目标原生 SQLite UI
  scoped 门禁与安装器构建/安装 smoke 任务成功；尚未审查这次全部六安装器字节。
- 只下载九份小型 validation ZIP 并核对传输身份；未据 Oracle 失败尝试发布。
- origin 仍只保留 main 和有未提交工作的 `codex_mongodb_finite_stream`；本轮并行分支留本地。

## 正在推进

- 定位两个 Oracle fixture 退出 24 / ORA-04021 / ORA-65048 的实际阶段；保留原失败，不加盲目重试。
- 修复现有画面中确定复现的 inspector 标题及 pipeline 源节点裁字问题；完整 U15 继续开放。
- 参考 Navicat 18.0.5 build321 的自有 SQLite 多附件与无效配置规则，保留运行 Evidence ID。
- 完整默认测试增加独立 collection、run collection、JUnit 对应及源码身份记录，避免只凭 JUnit
  或部分静态 membership 声称完整集合已验收。

本文件是恢复中的工作记录，最终截止 snapshot 将补充提交、测试、CI、发布和剩余项；
不能将正在推进的项目当作通过证据。
