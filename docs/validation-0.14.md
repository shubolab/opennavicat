# 0.14 开发候选验证

用户将本轮工作改为半小时内收尾，新的硬截止为 **2026-11-14 17:16:42 UTC / 北京时间 2026-11-15 01:16:42**，取代原十小时截止。完整目标未完成；所有最终 UI、工具和三平台参考门禁继续见 [parity matrix](parity-matrix.md)。

本轮整合 SQLite 加密高级页、15 原样候选、空 cipher 有限兼容推断、14 可执行格式、主/附件的独立绑定 key、OS keyring 保存选择、串行 worker 创建/关闭、严格来源/文件归属和取消后事务清理。普通 SQLite 保持标准库。密钥不写 workspace JSON；不保存时每次新 Open/Test 重新输入。数据库用户名/密码认证仍不是已实现的 SQLite user-auth。

参考已实际查询14种生成文件，另有AES256反向互读。provider200、backend/model/plain154是独立叶的专项；本机组合136以及真实窗口/生命周期/Oracle回归53分别通过，不相加作为完整产品验收。AES256实际Good创建、保存、Open、绑定写入和查询两行、只读拒写、错key/标准库拒读、hash稳定和密码防落盘的真实窗口测试已转绿；encrypted session关闭也进入串行worker。正常Session cursor在finally关闭，外部direct-provider保留busy reader后interrupt的rollback边界仍开放。

打包叶249项通过，并实际执行macOS ARM源码、源树外wheel、PyInstaller app及隔离DMG安装/启动/卸载；该叶实际版本仍0.13.3且QPA offscreen，不能借为新0.14版本或六目标资格。精确依赖apsw-sqlite3mc3.54.0.0，五份第三方许可与原文哈希随wheel/包保留，缺provider即失败。新工作流已加源、整包和已安装加密文件探针，仍需最终整合源码的六原生目标结果。

MC产品备份仍在创建目标前明确拒绝；独立已测试backup-target helper不等于产品备份接入。完整附件cipher继承、rekey/转换、SQLite2、General user-auth、设置位置/自动连接/初始查询及全部维护仍开放。12px Cocoa combo使用QMacStyle，20/36px为局部Fusion可读性回退，回12px恢复原生；既有profile栏/tabs大字裁剪及完整视觉对照仍开放。

本次发布状态仍以实际公开版本为准。0.13.3冻结4f5a914的main CI43615785473九个必需任务全部成功，15份产物/六安装器已独立审查通过，但未创建正式tag或新release，不把这些包借给0.14。

最终完整测试、GIF、CI和交接状态将在 [本轮 snapshot](snapshot/2026-11-15-ten-hour-resume.md) 与机器索引中补充。

## 最终源码本机完整验证

冻结 `6d6f906573829934e43109584e2a43d278161c05` 的完整默认 suite：**4,294 项，3,621 通过、673 条件跳过，零失败/error**。两次 collection 顺序与 JUnit multiset一致，286 源输入、149测试模块及七份 helper实际字节均固定；前后源码与Git对应，child进程/Store清理通过。[有界收据](evidence/014-default-6d6f906.json)记录实际skip原因。条件跳过不代表相应数据库或Windows原生能力通过。

保留 `c00dcf6` 的完整原失败：3,615通过、673跳过、6失败，其中四项是录制器flush proxy遗漏新的内存cache；另两项为新provider缺key消息和inactive/plain配置验证顺序。已修正clone字段、保留旧plain优先验证，并在测试中同时禁止标准库与MC factory，以证明缺key在文件访问前失败；随后相关288通过/3条件跳过及上述完整重跑通过，未回填旧记录。

新 [0.14 CI](https://github.com/shubolab/opennavicat/actions/runs/43619065100) 使用同一冻结源码，收尾时尚未全部完成。未发布0.14或0.13.3；[0.13.3六安装器审查](evidence/0133-main-4f5a914-installer-audit.json)属于不同冻结源码，不作为0.14六目标资格。

## 当前 README 实录

已从冻结6d6f906重录并原样更新唯一应用GIF：真实Cocoa、38阶段、521帧、约86.84秒、1,988,689字节；SHA256 `639be8c6ec9276b93dac0601a55ff34b30e85f931c81db32e9b2203ec3b80a06`。首次c00录制因snapshot漏字段失败，原失败保留；修后全部源/helper/私有工具前后不变，owned工作区与帧文件清理有本次运行收据。两位审查成员分担0–18/19–37，实际逐张查看38全尺寸代表帧；521帧Qt与ffmpeg解码RGBA完全相同，不冒称逐帧人工或源PNG比对。

本次primary marker34/34、driver-fill mode209/209精确匹配源常量；secondary三类mode差为1/7/36，这些不是全图最大编码误差，原始PNG已清理，不能归因。窄排序规则/preview格式、保存名称首端、viewport底部和长JSON可读性仍保留U15。stream画面为generated脚本/OK/耗时，不作为可读事件行证据。原487素材和颜色差仅作历史，不回填。本次Mongo容器/两卷缺额外exact-ID独立不存在复核；source收据的清理声明与独立核验分开，后者保留待续。

最终新GIF审查manifest SHA256：`85b5941ffe5efed464e8e390b166fe8c5238c099172c3cbce5acea8dd75284c0`；私有完整审查在本机 visual-clipping-0133 工作树的 `artifacts/gif-review-6d6f906/`，不随clone自动取得。
