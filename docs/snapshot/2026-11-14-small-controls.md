# 2026-11-14：0.13.2 小图标与结果条续作

完整 goal 保持 **active**，当前优先尽可能对齐全部图标和小型视觉元素。完整 1:1 保留 UI、
全部数据库工具和三平台实际参考门禁仍以[完整矩阵](../parity-matrix.md)为准。
本页接续[同日 v0.13.1 公开交付记录](2026-11-14-goal-resume.md)，不覆盖其历史证据。

## 当前恢复点

开发工作树为 `/Users/wangshubo/.codex/worktrees/small-controls-0132/opennavicat`，
分支 `codex_small_controls_0132`；已完成整合资格的源码为
`6af4335232e9cfadb5f9c3668490fc22c86da9e2`，版本元数据 0.13.2。
当前公开版本为 **[v0.13.2 预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.13.2)**，
固定源码12be；历史v0.13.1 b926及其它tag均未移动。
源码、文档和已审 GIF 检查点 `12be93d83116956beab1982c9a02bc3dedb19e77` 已推 main，
主目录与用户 release-0-10 工作树均仅快进到该点，物理环境同步至 0.13.2，额外包版本保留。
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
  0.13.2，公开安装器亦为本次 v0.13.2。窄文字省略/滚动及完整 UI 门禁仍开放。

## CI 失败与继续步骤

7ab main [43551247449](https://github.com/shubolab/opennavicat/actions/runs/43551247449)
终态失败：九必需任务六过、三失败，两 Windows 任务在 PATH 测试，Oracle ARM 服务以 24
退出；仅 13/15 artifacts，缺两 Windows 安装器，publisher skipped，未取得六安装器资格。
Windows 测试改在 Qt 自身 DLL 路径引导后取基线，恢复和相对工具检查均保留；须新 Windows
CI 验证。Oracle 增加已退出检测、5 秒/64 KiB 内存诊断、允许代码摘要和独立进程组回收。
本机 e16 候选 ARM 后端 14 项通过未复现旧退出，不解释旧 CI 原因。

main [43555639933](https://github.com/shubolab/opennavicat/actions/runs/43555639933)
已完成九必需任务成功/publisher skipped，15 ZIP 与六实际安装器、sidecar、manifest、
架构及安装/启动/卸载逐项审核通过。正式 `v0.13.2` 固定 exact12be，
[tag43558286471](https://github.com/shubolab/opennavicat/actions/runs/43558286471)
十任务全部成功含publisher；正式15 ZIP重新下载校验，六文件摘要全部不同main，未复用。
十二份frozen/installed QPA匹配xcb/cocoa/windows；Oracle两架构各14+3无跳过，scope分开。

2026-11-14 04:59:53 UTC公开为预发布，release445027340，八资产均匿名下载HTTP200，
API size/digest、正式字节、SHA256SUMS与完整manifest相同。公开ARM DMG另在本机实际
运行完整应用，Cocoa/arm64/0.13.2，6行×4列与17本地探针；80自有模块/入口匹配12be、
ditto404文件103链接清单相同。root已全尺寸目视；精确设备卸载、临时目录/副本及十所录
PID/PGID清理复核通过。这是有限运行和交付资格，不是全部UI/工具或三平台参考通过。

本机完整default集合见前文；CI default只上传JUnit，未提供完整collectmanifest。七模块
310项静态membership补充不等于全CI集合控制。旧Windows PATH与Oracle ARM exit24失败
不回填因果，原记录保留。正式/匿名/ARM详见[验证记录](../validation-0.13.2.md)。

7ec 的默认 16 项/Cocoa 19 项失败、7ab 演示圆点偏色、首轮不含 Mongo 的 recorder receipt
失败和 Oracle 日志 collector 的真实后代清理失败均保留，后续通过不回填旧记录。

下一视觉片段仍包括 SQLite 数据库页三列与裸控件、完整表单比例、配置栏其它模式、
全部小元素/主题/禁用/悬浮/焦点，以及 Windows/Linux 实际参考；U03/U08/U15/C18 不关闭。
不在里程碑后暂停，不访问用户真实连接、生产数据库或覆盖其正在运行的应用。

## 按用户要求清理远端分支

用户指出远端临时分支堆积，已据此补充 AGENTS：并行任务默认留在本地，仅需要远端评审/
交接才推分支，合入 main 后及时删远端。main 快照与提交承担进展检查点，不积累远端分支。

本次从 44 个远端分支清理 42 个。先删 38 个已祖先合入或补丁等价且无 dirty/unpushed
工作者；另四个图标叶虽部分中间提交 patch 不同，所有独有提交触及路径的最终完整 Git
mode/type/blob/name 清单都与可达 main 的祖先严格相同，逐项重新核对后删除。两轮均
atomic push 加每个 ref 的 exact SHA lease，之后直接 ls-remote 复查；main 和 tags 未变。

origin 现在仅保留 `main` 与关联工作树有未提交改动的 `codex_mongodb_finite_stream`。
保留本地分支、工作树、私有运行证据与资产，未猜测性清理 dirty 工作。后续进展只推 main。

## 下一本地开发树（未发布）

`/Users/wangshubo/.codex/worktrees/sqlite-databases/opennavicat`，本地分支
`codex_sqlite_databases`，物理Python3.13环境/版本0.13.3。未推远端任务分支，未冒称六目标
资格。已接AI图标语义、稳定附加库元数据与process-only密码来源、普通ATTACH原子会话
发布、取消代次、比较实际namespace/配置失效、worker捕获备份source、新SQLite保存Off。

整合首次八模块155通过，无跳过；随后14模块378通过，源码8005162，只算本地定向集合。保留并已修的真实失败包括：
旧hydrated凭据跨file/flag复用、helper返回与publication之间取消丢失、旧比较写到换绑文件、
选择aux却备份main、worker关闭期间GUI读取事务状态。各自红绿证据不合计成全源通过。
数据库页/新增编辑sheet已整合ede+f223：旧版长路径换行、密码行左沿和纯QFont/行高的
8项同字节测试先红后绿；独立复核单行、12/36px自然行高、密码缩进及销毁后无定时回调。
固定8005162的实际Cocoa集合已经结束：720项中711通过、6失败、3个既有数据库条件
跳过、0error；源码260输入前后与Git一致，隔离数据及进程清理通过。两项真实录制失败
为后台Store快照缺新缓存，已补独立副本，同字节2红→2绿；不是Cocoa样式故障。另四项
暴露真实QMacStyle输入框内容区仍21px、大字体裁切，新增7同字节native测试旧源全红、
后续修复全绿；修复叶Cocoa126通过、离屏230通过、两个缩放档各22通过。ROOT已查看
三张原始组件图并独立核对捕获PID不存在，不能算最终整合或全平台通过。
最新主线40e文档已合入本地整合，recorder修复与native字体修复已接入；完整整合资格
仍待最后源码冻结。后续必须补字体/状态、完整默认集合、原生包、真实演示与六目标
安装器；完整goal不在v0.13.2后暂停，详见[0.13.3开发记录](../validation-0.13.3.md)。

参考另有五个plain ATTACH实连/同进程保存重开检查点，见[数据库页合同](../sqlite-connection-databases.md)。
新profile正常删除后留下独占缓存目录，有限metadata核实后精确清理；输入失败与首轮
cleanup失败均保留。真实加密/Cipher/进程重启/invalid规则/三平台仍开放。
