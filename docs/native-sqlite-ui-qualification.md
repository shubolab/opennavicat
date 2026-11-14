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
Store、keyring/子进程零访问和窗口清理都保留原门禁。POSIX 收集器按其实际收据检查
进程组清理，Windows 文件日志 owner 保持原有单 leader 的准确边界。

本改动只是扩大原生控件回归范围。跨平台执行结果由各次验证回执证明，流程声明不等于
已经在六个目标执行；U08/U15 以及三平台实际参考继续开放。
