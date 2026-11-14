# SQLite 加密文件实际互读与新建边界

2026-11-14，Asia/Shanghai。指定参考为 Navicat Premium **18.0.5 build 321**，执行文件
SHA256 `3857a354fde7f42f8afbf9ef3a25956e6ff3b9745f598f77bbab1d27e7493bd1`。
本页继续 [SQLite 多附件与高级选项观察](sqlite-connection-runtime-2026-11-14.md)。这是
本机实际参考行为，不是 OpenNavicat 已集成加密、十五种格式可用或三平台验收通过。

## 方法与证据限制

独立开源 provider `apsw-sqlite3mc==3.54.0.0` 的本机实际版本为 SQLite 3.54.0、Multiple
Ciphers 2.6.1。它生成十四份自有文件，均有 `generated_facts(id, label)` 两行；参考只
使用唯一临时目录中的 0400 副本。没有复制、加载或调用 Navicat 数据库库文件。

只复用唯一生成连接 `Generated SQLite cipher read 20261114-1517`。每轮关闭后修改准确
文件与 cipher，常规页、高级页保存密码均关闭，设置位置也在本轮目录。重新打开时输入
合成口令，然后运行：

```sql
SELECT id, label FROM main.generated_facts ORDER BY id;
```

实际结果单元格为两行：`1 / generated owned row`、`2 / generated ' quote; SELECT only 样例`。
第二行的引号和 SQL 样文字是数据。未打开任何既有连接；含其它未打开连接名的窗口状态
仅在工具会话观察，不导出或放入 README、仓库、安装包。

Desktop 更新前使用 CUA，更新后使用 Computer Use 的 `node_repl + @oai/sky`。本页 UI
事实不冒充新的 REA UI Evidence ID；此前静态身份 Evidence
`ev_846e9ea175edf1704b793616ecb392f08ab2b4a2cb208917890948fc63f9418d` 仍只
绑定文件身份，其会话已导出并关闭。逐项回执、所有权和哈希保留在本工作树忽略目录
`artifacts/cipher-reference-10h/`，不会随 Git clone 自动恢复。

新工具初次只以 AX `set_value` 改 combo 时，显示文本改变，但重新编辑仍是原 RC4 legacy，
导致 AES128 打开报 26。改用真实键入并按 Tab 提交后，legacy 设置行随算法改变，正确
配置可打开并读取。这次自动化输入失败保留，不能当作 provider 不兼容。

## 十四个标签：provider 到参考的实际两行读取

| 参考标签 | 匹配文件打开与 SELECT |
| --- | --- |
| aes128cbc | 两行精确匹配 |
| aes256cbc | 两行精确匹配 |
| chacha20 | 两行精确匹配 |
| sqlcipher | 两行精确匹配 |
| rc4 | 两行精确匹配 |
| ascon128 | 两行精确匹配 |
| aes128cbc legacy | 两行精确匹配 |
| aes256cbc legacy | 两行精确匹配 |
| chacha20 legacy | 两行精确匹配 |
| sqlcipher legacy v1 | 两行精确匹配 |
| sqlcipher legacy v2 | 两行精确匹配 |
| sqlcipher legacy v3 | 两行精确匹配 |
| sqlcipher legacy v4 | 两行精确匹配 |
| rc4 legacy | 两行精确匹配，恢复后另重做一次 |

此前未保存草稿的十四次 Test 均成功、实际版本为 3.45.0；AES128 的错误合成口令、改用
AES256 cipher 两个 Test 对照均报 `26 - file is not a database`。Test 成功与两行读取分开。
十四个标签不等于十四种独立格式：provider 的 RC4 与 RC4 legacy 实际参数和文件字节相同。
实际行读取后，十四原件及十四副本 SHA256 全部未变，未出现数据库 sidecar。

## Ascon legacy 的真实失败

对正常 Ascon 文件改选 `ascon128 legacy` 后，打开失败文字是
`Fail to configure the database encryption.`，不是错误口令 26，也不证明它是 normal 的别名。

另用本轮不存在的新路径选择新建 SQLite3 和 Ascon legacy，点击配置“好”时警告：

```text
Fail to create SQLite database file.
Fail to configure the database encryption.
```

此尝试留下 0 字节新文件。关闭警告并取消编辑，持久 profile 仍指向原有效 AES256 文件且
关闭，失败新配置未保存。保留候选及失败合同，不删除参考入口，也不推广为全部 Ascon
legacy 输入必然失败。provider 严格 legacy 探针不支持该模式是另一条独立事实。

## AES256 反向：参考生成，provider 独立读取

复用同一个已有生成 profile，设置此前不存在的新文件、新建 SQLite3、加密和 AES256，
两个保存密码保持关闭。新建模式下 Test 禁用。点击配置“好”后直接回到主窗口；在本次
动作与检查点间没有再输入口令，也没有操作额外口令提示。立即检查首次发现已创建的
4096 字节文件，头部不是普通 SQLite 明文头。随后显式打开才弹口令提示，重新输入相同
合成口令可打开。此前同一 profile 已多次输入该口令；不猜测内部缓存，不把这段时序
推广到全新 profile 或没有历史口令的情况。成功创建后重开编辑，类型已自动变为现有文件。

仅在该新文件执行显式 BEGIN、创建 `generated_facts`、插入两条生成值、COMMIT，随后
限定 main 查询；参考结果为 `(1, generated reference one)`、`(2, generated reference two)`。
关闭会话后文件 8192 字节，无 sidecar，SHA256：
`90cc026172037e5eae4f11a5fe89c617b65d524f7dbcc906a32f6df8dca17d68`。

独立 provider 只读复制后取得 `quick_check=ok`、两行精确值、错误口令拒绝、普通 SQLite
拒读，原件哈希未变；其临时副本也已清理。这是一个 macOS AES256 反向实例，不表示
其余十三标签均完成反向写出验证。

## 控件与清理

后续另以新进程中的全新未保存草稿核对初始控件，见
[fresh 默认值与排布](sqlite-connection-fresh-defaults-2026-11-14.md)。其中 cipher 在加密
开启前后均为空白，不能从本页复用 profile 的历史选择推断默认算法。

高级保存密码关闭时，实际画面仍有 disabled 安全文本框和占位点，不能将此前“隐藏”描述
作为精确合同或把遮蔽点当实际密码。常规页关闭保存密码时未在 AX 列出密码框，两者
分开；打开连接仍重新弹口令提示。不能仅据控件状态声称系统钥匙串和所有内部缓存无写入。
legacy 标签另显示“密码旧版页面大小”，本轮值 0，未枚举范围或执行其它值。

最终在明确命名确认框等待用户当次授权后，只删除本轮唯一生成连接，UI 数量 17→16。
正常 Quit 后 `list_apps` 确认参考未运行。provider 反向读取完成后，重新核对已登记的
十四副本、AES256 反向文件、Ascon legacy 零字节文件、Settings 三个缓存文件，无 symlink
或未知项，再精确删除 19 个 regular 文件和两个目录。目录均不存在；十四 provider 原件
保留且哈希相同，参考执行文件哈希未变。不声称全局偏好或所有历史完全没有变化。

OpenNavicat 的 provider、UI、凭据及完整工作流尚须集成。加密附件、算法继承、密码策略、
进程重启、完整参数、取消/并发变化、Windows/Linux 实际参考继续开放；C01/U08/U15 和
完整功能门禁不因本页关闭。
