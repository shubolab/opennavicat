# SQLite 加密 provider 可行性实验

2026-11-14；本轮只在独立本地 worktree 的物理 Python 3.13.11 venv 安装 provider。
没有更改 OpenNavicat 产品依赖、标准 SQLite backend 或 UI。没有复制、加载或分发
Navicat 的库。完整有限收据见
[provider evidence](evidence/sqlite-cipher-provider-3.54.0.0.json)。

## 供应物与打包

[官方 APSW SQLite3MC 项目](https://github.com/utelle/apsw-sqlite3mc) 是 SQLite3 Multiple
Ciphers 项目列出的 [Python binding](https://utelle.github.io/SQLite3MultipleCiphers/docs/installation/install_overview/)。
本次 [PyPI 元数据](https://pypi.org/project/apsw-sqlite3mc/3.54.0.0/) 的精确版本为
`apsw-sqlite3mc==3.54.0.0`，Python 要求 `>=3.10`；实际 macOS arm64 模块报告
APSW 3.54.0.0、SQLite 3.54.0、SQLite3 Multiple Ciphers **2.6.1**。上游 GitHub 当前
README 的 2.7.0 示例不能替代已下载 wheel 的实际版本。

已下载六个 CPython 3.13 wheel，逐字节核实 PyPI size/SHA256，并检查核心扩展文件头：

| 目标 | wheel 平台标签 | 实际核心扩展文件头 |
| --- | --- | --- |
| macOS amd64 | macosx_10_13_x86_64 | Mach-O x86_64 |
| macOS arm64 | macosx_11_0_arm64 | Mach-O arm64 |
| Linux amd64 | manylinux_2_28_x86_64 | ELF x86-64 |
| Linux arm64 | manylinux_2_28_aarch64 | ELF AArch64 |
| Windows amd64 | win_amd64 | PE32+ x86-64 |
| Windows arm64 | win_arm64 | PE32+ AArch64 |

这是六目标二进制供应物的静态核查，实际执行只有本机 macOS arm64。
没有把 wheel 当安装器或其他五目标运行证据。

包的导入名是 `apsw`。本次另下载普通 `apsw==3.54.1.0` 的同平台 wheel 作静态比较，
发现 86 个 `apsw/` 文件路径重叠，包括核心扩展；普通 APSW 未安装。产品应明确只选
一个发行包，不能同时安装后依赖覆盖顺序选择 provider。当前产品未依赖 APSW。

独立 PyInstaller 6.22.3 onedir probe 已在本机实际构建和运行：冻结后的 arm64 进程
成功创建 AES256 加密文件、用正确密钥重开、由普通 sqlite3 拒读、只读操作保持文件
哈希，最后删除自有临时目录。核心扩展的本机依赖只有系统 `libSystem`。这证明一个
最小 provider probe 可冻结，不证明 OpenNavicat 整包、其余五平台或安装器已经通过。
wheel 还含大量 `sqlite_extra_binaries`，不能因安装了发行包就自动开放这些扩展能力。

## 真实文件实验

按本轮实际参考列出的 15 个 cipher 标签尝试配置，执行 **14 组成功、1 组明确不支持**：

- 六个基本标签：aes128cbc、aes256cbc、chacha20、sqlcipher、rc4、ascon128。
- aes128cbc/aes256cbc/chacha20 的 legacy=1，SQLCipher legacy=1/2/3/4，RC4 legacy=1。
- `ascon128 legacy` 无法应用：设置及读取 `legacy` 都返回 None，实验在写入前拒绝；
  新建的空文件已精确删除，不能把未知 pragma 的无操作当加密支持。

14 组均实际创建含两个生成样例行的加密文件、关闭后重开并通过 quick_check；错误
密码拒读；只读连接可读而写入被拒；标准库 sqlite3 拒绝读取 catalog；主库为普通
SQLite 时可绑定 filename 和 KEY 执行加密 ATTACH，并查询两个 schema；错误 ATTACH
密钥被拒。所有只读和错误路径后，加密文件与普通主库 SHA256 均保持不变。主文件
前 16 字节都不含普通 SQLite header；此事实与正确读取/错误读取结果合用，不单独
根据 header 宣称加密正确。文件、表名与数据全部新生成，没有使用现有用户数据库。

RC4 基本项与 legacy 项实际都报告 legacy=1，本次甚至生成相同的文件 SHA256；它们
不能被统计成两种已证明独立的格式。新 provider 的 RC4 默认值是否对应参考两标签的
区别，仍需实际互读。

主密钥通过 APSW 正式 `Connection.pragma` 的 value 参数设置；该 API 专门处理不支持
SQL parameter binding 的 PRAGMA，并避免将密钥 pragma 放入 statement cache。
实验没有自行拼接密码 SQL。ATTACH 使用
`ATTACH DATABASE ? AS generated_aux KEY ?`，文件和 KEY 都绑定值，别名是固定生成名。
文件名含空格和单引号，样例字段含引号与中文，仍按绑定值处理。密钥仅由 stdin 进入
进程内存，没有写入 key 文件、URI、收据或产品配置。

上游明确说明 [key 返回 ok 尚不验证密码](https://github.com/utelle/apsw-sqlite3mc#usage)，
必须真正读取已填充数据库。当前较新 SQLite 在 key 激活前可能因 SELECT 访问文件，
导致 [SQL 配置函数不适用于 main 的初次密钥配置](https://utelle.github.io/SQLite3MultipleCiphers/docs/configuration/config_sql_functions/)。
接入时应保持 cipher、legacy、key、真实 catalog 读取的顺序，并检查每一步返回值。

## 许可证与接入建议

wheel 的 dist-info 只带 APSW 的 zlib 风格许可及可选 any-OSI 条款；SQLite3MC 自身声明
[MIT](https://github.com/utelle/SQLite3MultipleCiphers/blob/main/LICENSE)，SQLite 原始核心
声明公有领域。精确 sdist SHA256
`60a021cd9f6c8f3e2aa6cb54e197cb344379d5eb9a688b7647a136fc24956db4` 的 amalgamation
还保留多个组件通知，例如 Argon2/Blake2 的 CC0 或 Apache-2.0 选项，以及 MIT 和公有
领域的 cipher 实现声明。因此只复制 wheel 的 APSW LICENSE 不构成完整的产品通知
整理；接入前需按精确供应物保留所有适用通知。本记录是已观察声明清单，不是完整法律
审查或声明未来分发的许可证工作已完成。

此 provider 是可继续集成的候选，但 APSW 不是标准库 sqlite3 的直接 DBAPI 替换。
项目依赖 cursor description/fetchmany/rowcount、异常类型、事务、getlimit、独立取消
和 backup 等合同；必须先有实际 provider 的专项适配验证，再接标准 backend。
尤其应保留普通 SQLite 无加密路径、默认只读/不意外创建文件、原子附加与取消代次，
禁止 backup 在目标 provider 不可用时静默退回明文 sqlite3。

剩余预算内适合先做一个受测适配叶及小范围真正互读，不适合据本实验直接宣称完整
SQLite/Navicat 加密支持。下一门禁包括参考实际创建和读取、双向正确/错误密码、每个
legacy 版本与参数、附加库算法继承、Ascon legacy、空密码、密码保存/重启、WAL/恢复、
备份加密、取消和六目标原生整包。当前 **尚无 Navicat 互读结果**，任何同名 cipher 或
静态 `libsqlite3mc` import 都不能替代实际互读。

私有完整证据在本 worktree 的 `artifacts/sqlite-cipher-provider-10h/`，其摘要已由上述
JSON 绑定。14 份新加密副本暂留 `reference-owned-fixtures/` 供后续自有参考实验，副本
与实验成功文件逐字节相同；实验停止后按该 manifest 精确清理，不能清理其他数据库。
