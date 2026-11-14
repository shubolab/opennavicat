# SQLite3MC 独立适配层合同

2026-11-14，macOS arm64。此叶新增 `opennavicat/sqlite_cipher_provider.py` 和真实 provider
专项测试，尚未接入标准 SQLite backend、连接 UI、产品依赖或六目标安装器。
[供应物实验](sqlite-cipher-provider-feasibility.md)与本页的适配验收分别记录。

## 当前实现

仅接受精确的 `apsw-sqlite3mc==3.54.0.0`、APSW 3.54.0.0、SQLite3MC 2.6.1 组合，并拒绝
同时安装普通 `apsw` 的环境。模块导入时不加载可选原生 provider；实际 factory 在打开
文件前核对依赖。缺失、重叠或版本不同均明确失败，备份也不回退到标准库明文连接。

- `connect` 必须显式提供非空密钥；`connect_plain_main` 是普通主库加密附件场景的独立
  factory，不把空密钥解释为降级选择。默认只打开现有文件，只有显式 `create=True` 才创建。
- cipher、legacy、密钥按正式 provider API 的顺序配置，密钥不进 URI、SQL 拼接或 statement
  cache。新加密文件必须真正填充并读取 catalog，不能把 key pragma 返回 ok 当解密证明。
- 实现项目需要的 cursor execute、description、fetchone/fetchmany/fetchall、rowcount、
  executemany、close，以及连接事务、getlimit、progress handler、interrupt 和 close。
  description 在原生 exec trace 的首步前缓存，空 SELECT 完成后仍保留七字段 DBAPI 形状。
- 一个 execute 只接受一个真实语句。SQLite 自身的 complete-statement tokenizer 判断
  候选分号边界，scratch 内存数据库以全拒绝 authorizer 检查尾部；真实连接的 trace 再检查
  一次。字符串、注释和 trigger body 中的分号不由正则猜测。这样也能在准备期 PRAGMA
  改状态之前拒绝真实多语句尾部。
- executemany 限于不返回行的 DML；RETURNING 在首行写入前拒绝。它遵循调用者事务，
  不是自动原子批量接口；导入、网格和比较的调用方仍须显式事务及冲突检查。
- 文件、记录值和 ATTACH KEY 使用绑定参数。普通主库配加密附件的实际查询已覆盖，
  configure_attachment 只管理下一次附件的临时算法参数，不代表附件完整工具已验收。
- 对外错误保留标准 sqlite3 类别及可用的 SQLite 代码/名称，消息不带原 SQL、绑定值、
  provider 注释或原始异常链。此安全错误呈现尚未做 Navicat UI 文案对照。

`read_only` factory 限制主文件的原生打开模式；完整会话只读还需 backend 保留已有的
`PRAGMA query_only=ON` 与每个附件 URI 的 `mode=ro`。此叶尚未接入这些会话初始化合同。
所有数据库调用仍须由窗口的串行后台队列调度，取消另走独立 worker；适配层没有 Qt 访问。
fetchmany 是增量取行接口，不承诺限制 SQLite 的总执行工作、文件 I/O 或内部内存。

## 加密备份的明确边界

备份只接受两个不同的已加密 provider 连接、`main` schema、相同的有效 cipher/legacy
和相同的实际 page_size。没有可信算法元数据的附件、普通主库、跨算法或跨页大小目标，
均在写目标之前返回 NotSupported。目标 key 仍由调用方显式提供，只存在进程内存。

