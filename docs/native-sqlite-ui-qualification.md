# 原生 SQLite UI 与长对象名补充门禁

`scripts/test-sqlite-window.py` 在宿主真实桌面 QPA（Windows `windows`、macOS `cocoa`、
Linux `xcb`）上运行固定声明模块。不能把 `offscreen` 当作原生结果。六个 CI 安装器任务
均调用此门禁，原生 UI 门禁、全部默认测试、打包及真实参考验收仍分别记录。

原来的五个 SQLite 模块全部保留：

- `tests/test_sqlite_connection_databases_ui.py`
- `tests/test_sqlite_connection_databases_font_ui.py`
- `tests/test_sqlite_connection_databases_native_contents.py`
- `tests/test_sqlite_small_visuals.py`
- `tests/test_sqlite_browse_native_layout.py`

另加 `tests/test_bounded_object_names_ui.py` 作为补充范围：pipeline 卡片短名/右侧省略、
信息面板长标题、plain text、AX 完整名称和 tooltip 保留，覆盖其声明的字体与主题。
它不访问 MongoDB、SQLite 或外部数据库，不执行 JS VM，也不替代完整 MongoDB 工具或
所有长文字/控件状态的验收。

runner 的 case 数量从完整 collection 动态取得，不硬编码“59”或新增用例总数。
collect/run 顺序、JUnit multiset 和每个声明模块均须完整对应，零跳过、失败或 error。
源文件须与冻结 Git 提交及执行前后字节一致；每项运行的实际 QPA、应用模块路径、隔离
Store、keyring 零访问、Python 子进程零尝试和窗口清理都保留原门禁。POSIX 收集器按其实际收据检查
进程组清理，Windows 文件日志 owner 保持原有单 leader 的准确边界。

本改动只是扩大原生控件回归范围。跨平台执行结果由各次验证回执证明，流程声明不等于
已经在六个目标执行；U08/U15 以及三平台实际参考继续开放。

## 本机首次扩大范围后的执行

冻结源码 `839c71d2daabb9c7698301218537b41e3b4ec498`，Darwin arm64，物理 `.venv`
Python 3.13.11、Qt 6.12.0。实际 CLI `--qt-platform cocoa` 完整执行六模块 **67 项通过，
零跳过、失败或 error**。五个原 SQLite 模块分别 36/8/8/6/1 项，新增长名补充 8 项；
完整 collect/run 地址顺序与 JUnit multiset 对应，没有裁减旧范围。

93 项源输入在执行前、Git blob 和执行后三者 SHA256 完全一致，HEAD 未变；每项实际
QPA 均为 cocoa，应用 import 都来自该冻结工作树。keyring 尝试、Python 子进程尝试、
session 后可见窗口均为零。collect/run 各自受控子进程退出 0、被回收，日志未超界；
POSIX pipe/readers/group cleanup 收据均通过，两个所录 PID 后验不存在，临时 Store 的
父目录也不存在。这里的 QPA 和控件回归通过不是人工目视、真实数据库、Windows/Linux
执行或 1:1 参考验收。

执行前同一组源码的 runner 单测与长名模块在 offscreen 共 40 项通过；这包含新增长名
8 项，与 Cocoa 67 项存在重叠，不合并计数。原生输出留在本地忽略的
`artifacts/reference-resume-10h/native-839c71d/`。

本工作树另外从固定 QuickJS-NG d7215b0 源档构建新的 arm64 raw helper（1,102,096 字节，
SHA256 `8f4dfabdd51aedbceaea9ac21ace55eebc8cfa40bbb737e915415e0a22e72978`），
构建回执与 runner.c SHA 对应，门禁前后 helper 字节不变。环境准备的旧档路径不存在、
PATH 缺 cmake 两次失败保留；随后在本物理 venv 使用官方缓存的 cmake 4.4.4 / ninja
1.13.2 成功构建。此 UI 范围没有调用 raw helper、JS VM 或数据库；构建存在不充当其
运行资格，完整默认集与 VM/安装器仍由各自门禁负责。
