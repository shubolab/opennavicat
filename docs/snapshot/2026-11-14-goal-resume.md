# 2026-11-14：v0.13.1 预发布、匿名字节与公开 ARM 应用 smoke 通过

完整 goal 保持 **active**。本轮用户重点是尽可能对齐全部图标与小型视觉元素；完整工具、
1:1保留UI及Windows/Linux/macOS实际参考继续由[完整矩阵](../parity-matrix.md)追踪。
源码测试、本机包、安装器、演示与参考人工验收是不同门禁。

## 当前恢复点

正式 `v0.13.1` 固定在 `b9261016de530dda0311683223a1862efdb05d26`，已公开为
**预发布**：[下载页](https://github.com/shubolab/opennavicat/releases/tag/v0.13.1)。
发布时间为 2026-11-14 00:18:33 UTC（北京时间 08:18:33），draft=false、prerelease=true。
此前本机完整资格和 GIF 的记录源仍为 `bb4d46964d3568d1dc2f6743f92050e7a43519d9`，
下文按实际来源保留，不改称重新在正式 tag 上执行过该全源/Cocoa测试集合。工作目录
`/Users/wangshubo/.codex/worktrees/oracle-basic-integration/opennavicat`；机器索引见
[当前JSON](2026-11-14-state.json)。
交付文档检查点 `26bb917dd63a1323aec942671bfe4e12e5180c75` 已推到 main 与候选分支，
两条远端 ref 另行核对一致，固定 b926 tag 未移动。主目录及用户 release-0-10 工作树均在
干净状态从 d9 仅快进到该点；两个物理 Python 3.13.11 环境已按 frozen/offline/inexact 同步
至 0.13.1，原 cmake/ninja 及用户树 imageio-ffmpeg 保留，没有删除额外发行包或改全局配置。
本快照是其后的纯文档交接，不重定义 bb4 本机资格、b926 正式源码和已发布安装器。

正式 [43531823280](https://github.com/shubolab/opennavicat/actions/runs/43531823280)
在 exact b926 上 **十项全部成功，包含 publisher**；独立审查 15 artifact、六安装器实际
字节及 native／installed 回执通过。严格匿名下载八公开资产（六安装器、SHA256SUMS、
build-manifests.json）已通过：curl 配置禁用、无 Authorization 或认证 fallback，八项均
HTTP 200，API size/digest、正式六份 bytes/hash、完整 manifest 和公开 tag/source 一致。
正式六文件摘要与 main 预检六文件均不同，未借用 main 字节。回执分别为
`artifacts/release-0131/tag-audit-43531823280/verification.json` 与
`artifacts/release-0131/public-43531823280-r2/{verification,delivery-handoff}.json`。

**公开 macOS ARM64 DMG 的完整打包应用 `--smoke-test` 已在 fresh-r2 通过。**
这是实际启动隔离副本中的应用，执行生成 SQLite 的有界异步路径，结果为 6 行×4 列，
Oracle 26.1.0 四项及其它十三项探针均 true；不是仅启动 JS helper。原生 ARM64、版本
0.13.1、79 自有模块和入口匹配 b926；ditto 副本 404 文件、103 符号链接清单相同。
root 已实际查看全尺寸截图。显式请求 Cocoa，但打包应用未自报实际 QPA；该路径不等于
完整 GUI／参考／工具功能验收，Oracle 四项也不是 Oracle 数据库连接。
应用正常退出，只读 disk4 已 detach，副本和临时目录不存在，全部记录 PID/PGID 已消失，
独立清理核对通过。回执 `public-arm-cocoa-43531823280-r2/verification.json` 的 SHA256 为
`b61d62ab848b78ea2331f1c163a4627539e6c8653e5b4a7b7c61a56414cb0fed`；
同目录 `root-visual-cleanup-review.json` 保留目视及独立清理。
公开 ARM64 文件 SHA256 为
`8aeb2fd1d3f6b506d82a55f8d232be045a974e7bac6ccfa6842e5be904ee9739`，52,689,861 字节。

main [43524907305](https://github.com/shubolab/opennavicat/actions/runs/43524907305)已完成：
**九必需job成功，publisherSkipped；15artifact/六安装器实际字节审查通过**。原600秒下载
transport失败保留，13目录不变，同run/ID补齐两ZIP并核API digest/size/CRC后完整审计通过。
两Mac只读提取挂载均已detach。具体六文件摘要和平台动态测试清单见
[0.13.1验证](../validation-0.13.1.md)。这是main预检，不能把其摘要借给正式tag或公开下载。

此前公开的 [v0.12.0预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.12.0)，
exact `b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`，保留为历史，不再是当前最新公开版本。
旧 v0.13.0 失败 tag 及其它既有 tag 未移动。

## 本轮实现及实际观察

主栏、对象、九驱动、供应商和常用动作使用独立几何绘制：主栏28、树/页签16、查询选择器
14、连接网格50/列表筛选16逻辑像素；连接、开关、禁用与DPI分开处理。页签灰叉保留平台
侧别、保存/执行保护，并销毁被替换按钮。未实现的工具没有因换图标而启用。

参考18.0.5 build321实际确认Oracle常规端口为纯文本框，无stepper且Up不改1521；profile
为蓝/灰pane、灰圈加号。实现保留非法草稿和TNS隐藏原值，Basic连接前严格校验；SSH原
stepper不变。表单按自然标签宽度居中，解决300px字段被祖先裁右边的问题。通用页复用
profile图形，不冒称其它driver参考页已经验收。非法值参考交互、大字/滚动仍开放。

原版两次搜索证实Atlas是橙Mongo叶、Enterprise Cloud是粉Redis R，供应商区为空；另一次
空搜索完整画面为12供应商筛选。此前14供应商误把两直接别名混入，现更正分类但保留14组
目录全部别名。[图标记录](../validation-icons-navicat18.md)保留EIDs和静态/运行边界。
原版证据未进入Git/包，临时SQLite文件已清理，全连接Off，正常Quit且REA export/close。

另修复整个“其它/用户”按钮刷新空白：原每次remove/insert重建QToolButton，8/8立即隐藏、
事件后才显示。按需幂等重排后，最终同字节test旧产品16红→新相关39绿；各driver原顺序、
可见/禁用和回调保留。新真实Oracle全部主窗口图中按钮均在，旧缺块不再出现。

## bb4本机资格

- 全源2725通过/662条件跳过，3387 collect/run/JUnit精确，零失败/错误，107.837秒。
  warnings=error；243源和原生helper前后/Git相同。跳过不算实际数据库执行。
- 实际Cocoa十一模块334通过/3既有真库条件跳过，337精确；243源相同。生命周期子pytest
  特意offscreen，未混称Cocoa；style子进程请求父平台，不修改父style/palette/font。
- ARM64 bundle及隔离smoke通过，6×4样例，Oracle26.1四项加其它13项探针true。Cocoa是显式
  请求，smoke不自报QPA；实际Cocoa由独立窗口scope报告。包内24静态检查、79模块/入口、
  61MachO、4Oracle机器码、32driverPy、55wheel payload/许可证和95链接通过。
- fresh owned Oracle实际Cocoa3/0：Test/保存重开/临时密码/schema/表/视图/Decimal，计算
  视图Stop/同会话复用/Disconnect与Shutdown。独立106结构/资源检查，runner24源前后匹配；
  28phase PNG/15unique均实际全尺寸看完。图片不冒称证明Stop时序、清理或完整参考。
- `combined-bb4-source-verification.json`独立比较default/native/Cocoa/实际录制各243源和Git
  一致，并绑定Oracle24输入；不把24改称runner已测243。当前源不沿用ca4旧数字。

## README真实GIF

同一份GIF已替换为bb4真实Cocoa：35阶段、471帧、78.5秒、1833002字节，SHA256
`8954f63340dba160cebe81f175dd575a2306c619a41389cc9000a7df27204587`。
生成SQLite/MongoDB、空Oracle表单和空选11卡网格；Oracle表单未保存/连接，不冒称DB功能。

全471帧text guard和独立decode通过；35个fullsize中帧、35首/中/尾联系表均实际目视，
无隐私/缺glyph/偏色阻断。11卡首中尾33实际区域主色误差为零；未声称471帧全人工看或
独立OCR。固定完整帧palette保留九主色/透明/黑白，只调色板不添画面，公开GIF+回执精确
等同已审字节。精确owned CID lifecycle/destroy/不存在独立核对；卷ID/temp路径仍只producer
断言。私有wrapper旧limits字符串写242，原回执保留并annotation，实际控制清单/计数为243。

## 保留失败历史与继续顺序

旧v0.13.0固定81523aa，正式43487011620因macOS Intel fixture teardown失败、未发布。
真实framework DeferredDelete红绿、原SQLite allocation原因未知分别保留；原cleanup移入
before_close，不靠invalidguard。新toolbar测试fixture也取得同样真实销毁的独立红绿。

ca4 main43511903905六项失败：旧96行高断言、Windows逐子控件setStyle、MacIntel子进程
OpenSSL导入和OracleARM服务准备。现保留原文字/50px图标、隔离Qt公共style全factory并
恢复真正hook测试环境；新增有限Oracle启动phase/reason/code/ownedState，不抬360秒预算。
原ARM原因未知，新run成功不回填因果。ca4首轮35phase GIF的Oracle偏棕失败也保留，未发布；
其精确中间发生点未知，本次新录制单独通过颜色核对。

公开下载首轮 `public-43531823280` 因匿名 GitHub API core 配额耗尽而 HTTP 403，
当时尚未请求任何 asset（assets_requested=0），只是元数据阶段失败，不能算匿名字节验收。
保留原目录及 rate-limit 诊断；配额重置后 r2 才取得上述八项实际匿名下载成功。

公开 ARM 应用首轮 `public-arm-cocoa-43531823280` 在复制／应用启动之前失败：hdiutil
返回有效 XML，但与 deprecation stderr 合并后使私有 plist 解析器报 ExpatError。
这次没有启动打包应用，原记录保留；disk4 detach、临时目录与全部进程组清理均通过。
私有解析器修正经 42 项纯测试通过，再以 fresh-r2 的独立实际应用运行取得上述资格。

## 冻结后新观察的下一控件片段（尚未实现）

[图标记录](../validation-icons-navicat18.md#正式冻结后的剩余控件实际观察)新增三条实际
18.0.5 证据，均发生在 v0.13.1 冻结后，不属于已发布包的实现资格：SQLite Test 成功条的
绿圈白勾、蓝双纸复制与细叉；SQLite 数据库页三列及裸加减；Oracle 未启用自定义数据库
列表的裸加减与顺时针刷新。复制未点击、失败外观未观察，不能外推剪贴板或 Oracle 结果
交互；pane 初始 On/Off 可能是保留状态，不能据此更改 driver 默认值。U08/U15/C18 保持开放。

1. 先按上述实际场景实现结果条及裸加减/复制/关闭/刷新等小元素，保留回调、禁用边界，
   再做真实尺寸、DPI、主题与裁剪验收，不复用错误的圈加号语义。
2. 继续全部小元素、主题/失活/焦点/悬浮、向导及Windows/Linux实际参考；完整Oracle Query、
   事务/DDL、类型/认证/安全/写入、serverSLEEP及时取消等功能门禁不缩小，不在里程碑后暂停。

不清理未合并或带改动工作树，不导入真实连接或操作用户数据库来验收。