本轮原生跨算法探针发现：AES256 source 备份到部分不同算法目标时，复制 API 可正常
返回，但目标关闭后重开报 CORRUPT；旧 SQLCipher legacy 页大小不同的路径会直接报错。
原始探针结果完整保留，不能用 API 返回成功替代关闭重开、quick_check 和独立密钥验证。
上游也说明不同算法有不同的保留字节与页大小要求，参见
[C 接口说明](https://utelle.github.io/SQLite3MultipleCiphers/docs/configuration/config_capi/)。

为保持已记录的 main 算法元数据有效，公共 execute 在 SQLite authorizer 处拒绝 key、
rekey、cipher、legacy、算法参数 pragma 及 sqlite3mc_config 调用；专用 factory 与附件
配置 API 管理这些参数。完整查询工具的手动 rekey/格式转换能力仍开放，未以此限制缩小
最终功能范围。底层私有 `_raw` 对象不属于调用方可修改的接口。

## 实际验证

物理 Python 3.13.11 venv、实际 macOS arm64 原生 extension 的专项集合为 **89 项通过，
零跳过/失败/error**。其中包括：

- 14 个支持配置的真实创建、关闭重开、错误密钥、只读、标准 SQLite 拒读和文件哈希。
- 全部 14 个支持配置的同配置加密备份，各自用目标密钥重开、quick_check、精确两行、
  源密钥拒读、标准 SQLite 拒读，并保持错误/只读操作后的文件哈希。
- 13 个不同目标配置和实际 4KiB/8KiB 页大小差异，在目标原内容变化前拒绝备份。
- 真实 native query 被独立线程 interrupt 后可复用；progress 取消、事务回滚、空 SELECT、
  trigger 分号、绑定文本、先前 authorizer 恢复与敏感错误信息隔离。

恢复后的同字节回归在原实现暴露两项真实副作用：RETURNING 被拒绝时首行已写入，
以及公共 rekey 可把 keyed 目标变成明文而保留旧标记；修正后这两项通过。另一个回归
证明准备期 foreign_keys pragma 原先会在多语句错误前改变状态，加入 native tokenizer
预检后通过。原红色 JUnit/log 与跨算法失败探针均保留在私有 artifacts，不回填。

一个独立 PyInstaller adapter probe 已实际构建和运行，包含本适配模块、APSW extension
与发行包 metadata；检查真实两行、空 SELECT 元数据、加密备份、错误 key 和标准库拒读，
自有临时目录已清理。打包需显式 hidden-import `apsw`、`apsw.ext` 并 copy-metadata
`apsw-sqlite3mc`。这不是 OpenNavicat 整包、安装器或其余五平台的运行证据。

参考应用本轮实际写出的一个 AES256 文件也已由本适配层只读重开：quick_check 为 ok，
精确两行符合预期，错 key 报 SQLite 26，标准 SQLite 拒读；原件和只读副本 SHA256
都未变，副本无 sidecar 且已精确清理。该反向记录只覆盖 aes256cbc，不推广为全部标签
的参考写出格式；参考 UI 的其他互读与 Ascon legacy 观察由专门记录分别承担。

路径无关的摘要与源码/测试/收据哈希见
[适配 evidence](evidence/sqlite-cipher-adapter-3.54.0.0.json)。本 worktree 的私有原始证据
位于 `artifacts/sqlite-cipher-provider-10h/`；14 份 provider 原件仍由原 manifest 管理，
本叶没有删除、修改或覆盖供参考互读使用的文件。

## 后续接入门禁

UI 配置与密码保存/提示、稳定 secret binding、原子附件初始化与取消代次、只读会话、
附件算法追踪与备份、完整 rekey/转换、WAL/恢复、许可证通知，以及六目标原生构建、
整包 smoke 和完整产品验收均保留开放。此叶不是完整 DBAPI 替换或 SQLite 加密工具验收。

## 0.14 预检复杂度与取消修正

独立复核发现原预检对引号内每个分号都重新扫描前缀，100KB 输入可把取消延迟到数秒。
下一里程碑先修正该适配层：线性跳过字符串、quoted identifier、注释内的分号以及前导
空分号；只有剩余候选交给 SQLite complete 验证，仍由 scratch authorizer 检查尾部，
不改变 trigger 与准备期 PRAGMA 的多语句拒绝合同。SQLite 对这些 token 的定义见
[complete API](https://www.sqlite.org/c3ref/complete.html)。

对含大量 trigger 内部候选的输入，累计 UTF-8 前缀扫描量以连接实际
`SQLITE_LIMIT_SQL_LENGTH` 为预算；超出时在真实连接 prepare 前给出明确的
`SQLPreflightLimit`，完整 SQL 不截断。此为当前实现的复杂度边界，不是参考输入限制。
Python 扫描每约 1024 字符及每个候选检查取消事件与已注册 progress callback；内部
原生 progress handler 也保留同一取消事件，覆盖 Python 预检与 native execute 之间的
竞态。下一次新 execute 清除旧操作事件，取消后连接可复用。

本修正专项集合为 **102 项通过、零跳过/失败/error**，新增 100KB quoted/comment/
leading-noop 输入的单次前缀扫描、所有 quote 形式与真实尾部、trigger 预算耗尽前不写、
Python 预检期间独立线程取消、进度回调在 prepare 前取消及取消后复用。
原 d4b9103 的独立失败/性能探针仍保留，不用新结果覆盖历史证据。

`connect` 与 `configure_attachment` 现在显式接受 `page_size=0`，非零仅传给已选
legacy cipher 的 `legacy_page_size`。参数按上游
[PRAGMA 合同](https://utelle.github.io/SQLite3MultipleCiphers/docs/configuration/config_sql_pragmas/)
配置后，还检查真实 main page_size，不能以参数回显当应用成功。本机 61 个受支持的
legacy/非零页大小组合均真实创建、关闭重开、同配置备份后 quick_check/两行、错误密钥
及标准库拒读；8 个 legacy 模式另实际绑定 ATTACH 8192 字节页文件。

探针发现 SQLCipher legacy v2/v3/v4 请求 512 时参数回显 512，实际文件却为 1024。
这 3 个组合现于打开任何文件前明确拒绝，原 3 项红色 JUnit/log 保留。其余结构非法值
也在打开前拒绝。含此扩展的专项集合为 **178 项通过、零跳过/失败/error**。
这些非零参数尚未在参考 UI 执行验收，不能据此关闭参考行为门禁。

原 [89 项收据](evidence/sqlite-cipher-adapter-3.54.0.0.json) 的源码、冻结包和测试哈希
对应独立适配层 d4b9103 快照；其冻结验证不覆盖后续预检或页大小修正。

为后端 main 备份新增 `open_backup_target(database, key=...)`。它从可信 keyed main
读取实际 cipher/legacy/page_size，目标须由调用方先独占保留；factory 用 existing-only
打开，先拒绝非空文件，再配置同格式及调用方显式 key。返回时文件仍为零字节，调用方
可在 copy 前再次检查文件归属，不会先把外来替换物化成加密 header。

正常模式也可有非默认实际页大小。此 target factory 通过空目标的 SQLite page_size
正式参数保留实际值，而不把 UI 的 legacy 参数擅自应用于正常 cipher。14 个配置的
空目标备份均实际重开/quick_check/两行/错 key/标准库拒读；另 5 个正常 cipher 的独立
8192 字节页原件经过相同目标路径后仍可读取。非空或未保留的目标先失败，既有内容
不变。含此扩展专项为 **198 项通过、零跳过/失败/error**。

0.14 原子导入清理再增加固定 rollback/savepoint API。停止 progress callback 只解决
Python 取消标记妨碍清理的问题，不保证任意外部 held reader 下原生 interrupt 已退休。
独立探针确认：未耗尽 reader 保持 busy 时 rollback 可报 SQLITE_INTERRUPT，关闭 reader
后再 rollback 成功。当前产品会话在 finally 关闭各 cursor 后才做异常事务清理；本适配
层仍不宣称全 DBAPI held-cursor 兼容。
