# SQLite 多附件、错误草稿与高级选项：新增实际观察

2026-11-14 22:47–23:02，Asia/Shanghai，macOS dark。参考为指定的 Navicat Premium
18.0.5 build 321，arm64，执行文件 SHA256
`3857a354fde7f42f8afbf9ef3a25956e6ff3b9745f598f77bbab1d27e7493bd1`。
这是[数据库页已有观察](sqlite-connection-databases.md)的后续片段，不是 OpenNavicat
对应实现已经通过或三平台参考一致的声明。

## 证据来源与限制

本页的交互与结果来自 CUA 实际操作及每次动作后的最新 AX 状态；长标题另有全窗口目视。
所有 SQL 和文件均为本轮生成。仅打开唯一新建的测试连接；没有打开、编辑或连接任何
既有 profile。初始树以及部分全窗口截图包含未打开的既有连接名称，只在工具会话目视，
不导出、不放入仓库或包，也不作为 README 演示素材。

REA 成功取得的 **静态** Mach-O 身份 Evidence ID 为
`ev_846e9ea175edf1704b793616ecb392f08ab2b4a2cb208917890948fc63f9418d`；它只证明
本页所绑定的参考文件身份、布局及导入声明，不能代替下面的运行行为证据。
三次 native UI observer 尝试均在 Swift helper 编译阶段因 `/usr/bin/xcrun` 的 60 秒
上限失败，没有取得有效的选定窗口身份或 UI Evidence ID。首次调用还使用了错误 PID，
不能计作有效参考选择。后续只读 doctor 与框架导入预热没有修复该问题；未更改 REA
配置、安装工具链或冒称捕获成功。失败保留，不以静态 Evidence ID 标记 CUA 结果。

静态证据已私有导出并关闭会话，bundle 563,662 字节，SHA256
`b2af04ae4d81713188cd52671b3298c24549ca555e1bcbc2de6e9bd99d444311`。
运行步骤摘要、fixture 身份及清理回执留在本地忽略的 `artifacts/reference-resume-10h/`。

## 自有 fixture 与草稿校验

在唯一临时目录新建 main、first、second 三份 SQLite，各有 `generated_data(id, label)`
及两行不同的生成值。测试连接名为 `Generated SQLite multi 20261114-1449`；下面的路径
用 `$OWNED_TMP` 代称，不记录个人临时目录。所有附件均未勾加密。

| 动作 | 实际结果 | 可证明的边界 |
| --- | --- | --- |
| 新增 sheet 的名称、文件都空，点击好 | 返回数据库页并增加一行，名称/文件空，已加密为否 | sheet 接受空元数据；不是实际 ATTACH 成功 |
| 保留该空行，点击测试连接 | 成功，服务器版本 3.45.0 | 此次 Test 不拒绝这份空草稿；未查询空 schema，不推断其内部处理 |
| 删除空行，新增名称 `main`，指向生成 first 文件 | sheet 接受；Test 报 `main: database main is already in use` | 保留名在本次 Test 阶段报错，非 sheet 提前拒绝 |
| 第一行改成 `owned_first`，第二行以 `OWNED_FIRST` 指向生成 second 文件 | sheet 接受两行；Test 报 `OWNED_FIRST: database OWNED_FIRST is already in use` | 这个大小写重复组合在实际连接测试中冲突 |
| 第二行改成 `owned_second`，文件改为本轮不存在的 `generated-missing.sqlite` | sheet 接受；Test 报 `owned_second: unable to open database: …/generated-missing.sqlite` | 此次不存在路径不被创建；不能推广为所有打开竞态均无副作用 |
| 第二行恢复生成 second 文件，保留两个不同别名 | Test 成功，服务器版本 3.45.0 | 随后的会话查询另行证明两个附件可用 |

每次错误后草稿仍在，可双击名称或文件列重开 sheet 修改。上述仅覆盖空的名称与文件组合、
`main`、一个 ASCII 大小写重复组合和一个不存在路径；`temp`、空名但有文件、空文件但有
名称、空白或非 ASCII 大小写、引号、NUL、同文件多别名、权限、目录路径及并发变化未观察。
参考接受错误元数据不等于应用应忽略错误：UI 草稿接受时机与实际 Test 错误门禁需要分别
跟踪，不能以更早拒绝的实现直接宣布 1:1 通过。

## 多附件保存、重启与只读查询

本轮新建常规页的保存密码初始勾选，随后明确取消勾选；没有输入任何密码。外层好保存
唯一测试连接后，连接状态保持 Off；显式双击打开才显示三个数据库 `main`、`owned_first`
和 `owned_second`。保存密码复选框与连接的 Off 状态是两个不同事实。

在主库上下文的新建查询中，以一次单行粘贴输入并逐字核对 AX 完整 SQL 后运行：

