# 2026-11-11 旧工作树集中归档与清理

本记录补充 [工作快照](2026-11-11-handoff.md)。用户明确授权归档有用测试/参考证据，删除已整合且没有未提交改动的旧目录，保留六处未提交内容。
本次仅做仓库维护，没有继续功能开发、构建、数据库测试或正式发布；完整 Navicat 目标仍未完成。

## 最终目录状态

**工作树从 27 个收敛为 8 个，已移除 19 个 clean、detached 的旧工作树。**
Git 分支仍只有 `main`、`work/mongodb-collections-next`。

| 保留目录 | 原因 |
| --- | --- |
| `opennavicat` | main 主工作树，含 0.9 候选与快照；最新公开安装包仍为 0.8.1 |
| `opennavicat-mongodb-next` | 单一后续 MongoDB 集合开发工作树 |
| `opennavicat-connection-manager` | 原有未提交内容保留 |
| `opennavicat-driver-dialogs` | 原有未提交内容保留 |
| `opennavicat-navicat-import` | 原有未提交内容保留 |
| `opennavicat-object-designers` | 原有未提交内容保留 |
| `opennavicat-redis-adapter` | 原有未提交内容保留 |
| `opennavicat-sqlserver-adapter` | 原有未提交内容保留 |

六个 dirty 目录的 HEAD、status、staged/unstaged diff、untracked 文件字节均在删除前后核对一致。
没有把这些未审查内容合进 main，也没有 reset、清理或移动其原目录。

已移除的 clean 目录及其可恢复源码提交：

| 原目录 | HEAD |
| --- | --- |
| `opennavicat-combo-native` | `c03846432790` |
| `opennavicat-connection-lifecycle` | `918d95328179` |
| `opennavicat-integration` | `39598f5e405f` |
| `opennavicat-js-engine` | `723b227c786a` |
| `opennavicat-js-index-helpers` | `82831724bb02` |
| `opennavicat-js-integration-candidate` | `36071bec1fc8` |
| `opennavicat-linux-discovery` | `e5f81b11326a` |
| `opennavicat-linux-integration` | `34619a79b2d9` |
| `opennavicat-mongo-cancel` | `6f46795c7a4a` |
| `opennavicat-mongo-collection-reference` | `d6ec9ea1c48f` |
| `opennavicat-mongo-collections` | `c9f536aef83e` |
| `opennavicat-mongo-index-designer` | `ec8eb4cf7a3a` |
| `opennavicat-mongo-selection` | `3c0a2c592189` |
| `opennavicat-mongo-tree` | `e3e6b19e0a8b` |
| `opennavicat-mongodb` | `cd189a01eaae` |
| `opennavicat-next` | `e7b0e1ac986d` |
| `opennavicat-next-09` | `92079b928f41` |
| `opennavicat-port-native` | `5e7f0bd10fbb` |
| `opennavicat-windows-discovery` | `a0a5dd2f414e` |

## 集中归档位置与内容

本机仓库外的私有目录：

```text
../opennavicat-archive/2026-11-11-worktrees/
```

相对于 `/Users/wangshubo/develop/opennavicat`；绝对路径为
`/Users/wangshubo/develop/opennavicat-archive/2026-11-11-worktrees`。
归档根权限 **0700**，原始证据没有推送到 GitHub。

| 归档内容 | 位置 |
| --- | --- |
| 各来源完整测试/参考/CI/冻结程序证据 | `worktrees/<原工作树名>/artifacts/` |
| main 已有发布材料副本 | `worktrees/opennavicat/release-assets/` |
| 六处未提交文件的附加备份 | `uncommitted/<原工作树名>/files/` |
| 对应原 HEAD 的二进制 Git diff | `uncommitted/<原工作树名>/{staged,unstaged}.patch` |
| 清理前完整 Git 历史 | `source-history.bundle` |
| 原路径、HEAD、文件 SHA/mode/链接及状态 | `inventory-initial.json`、`manifest.json` |
| 说明和来源计数 | `README.md`、`archive-index.json` |
| 清理前独立完整性/恢复证明 | `independent-verification-before-removal.json` |
| 清理后的目录/dirty 保全及归档验证 | `worktree-cleanup-verification.json`、`post-removal-archive-verification.json` |

