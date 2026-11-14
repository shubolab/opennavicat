# 0.14 开发候选验证

用户将本轮工作改为半小时内收尾，新的硬截止为 **2026-11-14 17:16:42 UTC / 北京时间 2026-11-15 01:16:42**，取代原十小时截止。完整目标未完成；所有最终 UI、工具和三平台参考门禁继续见 [parity matrix](parity-matrix.md)。

本轮整合 SQLite 加密高级页、15 原样候选、空 cipher 有限兼容推断、14 可执行格式、主/附件的独立绑定 key、OS keyring 保存选择、串行 worker 创建/关闭、严格来源/文件归属和取消后事务清理。普通 SQLite 保持标准库。密钥不写 workspace JSON；不保存时每次新 Open/Test 重新输入。数据库用户名/密码认证仍不是已实现的 SQLite user-auth。

参考已实际查询14种生成文件，另有AES256反向互读。provider200、backend/model/plain154是独立叶的专项；本机组合136以及真实窗口/生命周期/Oracle回归53分别通过，不相加作为完整产品验收。AES256实际Good创建、保存、Open、绑定写入和查询两行、只读拒写、错key/标准库拒读、hash稳定和密码防落盘的真实窗口测试已转绿；encrypted session关闭也进入串行worker。正常Session cursor在finally关闭，外部direct-provider保留busy reader后interrupt的rollback边界仍开放。

打包叶249项通过，并实际执行macOS ARM源码、源树外wheel、PyInstaller app及隔离DMG安装/启动/卸载；该叶实际版本仍0.13.3且QPA offscreen，不能借为新0.14版本或六目标资格。精确依赖apsw-sqlite3mc3.54.0.0，五份第三方许可与原文哈希随wheel/包保留，缺provider即失败。新工作流已加源、整包和已安装加密文件探针，仍需最终整合源码的六原生目标结果。

MC产品备份仍在创建目标前明确拒绝；独立已测试backup-target helper不等于产品备份接入。完整附件cipher继承、rekey/转换、SQLite2、General user-auth、设置位置/自动连接/初始查询及全部维护仍开放。12px Cocoa combo使用QMacStyle，20/36px为局部Fusion可读性回退，回12px恢复原生；既有profile栏/tabs大字裁剪及完整视觉对照仍开放。

本次发布状态仍以实际公开版本为准。0.13.3冻结4f5a914的main CI43615785473九个必需任务全部成功，15份产物/六安装器已独立审查通过，但未创建正式tag或新release，不把这些包借给0.14。

最终完整测试、GIF、CI和交接状态将在 [本轮 snapshot](snapshot/2026-11-15-ten-hour-resume.md) 与机器索引中补充。