```sql
SELECT 'main' AS source, id, label FROM main.generated_data
UNION ALL SELECT 'first', id, label FROM owned_first.generated_data
UNION ALL SELECT 'second', id, label FROM owned_second.generated_data
ORDER BY source, id;
```

结果为六行：first/main/second 各两行，值分别为该库的 `*-one`、`*-two`。
没有保存查询。正常 Quit 时选择不保存该 SQL 草稿，旧参考 PID 3806 随后不存在；
CUA 下一次观察重新启动为 PID 16961。重启后的唯一测试 profile 仍为 Off，显式打开后
再显示三个库；重新新建查询、核对同一 SQL 后运行，再次取得相同六行。
随后关闭连接时再次不保存查询。这里只证明本轮普通文件、两个附件及无密码配置经过
进程重启仍可实际查询；不证明加密附件、凭据记忆、重连取消或所有工具路由。

新建查询已完成运行时，查询菜单显示运行、运行当前语句、停止（禁用）、解释、遇到错误
时继续及 AI 相关入口；未执行继续错误或解释，也未建立快捷键合同。连接右键菜单的
附加数据库入口在连接打开时可用、关闭后禁用；没有执行该入口，不能据菜单存在视为
相应工作流已经验收。

## 高级页 Cipher Name 与取消隔离

关闭后的生成连接通过编辑连接进入高级页，实际看到设置位置、自动连接（关闭）、初始
查询（空）、加密（关闭）、密码、保存密码（勾选但禁用）和中文标签“密码名称”的组合框
（禁用）。高级页保存密码与本轮已关闭的常规页保存密码是独立控件，不推断其内部秘密存储。

只在未保存的编辑草稿临时勾加密，密码框、保存密码和可编辑组合框随即可用。没有输入
密码、选算法、执行 Test 或保存。展开组合框后，AX 列出以下十五个候选项；截图目视首部
及滚动列表外观，其余候选由同次 AX 列表确认，未逐项执行：

| 普通名称 | legacy 名称 |
| --- | --- |
| aes128cbc | aes128cbc legacy |
| aes256cbc | aes256cbc legacy |
| chacha20 | chacha20 legacy |
| sqlcipher | sqlcipher legacy v1 / v2 / v3 / v4（四个独立项） |
| rc4 | rc4 legacy |
| ascon128 | ascon128 legacy |

取消整份编辑后再次进入高级页，加密恢复关闭、组合框恢复禁用，确认临时开关未保存。
静态 Mach-O 声明导入 `libsqlite3mc.0.dylib`，但导入与候选名称都不证明某算法在实际
加密文件中生效、格式版本、附加库继承策略或与独立 provider 兼容。真实新建加密文件、
错误密码、外部兼容读取、保存密码、所有候选及三平台仍开放。

## 长名称信息标题

选中本轮 36 字符的测试连接时，右侧信息标题目视为 `Generated SQLite…`，明确在右侧
省略；左树连接名同样右侧省略。AX 文本输入区保留完整连接名。没有测试悬浮 tooltip、
信息面板宽度变化、长数据库/集合名、pipeline 节点或其他主题；不能把此连接标题推广为
所有文本的截断策略。它只为修正硬裁字提供一个实际参考合同，U15 保持开放。

## 清理与验收状态

UI 永久删除出现不可撤销勾选，首次取消；随后用户当次明确确认删除本轮测试连接和文件，
才恢复 cleanup 相位并最终删除唯一命名 profile。UI 计数从 17 变为 16，该测试项消失。
未操作其他 profile；正常 Quit 后 CUA inventory 为未运行，指定执行文件路径的进程也
无匹配。两个 CUA 相位分别提交其最终结果摘要；没有让下一次 AX 观察意外重启应用。

三文件在删除前均仍为 8192 字节、两行，SHA256 与生成后完全相同：

| 文件 | SHA256 |
| --- | --- |
| generated-main.sqlite | `4162d4cb499a0e1dbdbc0a5be04b59786bae97af1a9ddca6eb5cbeb3f3a2ee5c` |
| generated-first.sqlite | `e1c53e9283c7e8b9e2c14cbe62ca38ff1657e66a582fa72eed308931c0dbc561` |
| generated-second.sqlite | `e7b16aca0aa721c08aee2260c8765eccad61de153710debaf63b0f8728ea9093` |

只在核对唯一生成目录仍恰为原三 regular 文件、无 symlink 或未知项后逐文件删除并 rmdir。
本轮专属 Settings 目录只含 id_cache.db、-shm、-wal 三个 regular 缓存文件；核对准确归属
后精确清理，无读取其内容。两处目录均不存在。不声称全局偏好或历史完全没有变化。

本轮未修改 OpenNavicat 代码或发布版本；U08、U15、C01 和全部工具/三平台门禁仍开放。
