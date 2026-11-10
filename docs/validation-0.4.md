# 0.4.0：新版参考、SQLite 表设计与演示验证

日期：2026-11-10，Asia/Shanghai。用户更新参考并明确恢复目标；本轮实际打开
Navicat Premium 18.0.5 build 321，以自己的样例库完成新建表、修改表、SQL预览、
查询、多结果和草稿关闭验证。实际步骤见 [18版运行观察](ui-reference-18-observations.md)，
资源与手册证据另见 [静态报告](reference-18-static.md)。

## 实现与真实验证范围

- SQLite 表设计使用独立主标签，数据标签不受影响。新表和已有表均通过独立的 SQL
  预览确认保存；元数据读取、规划、保存通过同一串行后台队列执行，不在GUI线程操作数据库。
- 七页覆盖字段、索引、外键、唯一键、触发器、选项和SQL预览。已载入的原始SQL、隐藏约束、
  CHECK、generated表达式、排序规则、partial/expression索引等必须保留；无法安全解释的声明
  明确只读或拒绝修改，不能从有损浏览元数据重建表。
- schema计划绑定实际连接、schema目录和legacy ALTER状态，预览后发生变化即拒绝应用；
  取得BEGIN IMMEDIATE写锁后再次核对。复制行数、FK、视图、实际触发器程序在提交前验证。
- 实际回归覆盖数据、rowid、AUTOINCREMENT当前高水位、主键名称/排序/冲突策略、
  约束/索引/触发器、dependent view与INSTEAD OF trigger、父表CASCADE、失败回滚、取消、
  只读、外层事务、schema漂移和重新连接。
- 独立审查用临时SQLite复现并修复了legacy_alter_table=ON且FK=OFF时重命名破坏空子表引用、
  UPDATE OF列名大小写、inline FK的SET NULL/SET DEFAULT分词，以及命名表级PK修改冲突策略丢声明。
- MainWindow集成测试实际覆盖命名/预览时尚无DDL，确认后落库，刷新对象和保留导航展开，
  关闭/断开/退出的保存、不保存和取消，取消保存Sheet，未提交数据记录保护，以及旧预览错误不能覆盖新计划。

本机默认完整测试命令：

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -Werror -m pytest -q \
  --junitxml=artifacts/validation-0.4/pytest-results.xml
```

当前实现的SQLite引擎专项43项、页面组件12项、真实主窗口集成18项均实际执行通过。
最终本机完整默认为 **216 passed / 133 skipped / 0 failed，14.60秒**，`-W error` 下通过。
默认套件中的远程服务/平台跳过项不作为协议集成通过，原有独立75项TLS/SSH的历史证据见
[0.3验证](validation-0.3.md)，本轮发布仍会重新执行该独立安全作业。

源程序实际smoke返回0.4.0、Darwin/arm64、SQL6×4、libpq18.6与全部驱动，
实际加密provider探测成功。公共PyPI锁文件通过显式公共索引的离线一致性检查；本轮没有新增运行依赖。

## README 单段 GIF

按用户要求只发布一段实际运行GIF，由 [record-demo.py](../scripts/record-demo.py)
在隔离Store和生成的SQLite库中录制Qt窗口连续帧。示例连接名、客户、example.invalid邮箱、
城市、日期和SQL均受脚本控制，不读取原版连接、已有OpenNavicat工作区或系统密钥服务。
记录覆盖对象、表数据、SQL输入、真实6×4结果、表设计和真实planner的SQL预览；不应用预览变更。
公开GIF与不含本机路径的JSON记录位于 `docs/assets/`，临时PNG和样例库清理；多阶段复核图仅本机忽略目录保留。
最终单GIF为1440×900、85帧、14.170秒、336,504字节；已实际查看各阶段图，
包含右侧信息栏与选中字段的底部属性。其SHA-256为
`0e383846fc888fac79ee685f1a39a9b1ca2b6f7e28e036d5659b6b92aae95343`。

## 尚未关闭的差异与限制

可编辑设计器只支持SQLite；PostgreSQL/MySQL/MariaDB仍需独立无损DDL规划与适用事务语义。
SQLite虚拟/未知声明、外层事务、legacy ALTER设置、temp视图/触发器下的重建、混合重命名及其它修改
等不安全场景会拒绝保存；重命名应单独保存。表达式/partial索引的UI暂只读保留。
原版18简单修改实际留下旧表副本，当前采用原地ALTER或原子重建，**这项行为尚未完全对齐**。

九个数据库族、全部驱动专有对象、独立索引/触发器设计、连接管理器、导入导出/同步向导、
AI、Profiling/Masking/Dictionary、权限、自动任务、模型与BI及三平台完整UI验收仍开放。
新版工作台仍有未完成布局和交互，GIF、通过的测试和安装包不代表完整1:1功能验收。
全部门禁见 [parity-matrix.md](parity-matrix.md)，不得以此里程碑缩小最终范围。

## 原生安装与发布

发布仍要求六个原生OS/架构目标的测试、二进制架构检查、实际桌面后端smoke、安装及卸载，
加上独立真实安全连接作业。每份安装包使用SHA256SUMS和build-manifests.json，公开后另核对实际下载。
当前0.4.0安装流水线与公开下载状态将在完成后补录；存在本地测试或GIF不等于Release已发布。

继续沿用未签名/未Apple公证状态和Ubuntu24.04构建环境边界，Windows ARM64应用及库须为原生ARM64，
Inno引导程序为x86。用户已恢复目标，本轮里程碑交付后继续完整目标，不再次自动暂停。