共归档 **6882 个常规文件、480 个符号链接、5,051,429,617 字节**，覆盖 25 个来源工作树的证据/备份。
另外保存六处修改的 12 份 staged/unstaged patch、Git bundle 和清单/校验脚本。
Git bundle 为 **6,931,409 字节**，SHA256：

```text
ef3c7ac8ede95d7f31322bfc5520bc22d19041bbf4f3f98f37b5aeda1a903225
```

bundle 冻结在目录清理前，包含原 27 个 HEAD、版本 tag 和分支归档 tag；之后的快照文档提交从远端 main 取得。
证据归档包含完整私有参考收据及测试证书/密钥等原始资料，不应将本机归档直接提交或公开上传。
公开 Git 只保存本说明、计数、摘要和恢复入口。

## 校验和删除边界

- 19 个候选的提交全部由既有分支归档 tag 可达，且产品改动已通过 main/next 或有记录的 cherry/squash 整合。
- 18 个候选有 artifacts 根，全部保留；`opennavicat-mongo-cancel` 没有 artifacts，只有可再生成缓存。
- 按相对路径双向核对完整证据树，逐文件核 SHA256/大小/mode、symlink 原始文字；副本与源不是同 inode，也不跟随外链充当归档。
- 首次独立校验发现 134 个 symlink 副本使用默认权限，已仅修正归档副本的 link mode 并重验；首次失败记录仍在私有归档内。
- 从 bundle 独立 bare clone，确认原 27 个 HEAD 均能恢复；删除后再次运行不依赖原目录的归档校验。
- 只使用精确 19 目录名单逐项 `git worktree remove`；没有通配删除。
- 不保留证据根之外的可再生成 Python/pytest 缓存、Finder 文件和指向 main 环境的 `.venv` 外链。main 的环境、build/dist 仍在原处。

移除前检查没有发现候选目录的进程 cwd/可执行映射/打开文件、内部挂载、特殊 socket/FIFO 或 Docker bind mount 依赖。
原版 Navicat、main 的旧运行应用、Navicat 挂载镜像、两个此前保留的参考服务及其 Docker volumes 均未清理。
Dell/WSL 延期验收现场未动。

## 恢复与继续

日常继续使用 main 与 `opennavicat-mongodb-next`，不再依赖已删除的旧目录。
历史证据中的路径按这个规则查找：

```text
旧：<旧工作树>/artifacts/<相对路径>
新：<集中归档>/worktrees/<旧工作树名>/artifacts/<相对路径>
```

例如冻结 0.9 本机资格在 `worktrees/opennavicat-next-09/artifacts/0.9-integration/`；
集合 53 项资格在 `worktrees/opennavicat-mongo-collections/artifacts/mongo-collections/checkpoint-final/`；
静态 REA 补证在 `worktrees/opennavicat-mongo-collection-reference/artifacts/mongo-collection-static/`。
main 中的正式发布验证资料原位置仍保留，集中归档中另有副本。

在主工作树校验归档：

```sh
.venv/bin/python ../opennavicat-archive/2026-11-11-worktrees/verify_saved_archive.py
```

若需复现旧提交，先用精确 SHA 新建一个隔离工作树，再从集中归档恢复其 artifacts；
具体示例见本机集中归档 `README.md`。源码环境按项目 README 重建，不复用已删除目录的 `.venv` 链接。
六处未提交修改依赖各自原 HEAD，恢复或审查时先核对清单，不将备份批量套到 main。

原分支名称与 SHA 的恢复索引仍为 [branches.json](2026-11-11-branches.json)，
Git 恢复归档 tag `archive/2026-11-11-branch-consolidation` 保持不变。
