# 0.13 单 GIF 演示准备检查点

本检查点准备实际 OpenNavicat 的录制流程。最终 GIF、帧数、时长、大小与 SHA256，须在候选源码合流、窗口关闭修复和 Oracle 窗口资格完成后，由实际录制及逐阶段视觉检查确定。

完整模式保留既有 SQLite、MongoDB、批处理作业和有限流共 31 个阶段，再追加 3 个 Oracle 连接表单阶段，共 34 个预期阶段：

| 阶段 | 实际显示 | 约束 |
| --- | --- | --- |
| `oracle_connection_general` | 新建 Oracle 连接的“常规”页 | 生成的连接名称；主机、用户名、密码为空；服务名称采用公共默认值 `ORCL` |
| `oracle_connection_advanced` | 同一表单的“高级”页 | 原始默认设置；初始查询、文件位置为空 |
| `oracle_connection_databases` | 同一表单的“数据库”页 | 自定义数据库列表未启用，表格为空 |

三个阶段均保留真实表单的“常规／高级／数据库／SSH／备注”五个页签。记录使用应用本身的 `OracleConnectionDialog` 与 `QWidget.grab()`；模态画面保持比例，居中放入纯色画布，每帧格式统一为 RGB32。原先 31 个阶段的真实数据库执行、源数据检查与隐私检查继续保留。

Oracle 部分展示连接设置界面，回执明确记录 `database_connection_attempted=false`、`database_success_claimed=false`、`profile_saved=false`。录制期间的 Oracle 会话连接入口被禁止；该期间创建的会话在表单关闭后仍保留禁止连接的实例保护。每帧检查真实字段、活动任务、测试结果、表单保存状态、配置文件列表、文本占位符与其他可见文本，发现端点、凭据或个人路径即停止。

录制参数新增 `--include-oracle-form`。以下命令供合流后的最终录制使用，输出先放在开发证据目录：

```sh
.venv/bin/python scripts/record-demo.py \
  --platform cocoa --theme light --width 1440 --height 900 --fps 6 \
  --include-mongodb --include-stream --include-oracle-form \
  --output artifacts/release-013/demo-candidate.gif
```

本阶段已完成离屏回归 `96 passed, 3 skipped`。其中新增 Oracle 表单与保护检查 17 项通过；3 个跳过项需要显式启用真实 MongoDB 集成环境。本轮未启动数据库服务。实际 Oracle 三页的 Qt 抓图与 RGB32 比例处理已通过测试，这些离屏检查不替代最终 Cocoa 全流程录制或人工视觉验收。

最终交付继续使用 README 中的一份实际应用 GIF。合流后应逐阶段检查首、中、尾帧及全尺寸画面，核对全部帧的隐私检查、34 个阶段的顺序、编码回执和生成工作区清理结果，再更新正式资产。Oracle 数据库功能资格与原版 UI 观察分别见对应验证和参考文档；表单演示不构成完整 Oracle 工具、三个操作系统或整体 Navicat 对齐验收。
