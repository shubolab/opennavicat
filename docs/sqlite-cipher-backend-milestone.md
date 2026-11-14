# SQLite 加密后端接入中的验收边界

此为 0.13.3 之后的下一里程碑，尚未发布，不等于完整 SQLite 工具或完整 Navicat parity。
配置模型以原样标签保存 enabled/cipher/page size，主密码及其 carrier 不写 JSON；附件
密码绑定 owner/file/encrypted/cipher/page size。旧 encrypted 三元 carrier 不自动升级。

主库或任一附件显式启用加密才加载可选 MC provider；全普通 SQLite 保留标准 sqlite3。
普通 main 加密附件通过独立 `connect_plain_main` 打开，MC 中的普通附件显式绑定空 KEY，
避免继承加密 main 的密钥。所有 key 和 filename 都是绑定参数，alias 按 SQLite quote。
metadata、当前 bound keys、全部路径先校验，初始化与所有附件 catalog 检查成功后才
发布 handle；取消或任一失败均关闭未发布 candidate，沿用原取消代次合同。

附件执行时使用全局 cipher/page size 是当前受控配置规则，尚未实测参考的继承规则；
此门禁保持开放。空 cipher 原样保存，执行选择 AES128 来自参考正负控的受控推断，
不是内部实现证明。Ascon legacy 与静默钳制的 SQLCipher legacy2/3/4 + 512 组合明确失败。

`DatabaseSession.create_sqlite_database(config, cancellation_requested=None)` 是 worker
API。仅接受 writable/create/物理文件，先验证完整配置，独占新目标，拒绝已有 sidecar；
成功完成初始化与物化 header 后关闭。取消或失败只删除本次保留且 inode 仍匹配的目标，
不会删除既有文件或外部替换。回调为 callable，不触碰任何 Qt widget。

首阶段专项共 **149 项通过、零跳过/失败/error**：48 个新 native backend workflows、
41 个既有 plain execution 回归、60 个 model 参数化测试。14 支持标签实际创建、写入、只读重开、错 key、标准库
拒读；14 标签普通 main 配加密附件；另覆盖 mixed key、绑定、alias、只读附件、PK/
冲突/原子导入、候选取消/关闭、创建失败/取消/外部替换及 native query 取消后复用。

此提交先把 MC 备份在目标创建前明确关闭，后续只接可信 main 同算法/实际页大小路径。
附件备份、完整 rekey/格式转换、参考非零页大小行为、WAL/恢复、平台完整工具行为与
六目标 native 安装器/整包 smoke 仍保持开放。provider 的独立专项和冻结证明见各自记录，
不据此宣称本后端已在其余 OS/架构运行。
