# 2026-11-14：0.13.2 小图标与结果条续作

完整 goal 保持 **active**，当前优先尽可能对齐全部图标和小型视觉元素。完整 1:1 保留 UI、
全部数据库工具和三平台实际参考门禁仍以[完整矩阵](../parity-matrix.md)为准。
本页接续[同日 v0.13.1 公开交付记录](2026-11-14-goal-resume.md)，不覆盖其历史证据。

## 当前恢复点

开发工作树为 `/Users/wangshubo/.codex/worktrees/small-controls-0132/opennavicat`，
分支 `codex_small_controls_0132`；已完成整合资格的源码为
`6af4335232e9cfadb5f9c3668490fc22c86da9e2`，版本元数据 0.13.2。
当前公开版本仍为 **v0.13.1 预发布**，固定 b926 tag 不移动。0.13.2 尚未打 tag 或发布。
机器索引见[本次状态](2026-11-14-small-controls-state.json)。

新实现包括成功圈勾、复制、细叉、Oracle 裸加减/刷新、SQLite 实际 Test 结果条及配置栏头、
成功连线和原生按钮顺序、MongoDB 条件加减与详情关闭。均为独立 Qt 绘制，保留各自回调、
草稿和禁用边界，不引入参考图像。参考继续为 18.0.5 build321；三条新控件 Evidence ID 及
复制未点、亮色未观察等边界见[0.13.2 验证](../validation-0.13.2.md)。

## 本机资格与演示

- exact 6af：默认 **2874 通过 / 662 条件跳过**，3536 collect/run/JUnit；实际 Cocoa
  17 模块 **494 通过 / 3 条件跳过**，497 项。250 个输入前后与 Git 一致，默认 native
  helper 摘要稳定，隔离工作区清理通过。子进程刻意 offscreen 的检查不混称 Cocoa。
- 7ab 原生 arm64 包实际 Cocoa smoke 6 行×4 列、17 探针通过。6af 对同一个既有包只读
  静态复核 24 项、80 自有模块及入口匹配；不是重新构建或再次运行该包。
- 59f 新的实际 Cocoa 36 阶段 GIF：492 帧、82 秒、1,923,259 字节，SHA256
  `d0bcdd75f7cb27d65003c2b84ea8bf20677b734b99fdeac08b1cd9c5e9982e1b`。250 输入前后
  与 Git 一致；与 6af 的差异只有两项 Oracle 测试输入，应用和录制脚本相同。已全帧独立
  解码；成功圆点首中尾和 11 卡片共 33 图标区域均精确保留本应用源色。新容器与两项卷的
  exact ID 生命周期/不存在独立核对通过；临时应用路径清理仍仅 producer 断言。
- 全 36 阶段全尺寸代表帧和首中尾联系表已实际审查通过，未见隐私或参考图混入；不冒称
  492 帧全人工查看/OCR。README 唯一 GIF 与原 JSON 已逐字节替换为本次新录制，明确标注
  0.13.2 开发版，公开安装器仍为 v0.13.1。窄文字省略/滚动及完整 UI 门禁仍开放。

## CI 失败与继续步骤

7ab main [43551247449](https://github.com/shubolab/opennavicat/actions/runs/43551247449)
终态失败：九必需任务六过、三失败，两 Windows 任务在 PATH 测试，Oracle ARM 服务以 24
退出；仅 13/15 artifacts，缺两 Windows 安装器，publisher skipped，未取得六安装器资格。
Windows 测试改在 Qt 自身 DLL 路径引导后取基线，恢复和相对工具检查均保留；须新 Windows
CI 验证。Oracle 增加已退出检测、5 秒/64 KiB 内存诊断、允许代码摘要和独立进程组回收。
本机 e16 候选 ARM 后端 14 项通过未复现旧退出，不解释旧 CI 原因。

新整合源本机检查已通过；下一次 main 推送后核实新 run，必须重新取得九必需任务、15 产物、
六实际安装器及摘要，才可打 v0.13.2 正式 tag；tag 流水线、匿名八资产下载和公开 ARM 应用
smoke 再分别验收。不得复用旧 run 的安装器字节，不取消、重写旧失败 tag 或 run。

7ec 的默认 16 项/Cocoa 19 项失败、7ab 演示圆点偏色、首轮不含 Mongo 的 recorder receipt
失败和 Oracle 日志 collector 的真实后代清理失败均保留，后续通过不回填旧记录。

下一视觉片段仍包括 SQLite 数据库页三列与裸控件、完整表单比例、配置栏其它模式、
全部小元素/主题/禁用/悬浮/焦点，以及 Windows/Linux 实际参考；U03/U08/U15/C18 不关闭。
不在里程碑后暂停，不访问用户真实连接、生产数据库或覆盖其正在运行的应用。
