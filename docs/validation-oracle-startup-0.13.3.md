# Oracle 0.13.3 启动诊断跟进

2026-11-14；基线 `adf9221ecac6b51d27ccc56cea9acc6d4d8cc819`。

## CI 失败的实际边界

[`run 43603141977`](https://github.com/shubolab/opennavicat/actions/runs/43603141977)
的两个 Oracle native 任务均在 `fixture_start/readiness` 失败。amd64 与 arm64 分别约
61.7 和 70.2 秒后观察到自有容器退出，exit code 24、非 OOM；有限日志各记录一次
`ORA-04021` 和 `ORA-65048`。14 项 backend 用例仅完成 collection，未执行；window
范围被跳过。两个任务的精确容器清理与临时密码清理通过，不能把这些失败改记为通过。

小产物的 GitHub artifact ID 分别为 `14116863650`（amd64）与 `14116619621`（arm64）。
原始容器日志未持久化，现有代码摘要不足以证明是哪一条 SQL 导致退出。

## 本次改动

保持现有 360 秒 readiness 预算、镜像 pin、所有权检查、进程清理和零跳过合同，
不添加自动重试或吞掉启动失败。只对本来就会读取的有界日志增加固定词典分类：
数据库启动、密码重设、用户脚本、数据库 ready、启动失败；SQL*Plus 回显密码语句时，
仅保留 `sys` 或 `system` 枚举，不保存密码、SQL、数据库名或自由文本。
未知或伪造的分类在导出边界被拒绝。

上游 [gvenzl issue 35](https://github.com/gvenzl/oci-oracle-free/issues/35) 记录过
首次密码重设时同一组错误；其当前 [resetPassword 实现](https://github.com/gvenzl/oci-oracle-free/blob/main/resetPassword)
也执行两条密码 DDL。这只提供调查方向，不证明本轮 CI 的锁持有者或失败语句。
需新的失败回执含阶段/回显分类，或另有实际复现后才能确定处理方法。

## 本机验证与限制

物理 `.venv`，Python 3.13.11。`test_oracle_runner.py` 第二轮 68 项通过，包括有限日志
分类和凭据不外泄回归。第一轮有三个既有真实子进程极短 deadline 用例失败，具体为
success、closed-pipe、已退出 leader 的 closed grandchild；未修改这些用例的超时或
进程实现，同源码第二轮通过，不能据此推断第一轮失败原因。

随后在已有 OrbStack 的原生 Linux aarch64 引擎上，使用既有 pin 的全新 owned fixture，
实际 Oracle 23.26.3 backend 14 项零跳过通过。客户端运行于 macOS arm64；这不是
Linux CI 客户端、amd64 或参考 Navicat UI 的验收。启动完成约 75.7 秒、35 次尝试，
没有复现退出 24。人工附加的有限日志检查见到 database_start/password_reset/ready，
未保存原始日志。新分类代码没有改变启动或测试行为，因此不能称为修复了锁问题。

仅自有容器 `8a2e94795c9f37764455c74ce5eda825324b2815663f8635d6d84b6e8542ce30`
被创建和移除；独立精确 ID inspect 确认不存在，receipt 的 tmpfs/secret scan/cleanup
均通过。没有操作其他容器或数据库。

私有证据位于本地 worktree 的 `artifacts/oracle-ci-resume-10h/`：

- `diagnostic-arm64/verification.json` SHA256
  `8efbd98a52f3e51de65a673b62adf5ae3532666805dcb110ab7bbda0d4695c04`。
- `runner-tests-attempt2.xml` SHA256
  `44d4d6b849d19e04dac3bf8b91c4205604e69c66fcf209bb192999d86159a686`。

后续仍需主线的新原生 amd64/arm64 Oracle backend/window 运行；此记录不关闭 Oracle
完整功能或跨平台门禁，也不授权用现有六目标安装任务成功直接发布 0.13.3。
