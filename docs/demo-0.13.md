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

## 0.13.1 连接选择器录制准备

新增可选参数 `--include-connection-chooser`，在既有流程最后追加一个 `connection_chooser` 阶段。未指定该参数时，默认 6 阶段、MongoDB 有限流 31 阶段和追加 Oracle 表单后的 34 阶段均保持原顺序；全部启用时预期为 35 阶段。

新阶段直接显示应用的 `ConnectionManagerDialog` 并抓取其真实画面。使用空搜索的“全部连接类型”网格，图标为 50×50 逻辑像素；校验九个数据库类型存在，回执记录实际显示的卡片名称及已观察的直接别名。当前基线显示九类数据库，合流后的 MongoDB Atlas 与 Redis Enterprise Cloud 别名由应用本身提供；录制器不会添加卡片或替换图标。最终 11 张卡片须在合流后的实际 Cocoa 录制中另行核对。

每帧检查卡片未选中、“下一步”禁用、没有设置页交接、URI 子窗口或保存行为，并继续检查真实标题、搜索框、侧栏和卡片文本中的路径、端点与凭据。该对话框不绑定主窗口的新建连接回调，不读取已有 Store 或凭据；结束和异常路径均拒绝对话框并安排 Qt 销毁。画面沿用真实模态表单的等比例居中纯色留边，不合成主窗口背景或控件。

新增参数同样要求显式 `--output`。后续合流录制命令为：

```sh
.venv/bin/python scripts/record-demo.py \
  --platform cocoa --theme light --width 1440 --height 900 --fps 6 \
  --include-mongodb --include-stream --include-oracle-form \
  --include-connection-chooser \
  --output artifacts/release-0131/demo-candidate.gif
```

本次准备不录制 GIF、不启动数据库服务，也不改公开资产。离屏回归验证实际选择器抓图、无交接状态、可见文本保护及正常／异常后的销毁；这些检查不替代合流后的 35 阶段 Cocoa 录制、逐阶段视觉审查或三平台验收。

## GIF 图标颜色修正

源码 `ca4d20a` 的首轮实际 35 阶段 GIF 已完成逐阶段隐私审查，但连接选择器中的 Oracle 红色变为棕红色，不能作为视觉通过的展示。相同源码的原始 Cocoa PNG 保持鲜红、青蓝和粉色；两次独立抓图不足以定位首轮录制的每一步，旧 GIF 继续保留为失败证据。

编码改为统计完整帧的全局 palette，并在该 16×16 palette 中保留本应用独立绘制的九个数据库主色。已有精确色保留原 entry，缺少的颜色先复用重复 entry，再选择最接近的不同 entry；透明 slot 和黑白保留。该操作只调整 GIF 调色板，所有画面仍来自实际应用抓图，不添加色板、图标或控件。QImage 的文件/像素处理在录制器的 I/O worker 执行，不读取 QWidget、QPixmap 或现有工作区。公开回执新增 `gif_palette`，记录实际使用的策略及非秘密主色。

对照同时保留原全局 diff、全局 full、逐帧 single/new 与保色全局 full。逐帧方案有较大的体积代价；把逐帧 palette 与 rectangle 复用叠加还会使未改变区域使用旧索引。当前选择保色全局 full，继续使用固定 palette 的 rectangle 优化。FFmpeg 的 [palettegen](https://www.ffmpeg.org/ffmpeg-filters.html#palettegen) 与 [paletteuse](https://www.ffmpeg.org/ffmpeg-filters.html#paletteuse) 文档说明统计方式及逐帧 palette 选项，实际颜色、文字、帧序和时间仍以解码测试为准。

新增回归使用本应用实际 16px 图标与生成的多色画面，检查晚出现的静态图标、局部变化时未改变的图标、文字前景、回到前一画面的顺序和实际 GIF 帧数/时长；另检查 worker 中调色板读写、透明 slot、少色 padding 与无效输入不改文件。原始应用 PNG 和已审样例只用于忽略目录下的离线诊断，诊断重编码不是新的实际应用演示。合流冻结新源码后，须重新实际录制全部 35 阶段并逐阶段审查，再替换 README 同一份 GIF；以上测试不代替该步骤，也不构成完整视觉或三平台验收。
