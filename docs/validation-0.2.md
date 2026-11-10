# 0.2.0 界面重建与验证记录

日期：2026-11-10（Asia/Shanghai）。用户已明确：完整还原本机 Navicat 的 UI 与数据库工具，
排除产品账号、登录、订阅和授权体系。本轮纠正 0.1 未实际对照原版而自行设计 UI 的偏差。
**当前仍未达到完整功能或 1:1 UI 验收，目标继续 active。**

## 参考端与重建端

本轮实际启动 Navicat Premium，并在自建 SQLite 样例连接上查看主窗口、对象列表、
数据表、SQL 运行前后、连接常规/SSL/SSH 页、信息面板和表设计窗口。
步骤及边界见 `ui-reference-observations.md`；静态 REA Evidence 仍独立记录。

重建了主工具栏分组、数据库分类导航、对象页、信息栏、紧凑查询操作区、
信息/摘要/结果切换、数据行复选、连续网格背景和连接窗口结构。
默认表格列宽可显示邮箱与时间，长字段宽度有上限；表头使用左对齐。
没有加入产品登录、头像、订阅、激活流程。

当前实现的对照输出位于 `artifacts/ui-reference/current/`，可通过
`scripts/capture_ui.py --theme dark` 重新生成。输出明确区分 Qt 客户区和原生外框，
不把源码渲染图伪称为参考截图。当前外框 1512 × 948 logical px，参考约 1512 × 949，
仍有约 1 logical px 差别；完整视觉状态和其它平台没有通过像素级对照。

## 行为测试和打包

| 环境 | 回归结果 | 打包程序 |
| --- | --- | --- |
| macOS 27.2 ARM64，Python 3.13.10，Qt 6.12.0 | 112 passed / 1 skipped，11.55s | dark smoke 通过；真实桌面打开新版、重连本地样例、点击运行并显示六行结果 |
| Debian 13.7 aarch64，glibc 2.41，Python 3.13.18，Qt 6.12.0 | 112 passed / 1 skipped，12.97s | 源码和打包后 dark smoke 均通过，中文字体实际渲染可读 |
| Windows | 未实际执行 | 同源脚本和 CI 存在，不代表 Windows 已验收 |

本次一次性数据库为 PostgreSQL 15.19、MySQL 8.0.36、MariaDB 11.4.13，
配合临时 SQLite 文件执行。唯一跳过项仍是测试 PostgreSQL 服务未启用的 TLS 专项。
运行命令与测试端口沿用原验证记录；最终3个任务容器均已移除。

新增验证覆盖：实际对象 rootpage/索引/触发器、分类导航、信息栏、查询失败清除旧结果、
忙碌状态、真实复选行选择和复制、空白绘制没有假数据、默认内容列宽、外框尺寸、
连接保存与连接测试分离、测试期间关闭保护。已有 SQL、事务、冲突回滚、类型交换、
历史/草稿和远程驱动断言继续执行。

这些是**现有实现的回归测试**，不能代替完整 Navicat 工具流程或 1:1 验收。
原生桌面观察确认新版查询及分类/结果状态；系统前台菜单的全部状态、所有对话框、
向导和多驱动专用窗口继续保留在 UI 验收清单内。

## 最终产物

| 平台 | 文件 | ZIP 大小 | SHA-256 |
| --- | --- | ---: | --- |
| macOS ARM64 | `artifacts/OpenNavicat-0.2.0-darwin-arm64.zip` | 49,692,506 bytes | `96e1824c1a2d0d5a19902b461d68c95a02f7e9b56ebb701ab12f9c0971395837` |
| Linux aarch64 | `artifacts/v0.2/linux/OpenNavicat-0.2.0-linux-aarch64.zip` | 103,863,707 bytes | `1805a72574c14776ff2e4fc4b53e70dd0e52e906ede12bd4d48c103a3bca4567` |

macOS 原生程序为 `dist/OpenNavicat.app`。macOS ZIP 保留 122 个符号链接；两平台
主程序均保留 `100755` 权限，没有 WebEngine。macOS 构建前/后、Linux 快照及当前
所有应用 Python 文件摘要一致。构建日志、快照、JUnit 和指标位于 `artifacts/v0.2/`。

两平台均包含 `qtbase_zh_CN.qm`。Linux 使用系统 Noto Sans CJK SC，未捆绑整套字体；
最小 Linux 环境需要安装相应 CJK 字体及 Qt 运行依赖。macOS 仍未进行正式发布签名和公证，
Linux 桌面人工操作和其它发行版/架构仍需验收。

0.1 日志和指标保留在 `artifacts/v0.1/`，旧 ZIP 未被覆盖。
全部未完成的 UI 和工具能力持续见 `parity-matrix.md`；没有把后续阶段改成可选目标。
