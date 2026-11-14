# 2026-11-14：图标与小控件通过本机和六目标预检

完整 goal 保持 **active**。本轮用户重点是尽可能对齐全部图标与小型视觉元素；完整工具、
1:1保留UI及Windows/Linux/macOS实际参考继续由[完整矩阵](../parity-matrix.md)追踪。
源码测试、本机包、安装器、演示与参考人工验收是不同门禁。

## 当前恢复点

应用源码冻结为 `bb4d46964d3568d1dc2f6743f92050e7a43519d9`，版本 **0.13.1候选**，已推
main及`codex_oracle_basic_integration`。工作目录
`/Users/wangshubo/.codex/worktrees/oracle-basic-integration/opennavicat`；后续文档/GIF提交须
明确243声明源与bb4逐字节一致，不自指未来HEAD。机器索引见[当前JSON](2026-11-14-state.json)。

main [43524907305](https://github.com/shubolab/opennavicat/actions/runs/43524907305)已完成：
**九必需job成功，publisherSkipped；15artifact/六安装器实际字节审查通过**。原600秒下载
transport失败保留，13目录不变，同run/ID补齐两ZIP并核API digest/size/CRC后完整审计通过。
两Mac只读提取挂载均已detach。具体六文件摘要和平台动态测试清单见
[0.13.1验证](../validation-0.13.1.md)。这是main预检，不能把其摘要借给正式tag或公开下载。

**当前仍未创建v0.13.1 tag，未发布0.13.1安装器。** 最新公开为
[v0.12.0预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.12.0)，exact
`b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`。下一步创建新正常提交的tag候选，重新取得正式
十job和公开下载资格；不改旧v0.13.0或其它已发布tag。

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

1. 提交本次文档及精确已审GIF；证明全部243源等同bb4后，创建全新v0.13.1 tag。
2. 正式十job成功后另核15artifact/六实际字节、checksums/manifest及匿名八公开资产。
3. 本机对公开ARM64 DMG隔离Cocoa smoke、清理后准确更新版本/快照；只快进干净用户checkout。
4. 继续全部小元素、主题/失活/焦点/悬浮、向导及Windows/Linux实际参考；完整Oracle Query、
   事务/DDL、类型/认证/安全/写入、serverSLEEP及时取消等功能门禁不缩小，不在里程碑后暂停。

不清理未合并或带改动工作树，不导入真实连接或操作用户数据库来验收。
