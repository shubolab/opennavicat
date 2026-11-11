# OpenNavicat 交接快照

最近一次交接：**[2026-11-11：已发布 0.8.1、0.9 候选预检通过、普通集合后端待集成](2026-11-11-handoff.md)**。

恢复当前 goal 时先读这份快照，再核对其中的分支、提交和 CI 当前状态。
机器可读索引见 [2026-11-11-state.json](2026-11-11-state.json)。

完整功能与 UI 的验收范围以 [parity-matrix.md](../parity-matrix.md) 为准；
快照记录某个时点的交接事实，不缩小完整 Navicat 还原目标。
原始 REA、测试、安装器与运行证据已集中到本机私有归档，main 原证据也保留；不会随 Git clone 自动恢复。

分支已收敛为 `main` 与 `work/mongodb-collections-next`；旧 ref 的提交与恢复归档见
[分支索引](2026-11-11-branches.json)。19个clean旧工作树已移除，6个dirty目录保留；测试/参考证据归档和恢复方法见[目录收敛记录](2026-11-11-worktrees.md)。
