# 0.13 原生交付验证

本页当前记录 **main 预检已完成、正式 tag 与公开发布待独立验收**。最新公开版本仍为
v0.12.0，不能把本轮预检文件当成公开 0.13 安装器。完整 goal 保持 active，Oracle
有限浏览与图标修整不等于完整数据库工具或 1:1 UI 通过。

## 应用源码与本机证据

应用冻结检查点为 `211fef0ec0c7edbca2c016d0fd887e4acd977c90`；后续 README/GIF 和本页
均为文档改动，正式 tag 必须再逐 Git blob 确认全部 240 个声明运行/test/build/workflow
输入相同。具体实现、原失败与本机证据分别见 [Oracle 候选](validation-oracle-0.13-candidate.md)、
[图标专项](validation-icons-navicat18.md) 与 [当前 snapshot](snapshot/2026-11-14-goal-resume.md)。

默认源码 2633 通过/662 条件跳过/零失败，实际 Cocoa Oracle3/0、本机 ARM64 bundle
构建及 packaged smoke/23 静态检查分别通过。README 唯一 GIF 为实际应用的 34 阶段、
457 帧、76.17 秒；34 个阶段各查看全尺寸中帧，全部首/中/尾联系表辅助检查，457 帧执行
文本 guard 并独立解码。只含隔离生成数据，Oracle 主机/用户名/密码均空，未连接或保存。
GIF 为 1,800,760 字节，SHA-256：
`b81e97bc17640e04829d1c830d5472f51de64baa2d38936290c7bddb7bf31854`。

## main 预检

[43473238808](https://github.com/shubolab/opennavicat/actions/runs/43473238808) 精确绑定 Git211，
九 required job success，publisher 按 main 规则 skipped。实际 runner/provenance 为六
OS/architecture 桌面目标加 security 与两原生 Linux Oracle。独立审计实际下载 15 artifact：
六 installer、六 desktop validation、security、两个含 backend/window 子目录的 Oracle 包。

- 六个 EXE/DEB/DMG 实际文件的大小/SHA、各自 manifest/sidecar、合并边界与目标架构相符。
  原生构建、测试、packaged smoke、实际安装、启动和卸载收据逐目标核对。
- 两份 DMG 在本机仅只读挂载检查，之后均确认 detach、挂载点失效且底目录为空。
  Windows/Linux 安装器在各自 CI 原生 runner 执行，未在本机假执行。
- QuickJS source→processed→frozen→installed 摘要链及 binary native identity 核对。
  每目标 frozen/installed 均为 Oracle26.1.0，四项 adapter/native extension/public params/
  thin flags 均明确 true，不能把导入成功或声明当作完整 Oracle 数据库工具。
- 两原生 Linux 架构各自 backend14/Window3 required/JUnit Counter 精确、零跳过，源
  hash 与 exact Git211、镜像 child/config pin、原生 Docker engine/uname、loopback、
  app session/容器/进程清理收据分别核对。四次 fixture run/CID/name 不混用。
- 其它 scope 分别核 collections511、stream backend74/window25/security2/cleanup13，
  不将它们相加冒充一次总体或参考验收。

私有审计与六个预检摘要保存在
`artifacts/release-013/main-audit-43473238808/{verification,delivery-handoff}.json`。
API 的 artifact ZIP digest 已保留但没有重新计算 ZIP digest；六安装器实际文件则已独立
计算 hash 并与各自 manifest 和 sidecar 比较。禁止把这里六个预检 hash 写成正式公开 hash。

## 正式发布仍须完成

下一 annotated `v0.13.0` 必须从不含 CI 跳过指令的发布检查点触发正式 tag workflow；
其说明见 [GitHub 官方文档](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)。
重新核 exact source、九 required 与 publisher、15 artifact、六正式安装器、checksums 和
合并 manifest，再匿名下载八个公开资产，逐文件比 API size/digest、SUMS 与正式 CI 字节，
并对公开 ARM64 DMG 做隔离 Cocoa smoke。正式与预检摘要即便碰巧相同也必须各自核验。

当前尚无正式 tag/公开八文件或公开 ARM64 smoke 资格。无发布者签名与 Apple 公证；
Windows ARM64 Setup 引导器为 x86，应用和依赖为原生 ARM64。完整功能/视觉、人验与
Windows/Linux/macOS 原版对照继续开放，不能在这个里程碑后暂停完整 goal。
