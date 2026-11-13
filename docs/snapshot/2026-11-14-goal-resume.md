# 2026-11-14：图标与小控件合流，0.13.1 继续资格

完整 goal 保持 **active**。当前用户重点是尽可能对齐全部图标和小型视觉元素；完整工具、
1:1 保留 UI 和 Windows/Linux/macOS 实际参考门禁继续由 [完整矩阵](../parity-matrix.md)追踪。
源码测试、应用包、安装器、演示和参考人工验收分别记录，不将其中一项当作产品完成。

## 当前恢复点

应用源码 `ca4d20a858c964d18eff53e97664e4b32d403cc9`、版本 **0.13.1 候选**已推 main 与
`codex_oracle_basic_integration`。本地另有 `edbb58c` / `cb62a39` 两个选择器测试修正，尚未
推送；其它隔离修正正在合流。工作目录为
`/Users/wangshubo/.codex/worktrees/oracle-basic-integration/opennavicat`。

**最新 main [run43511903905](https://github.com/shubolab/opennavicat/actions/runs/43511903905)
失败，尚不能发布。** 三个必需 job 成功、六个失败、publisher skipped；没有把诊断 artifact
或成功 job 当作完整安装器资格。失败范围与修正分别是：

- Linux 两架构和 Windows ARM64 的选择器测试把行高写死96；两个直接别名在平台字体下
  需要113或108。产品本来会增高以容纳文字；本地修正检查所有实际标签和50px图标可容纳，
  并覆盖系统默认/等宽字体。没有压缩字或裁掉别名来迎合旧断言。
- Windows AMD64 在小控件测试逐个给内部 QWidget 设置 QStyle 时访问冲突；改用由 Qt
  拥有的公共样式传播，并完整恢复测试状态。新 Windows 结果仍须实际 CI 验证。
- macOS Intel 的生命周期回归子进程在导入阶段遇到 OpenSSL legacy-provider warning，
  被原有 `-Werror` 拒绝；原 designer 用例尚未开始。已独立证明既有 packaging hook 测试
  在初始没有 OPENSSL_MODULES 时会泄漏临时路径，正修复环境恢复。不能据此提前宣布 Intel
  问题全部解决，也不设置 NO_LEGACY 或屏蔽警告。
- 原生 Linux Oracle ARM64 在 fixture_start 阶段失败，14用例已收集但未执行；Window专项
  也未开始。已清除精确 owned CID/tmpfs且无数据卷。旧回执没有保留安全原因，readiness
  超时只能推断；续作增加有限阶段/reason/code/State诊断，不抬360秒预算盲重跑。

最新公开安装器仍是 [v0.12.0 预发布](https://github.com/shubolab/opennavicat/releases/tag/v0.12.0)，
exact `b6e1da4f5fa9599cd84de69b1dbf4055fdbbcba7`。0.13.1 尚未创建 tag、发布安装器或验收
公开下载。机器恢复索引见 [2026-11-14-state.json](2026-11-14-state.json)。

## 已完成的图标与小控件实现

按 Navicat 18.0.5 build321 实际主/查询/连接窗口，独立重画主栏、对象、九驱动、供应商及
常用动作图形，分别使用28主栏、16树/页签、14查询选择器、50连接网格与16列表/筛选。
连接状态、开关、禁用和DPI分别处理；修正灰叉页签关闭及旧按钮释放，未启用缺失工具。

Oracle General 实际为纯端口框，没有上下箭头，Up不改1521；配置栏是蓝/灰pane图形，
新增配置是灰圈加号。实现已合流，并保留非法端口草稿、TNS隐藏原值、原禁用功能和SSH
现有stepper。表单按自然标签宽度居中，修正父布局裁掉300px字段右边缘的问题。通用连接
页复用两个profile图形，不能冒充其它驱动参考页已经验收。

两次原版实际搜索确认 Atlas 用橙色Mongo叶、Redis Enterprise Cloud用粉色Redis R，供应商
区均为空；空搜索全画面另确认12供应商筛选。旧“14供应商”是把两个直接数据库别名混入
供应商的误分类；14组目录数据全部保留。Evidence IDs、限制和独立绘制边界见
[图标专项](../validation-icons-navicat18.md)。原版证据未进Git或包，临时SQLite已清理，
所有参考连接Off，正常Quit，REA全部export/close。

## Git ca4 的本机资格

- 默认全源2693通过/662条件跳过，3355 collect/run/JUnit精确，零失败/错误；242声明源和
  原生helper前后/Git相同，112.521秒，warnings=error。跳过不算真实数据库执行。
- 合流实际Cocoa七模块249通过/3既有真库条件跳过；252清单精确、242源相同。生命周期
  子pytest特意使用offscreen，未混称Cocoa。原pool删除修复在真实before_close_func中清理。
- 本机ARM64 bundle构建及隔离smoke成功，样例6×4、Oracle26.1四项及另13探针true。
  Cocoa为显式请求，smoke不自行报告Qt平台；独立源窗口报告为实际Cocoa。
- 包内静态24项通过：79应用模块/入口与源/Git一致、61 Mach-O、4Oracle机器码、32driver
  Python、55wheel payload/许可证、95包内链接分别核对。不是六目标或安装器验收。
- fresh owned Oracle实际Cocoa3/0：Test/保存重开/临时密码/schema/表/视图/Decimal，计算
  视图Stop/同会话复用/Disconnect与Shutdown；独立106结构/资源检查通过。runner声明24源，
  不冒称全部242源；28阶段图/16种唯一画面目视记录另存。
- 生成数据的实际Cocoa主栏/查询/表/选择器亮暗12图，加通用profile两驱动On/Off亮暗8图，
  共20全尺寸画面均实际查看；完整主题、失活/悬浮/焦点、所有向导与三平台参考仍开放。

新源码须重新绑定其测试/录制/诊断范围；若复用ca4运行证据，必须明确证明对应运行源码
字节一致，不能把其242输入或计数改名成下一版本资格。详见 [0.13.1记录](../validation-0.13.1.md)。

## GIF：已录制，颜色审查未通过

ca4实际Cocoa新35阶段、471帧、78.5秒、1944860字节，摘要
`14e0d206f81e47873bf7af765ab944a11a8019dac7c5e37e38b25aedc6c79711`。
全部帧通过可见文本保护和独立解码，35全尺寸中帧及35首/中/尾联系表已目视，无真实数据
泄漏或缺glyph阻断；但Oracle卡片由原始own PNG的255,0,51变成GIF的205,99,77，颜色明显
偏棕，**不适合README，未复制公开资产**。偏色具体发生点尚未证明；编码器正在做真实颜色、
文字、帧序、时长和尺寸对照，再实际录制和审查。不能把离线重编码诊断产物冒充新演示。

README暂保留原Git211的34阶段/457帧同一份GIF。新录制的精确owned容器生命周期已独立
核销毁/不存在；卷ID和temp路径未独立保留，仍只记producer断言。Oracle表单为空，不冒称
Oracle数据库执行。私有新证据在 `artifacts/release-0131/`。

## 必须保留的历史与继续顺序

`v0.13.0` 保持在81523aa，正式43487011620因macOS Intel fixture teardown失败、publisher
skipped，未发布。其真实framework DeferredDelete红/绿证明及原SQLite allocation原因未知
分别保留；不要与本次OpenSSL导入故障混为一个原因。此前Git211 main43473238808的九必需
成功、15artifact/六安装器字节资格也保留为历史，不抵消任一失败，不借给下一tag。

1. 合流三平台测试修正、OpenSSL测试环境恢复和Oracle安全启动诊断；完成GIF颜色修复。
2. 冻结新源，验证受影响测试，明确运行源码字节绑定；重新实际录制/审查同一份GIF。
3. 新main九必需通过后实际核15artifact/六字节；新正式tag十job通过后另核六安装器、
   checksums/manifest、匿名八资产及公开ARM64 DMG隔离Cocoa smoke，不改旧tag。
4. 持续逐项对齐剩余小元素、完整Oracle和其它数据库工具。Query事务/DDL、及时取消
   server SLEEP、所有认证/安全/类型及三平台实际参考仍开放，不在里程碑后暂停goal。

不清理未合并分支或带改动工作树，不导入真实连接或操作用户数据库来验收。
