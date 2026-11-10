# 实现结构

OpenNavicat 使用 Qt 6 Widgets 原生桌面控件，Python 负责工作流编排和驱动适配。
应用不需要浏览器、Web 服务或 Navicat 安装。SQLite 使用 Python 内置驱动，
PostgreSQL 使用 psycopg 3，MySQL/MariaDB 使用 PyMySQL。

```mermaid
flowchart LR
    W[Qt 工作台 / SQL / 表格] --> Q[串行后台操作队列]
    Q --> D[DatabaseSession]
    D --> S[SQLite]
    D --> P[PostgreSQL]
    D --> M[MySQL / MariaDB]
    W --> C[独立取消队列]
    C --> D
    W --> L[本地配置 / 草稿 / 历史]
    L --> K[操作系统密钥服务]
    D --> R[字段 / 索引 / 外键元数据]
    R --> G[原生关系图]
    D --> V[数据差异预览]
    V --> A[事务 + 原始行冲突校验]
    A --> D
```

- `__main__.py` 初始化平台应用、样式、工作区锁及独立的 smoke 生命周期。
- `ui/window.py` 连接树、SQL/表标签、动作状态、后台任务和事务交互。
- `ui/editor.py` SQL 高亮、行号、选区、查找和已有对象名称补全。
- `ui/grid.py` 数据模型保留原始值，草稿变更与真实写入分开；生成列保持只读。
- `ui/dialogs.py` 原生连接表单、表设计 SQL 预览和带 NULL/default 区分的记录输入。
- `database.py` 方言引用、参数绑定、对象元数据、会话事务、取消及原子写入。
- `compare.py` 有界差异计划，绑定目标配置和表结构；应用时复查绑定与行冲突。
- `ui/diagram.py` Qt 图形场景绘制真实表与外键，无数据库写入。
- `storage.py` 原子配置文件、SQLite 查询历史及 OS keyring；密码不会写入配置。
- `transfer.py` CSV / JSON 交换，保留 NULL、重复 SQL 列名及 JSON 二进制表示。

所有 Qt 控件访问在 GUI 线程，数据库操作在串行后台队列，保证共享连接的事务
不会被两个界面操作并发使用。取消走独立工作队列，可中断正在执行的查询。
表浏览、行编辑、导入和比较不拼接用户值；SQL 编辑器按用户输入执行原始 SQL。

返回行数限制是结果展示约束。PostgreSQL 普通游标底层可能缓存服务器完整结果，
因此不能把它描述为服务端执行工作量或整个进程内存的硬上限。

当前完整功能范围、后续能力与平台验收分别记录在 `roadmap.md` 和 `validation.md`。
本机 Navicat 的静态资源调查只作为产品需求证据，不作为本应用运行依赖或行为证明。
