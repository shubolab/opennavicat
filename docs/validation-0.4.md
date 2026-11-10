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

当前实现的SQLite引擎专项43项、页面组件12项、真实主窗口集成21项均实际执行通过。
修复后最终本机完整默认为 **219 passed / 133 skipped / 0 failed，14.46秒**，`-W error` 下通过。
默认套件中的远程服务/平台跳过项不作为协议集成通过，原有独立75项TLS/SSH的历史证据见
[0.3验证](validation-0.3.md)，本轮最终tag独立安全作业也已重新执行并通过。

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

首次原生运行 [42772595932](https://github.com/shubolab/opennavicat/actions/runs/42772595932)
中，Linux/macOS四个目标的真实安装验证与独立75项安全作业均成功；Windows两架构均在
Save→disconnect的时序上复现已排队schema刷新访问已关闭session的KeyError，整条流水线失败，未发布。
修复为断开/退出后忽略排队刷新，并用session identity拒绝同ID重连后的旧响应；
三个确定性真实主窗口回归覆盖了断开、退出和旧响应后到，不能以其它平台局部成功替代完整门禁。

修复后的main流水线42773857224与最终tag流水线
[42774383062](https://github.com/shubolab/opennavicat/actions/runs/42774383062)
均全部通过。最终tag源码为 `6eb1eff36edeee24c4108b13223a712ad3c3dce3`；
[v0.4.0公开预发行版本](https://github.com/shubolab/opennavicat/releases/tag/v0.4.0)
于2026-11-10 15:25:09（北京时间）发布，六份安装包、SHA256SUMS及build-manifests.json共8个资产。

| 最终原生作业 | passed | skipped | 安装、运行、卸载 |
| --- | ---: | ---: | --- |
| Windows amd64 | 218 | 134 | 通过 |
| Windows arm64 | 218 | 134 | 通过 |
| macOS amd64 | 219 | 133 | 通过 |
| macOS arm64 | 219 | 133 | 通过 |
| Linux amd64 | 219 | 133 | 通过 |
| Linux arm64 | 219 | 133 | 通过 |
| 真实TLS/SSH独立安全作业 | 75 | 0 | 通过 |

六个目标均在对应原生host执行，实际安装后返回6行×4列查询、0.4.0版本、libpq18及真实加密provider成功。
原生映像逐项检查数量分别为macOS amd64/arm64的111/110、Windows的123/121、Linux的242/238。
公开资产再以无Authorization的匿名HTTPS下载，全部HTTP200。三份独立下载的校验文件/构建清单逐字节一致，
六份实际安装包的SHA-256和长度同时匹配校验文件、构建清单及GitHub公开资产digest/size。

| 实际下载文件 | bytes | SHA-256 |
| --- | ---: | --- |
| OpenNavicat-0.4.0-linux-amd64.deb | 71636754 | c219daf594ae6ac67ff3a552b7ba760665236253d8b1f1f5ea808e61254018dd |
| OpenNavicat-0.4.0-linux-arm64.deb | 70342590 | 340ae96edcf977cab5bb48f7fdf9b54ed2dbdad57174cdddde2431f977e641b4 |
| OpenNavicat-0.4.0-macos-amd64.dmg | 45341453 | e0021c6cbfc501d9cde890a62d10f594c05e066bff18833be216f2272e262bb9 |
| OpenNavicat-0.4.0-macos-arm64.dmg | 44986484 | 71a25c1e4eac323c565024a5a2bf8ef7876363375b244ad60f9378dd88223109 |
| OpenNavicat-0.4.0-windows-amd64-setup.exe | 38823970 | 3d10c30e1086a2c1ca5e42f88bfeef80266210e7804be064b0f63cf702deee87 |
| OpenNavicat-0.4.0-windows-arm64-setup.exe | 30303070 | f1b63665ff2a9ca08c4bfb83a26780564e7deb060518de35e3c11bd54739eb67 |
| SHA256SUMS | 616 | e269aa3542946cee700251eedff5f3f8e6c71c83d8631ce1a96480754237c32e |
| build-manifests.json | 195740 | 2942069f5e08e357b68d5322e776c00776ed0de9efebdc7601a0cd4e6b4b39a6 |

根复核收据保存在忽略目录 `artifacts/release-v0.4.0/final/verification.json`，
公开可复核证据为Release随附的
[SHA256SUMS](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/SHA256SUMS)及
[build-manifests.json](https://github.com/shubolab/opennavicat/releases/download/v0.4.0/build-manifests.json)。

公开后的macOS ARM64 DMG另在本机macOS27.2 ARM64进行只读挂载、隔离复制、Cocoa实际运行、清理和卸载；
两个DMG均从实际挂载内容核对全部原生映像、symlink及主程序摘要。Linux最终公开ARM64 DEB另在
干净Ubuntu24.04.5 ARM64/glibc2.39仅安装声明依赖和xvfb/xauth后运行，xcb依赖无缺，
实际6×4查询、中文截图及crypto probe通过，apt purge及任务容器移除已完成。

用户授权的 Dell Windows AMD64 主机已完成本地 RDP 实际界面验证，使用 Beta 中已有的免密会话，
没有改变远程桌面、网络或其它 Windows 工作。目标系统报告为
`Microsoft Windows NT 10.0.26100.0 / AMD64`。公开 AMD64 安装器实际下载的长度为
38,823,970 字节，SHA-256 与上表和发布清单一致；安装后主程序摘要另核对为
`b0e27a4ebf110dcef7d90e7dc33875c9eb8edf966fbfc13624af4c37269d3eed`。

在任务专用的隔离工作区中，实际通过桌面操作运行样例查询，获得 **6 行 × 4 列**，
并打开包含七页的表设计器，随后关闭应用。另通过 SSH 启动同一个已安装的主程序运行
默认 Windows 平台的 `--smoke-test`，退出码为 0；报告确认为 0.4.0、Windows/AMD64、
Python 3.13.17、Qt 6.12.0、SQL 6×4、libpq 18.4，以及 `crypto_legacy_probe=true`。
原始运行报告保存在本机忽略目录 `artifacts/dell-validation/runtime-smoke.json`，
RDP/安装/卸载汇总为 `artifacts/dell-validation/final-receipt.json`。

本地卸载退出码为 0。随后只读复核发现仅残留 `unins000.exe` 自删除程序；常规清理前再次核对
没有该卸载器进程、没有其它文件或链接，并确认 verification AppId 的卸载登记在 HKCU/HKLM
及对应 32 位视图均已移除。只删除这个任务工件及空的 Application 目录，保留父任务目录，
最终只读复核确认应用目录已消失。原始检查与最终回执分别为
`artifacts/dell-validation/cleanup-inspection.json` 和 `artifacts/dell-validation/cleanup-receipt.json`。
Windows 其它工作、全局网络及桌面设置未变。Linux AMD64 的 WSL **GUI** 验收按用户明确要求暂缓；
没有安装桌面，也不将后台协议测试计作 Linux 界面验收。

继续沿用未签名/未Apple公证状态和Ubuntu24.04构建环境边界，Windows ARM64应用及库为原生ARM64，
Inno引导程序为x86。用户已恢复目标，本轮里程碑交付后继续完整目标，不再次自动暂停。
