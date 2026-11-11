# Windows Navicat Premium 18.0.6：静态参考身份

调查日期：2026-11-11，Asia/Shanghai。用户在当前 Windows 环境安装了
`C:\Program Files\Navicat\Navicat Premium 18\navicat.exe`，并要求继续用 REA 与 Computer Use
核对实际参考。本稿只记录已完成的静态身份检查；Computer Use 在本轮因 Windows runtime 拒绝
WSL 工作区 URI 而未执行，因此**没有据此宣称任何 18.0.6 运行界面或行为已观察**。

## 安装身份

| 项目 | 观察值 |
| --- | --- |
| 注册表显示名称 | Navicat Premium 18.0 |
| 注册表 DisplayVersion | **18.0.6** |
| FileVersion | **18.0.6.0** |
| ProductVersion | 18.0 |
| 安装路径 | `C:\Program Files\Navicat\Navicat Premium 18\` |
| 主程序 | `navicat.exe` |
| 大小 | **110,135,368 bytes** |
| SHA-256 | `23e325a375a53b0443f6a29dd34e932ec0d89dc13ffc463c1a2fc3e07071212f` |
| PE 格式 | PE32+ / amd64 |
| imphash | `94133673806777b0d7b9bbd87f2dabaa` |

以上不是 macOS 18.0.5 build 321 的身份替代。18.0.5 仍是已有 macOS 运行观察的来源；
Windows 18.0.6 是新增的平台特定静态参考。跨版本和跨 OS 的页面、默认值、快捷键及行为差异
必须分别观察。

## REA Evidence

| 引用 | Evidence ID | 范围与边界 |
| --- | --- | --- |
| R18-WIN-DIR | `ev_18473b2ceb07acbcecbe1f342c43bd0646d2e553ea4bc84474b1e5e4606b451f` | 安装目录完整扫描范围内 187 个条目、181 个探测文件；目录 digest 只承诺保留路径、元数据和读取范围。 |
| R18-WIN-DIR-S | `ev_83b93593fa99cdffa35302a80390d0822e32cb34d2f3b13ecb9984b6bc3f9947` | 上述记录的 summary 视图；未证明运行时加载。 |
| R18-WIN-PE-I | `ev_e825274388604623d2b4d2d34ee552356e5e4927e0a4273aeff106fbc5fb997d` | 45 个 import DLL、1,335 个 imports、652 个 delay imports；不证明绑定或可达性。 |
| R18-WIN-PE-R | `ev_7acb238cc5565cea3a86c4c304fcf802349e020fa2651e18cbd7fa10c9e49974` | 5,782 个资源目录条目、2,886 个资源完整枚举；除 group icon 外资源 payload 不透明。 |

REA 目录扫描限制包括：目录 digest 不是全部文件内容的哈希、不是原子快照、symlink 不跟随、
特殊条目 payload 不读取，运行时行为和未读字节仍为 unknown。PE import 和资源清单是交付产物事实，
不是 Navicat 已执行这些路径的证明。

## 下一步运行观察门禁

Windows Computer Use 恢复后，只使用本次新建的 SQLite 临时库和全新连接名，依次记录：
主窗口、九个数据库族入口、新建连接所有页与默认值、工具栏/菜单/快捷键、对象列表列和右键动作；
随后观察普通/时序 MongoDB 集合设计、查询与结果区、对象设计器、导入导出/传输/同步、备份/自动运行，
以及模型、BI、AI provider、Profiling、Masking、Dictionary 等入口。每个动作同时保存 UI 状态与可独立的
数据库/文件副作用，不能把静态资源或旧 macOS 观察当作 Windows 18.0.6 运行通过。
