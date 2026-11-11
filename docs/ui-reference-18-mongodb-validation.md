# Navicat 18 MongoDB 验证器运行观察

日期：2026-11-11 至 2026-11-12，Asia/Shanghai。参考为用户指定的
`/Users/wangshubo/Downloads/Navicat Premium.app`，实际身份为 **18.0.5 build 321**。
本轮在 macOS ARM64 上，通过原版 UI 操作一个新建、认证、仅 loopback 的原生
ARM64 MongoDB **8.0.34** 服务；只使用合成数据库、集合、字段和值。

下列 `MV18-*` 是 **CUA 实际运行观察编号，不是 REA Evidence ID**。
独立 PyMongo 读取和写入仅用于核对服务器结果，没有代替原版 UI 操作。
没有读取或导入既有连接配置、打开业务连接、修改产品账户或激活状态。
没有将原始参考截图、界面档案或代码加入 Git、README 或安装包。
普通集合七页结构另见[普通集合设计器观察](ui-reference-18-mongodb-collections.md)。

## MV18-01：验证页、属性表与初始状态

新建普通集合的“验证”页显示：

| 控件 | 新建初始显示 | 实际展开选项 |
| --- | --- | --- |
| 验证级别 | 空白组合框 | `off`、`strict`、`moderate` |
| 验证动作 | 空白组合框 | `error`、`warn` |
| 验证符类型 | `JSON Schema` | `JSON Schema`、`Query` |

服务器的隐式默认值不能当成组合框显示值。JSON Schema 下有“属性、根模式”两子页，
初始为属性。工具条为“保存、添加属性、删除属性、进入/退出专注模式”；
Query 下不显示添加/删除属性。没有调用“询问 AI”。

属性表在前导图标列之后依次为 **名称、BSON 类型、必需、标题、描述**。
“添加属性”增加未保存的可编辑空行；“删除属性”移除选中的草稿行。
名称、标题、描述可编辑，“必需”为复选框。表下用分隔器放当前行的约束编辑区。
本轮草稿加/删没有自行修改服务器；实际写入均经过保存及脚本预览 Sheet。

本轮曾出现仅修改未激活单元格的 AX 显示值、但最终脚本仍保持原类型的状态；
组合框输入的前缀也可能自动补全为较长类型名。因此类型观察以**实际选中行、
刷新后的约束区和最终脚本**交叉确认，没有把 `setValue` 成功作为模型提交证据。

## MV18-02：完整 BSON 类型菜单及逐类型约束区

实际展开的顺序为：

```text
array, binData, bool, date, dbPointer, decimal, double, int,
javascript, javascriptWithScope, long, maxKey, minKey, null,
number, object, objectId, regex, string, symbol, timestamp,
undefined, (Multiple)
```

本轮为前 22 项各建立一个独立的合成属性行，逐行选择并核实类型及约束区；
以下分组来自实际逐项观察，没有将未切换的类型推断为相同。

| 已逐项选择的 BSON 类型 | 行下约束区的实际控件 |
| --- | --- |
| `array` | 枚举、项目、附加项目、最大项目、最小项目、唯一项目复选框 |
| `decimal`、`double`、`int`、`long`、`number` | 枚举、多个、最大、独占最大值复选框、最小、独占最小值复选框 |
| `object` | 枚举、最大属性、最小属性、必需、属性、附加属性、模式属性、依赖性 |
| `string` | 枚举、最大长度、最小长度、模式 |
| `binData`、`bool`、`date`、`dbPointer`、`javascript`、`javascriptWithScope`、`maxKey`、`minKey`、`null`、`objectId`、`regex`、`symbol`、`timestamp`、`undefined` | 仅枚举 |

新行的约束值为空；实际观察到的独占边界和唯一项目复选框初始未勾选。
界面中的“多个”对应本轮生成脚本的 `multipleOf`。
共享的必需、标题、描述仍在表格内，不属于上述类型专属约束区。

另用独立客户端为 22 种别名分别创建最小 validator，MongoDB 8.0.34 全部接受；
这仅证明该服务器接受 schema 类型声明，不证明原版已完整编辑或写入每种 BSON 值。

### `(Multiple)` 的独立类型选择器

选中 `(Multiple)` 后，行下显示“BSON 类型、枚举”，BSON 类型旁的省略号打开
标题为 **“BSON 类型”** 的模态表格。表格以复选框列出上述 22 种类型，
有“全选、取消全选、好、取消”；本轮初始没有勾选类型。

本轮勾选 `int`、`null`，确认后实际预览为
`bsonType: ["int", "null"]`，而不是字符串 `"(Multiple)"`。
该数组随后真实保存、独立读回；整数及 null 写入成功，字符串写入被拒绝。

## MV18-03：根模式及原始片段编辑入口

根模式实际顺序为：**最大属性、最小属性、附加属性、模式属性、依赖性、标题、描述**。
最大/最小属性为输入框，其余五项均有省略号。
本轮逐个打开这五个省略号，均出现通用标题 `Window`、带行号的 Scintilla 编辑区、
“好、取消”。模式属性、依赖性、标题、描述的模态窗口均取消，没有提交内容。
不能将“标题/描述也使用该编辑窗口”扩展为已验证了其所有转义及输入语义。

附加属性输入 `false` 后点“好”，根模式显示该片段；实际更改预览生成
`$jsonSchema.additionalProperties: false`。这次仅检查草稿，未保存到服务器。
本轮没有证明根模式全部约束的保存、边界和错误行为。

对象行的“属性”、数组行的“项目”“附加项目”、字符串行的“枚举”省略号，
也分别实际打开同样的 Scintilla 模态编辑器。观察到的是**原始片段输入**，
没有出现递归的属性树编辑器。其后实际输入、预览、保存结果见 MV18-06。
对象行中其它省略号没有逐个打开，仍保留为后续门禁。

## MV18-04：JSON Schema 新建、命名、取消与服务端边界

原版 UI 新建了 `score` 属性：BSON 类型 `int`、必需勾选、标题
`Generated score`、描述 `Owned integer boundary`，多个为 5、最大为 100、
最小为 0，独占边界均未勾选；显式选择 strict/error。
实际脚本预览的 validator 为：

```javascript
{
    $jsonSchema: {
        properties: {
            score: {
                bsonType: "int",
                title: "Generated score",
                description: "Owned integer boundary",
                multipleOf: 5,
                maximum: 100,
                minimum: 0
            }
        },
        required: ["score"]
    }
}
```

新建保存 Sheet 的名称标签实际为 **“表名称:”**；空名称保存禁用。
Sheet 同时显示完整创建脚本和默认未勾选的“不再显示脚本预览”。
第一次命名后取消，独立读取确认没有创建该集合；草稿仍可再次保存。
第二次命名为 `schema_ui_probe` 并核实精确创建脚本，最终保存成功，标题更新，
更改预览显示“无更改”。没有修改脚本预览偏好。

独立 catalog 确认 validator、strict/error 均真实保存；原版脚本的裸数字在服务器
读回为 BSON double（例如 `5.0`、`100.0`、`0.0`）。独立写入结果：

| 合成文档条件 | 结果 |
| --- | --- |
| `score` 分别为整数 0、5、100 | 3 次成功 |
| 缺少 `score`、字符串 `"5"`、整数 -5、105、3 | 5 次拒绝，均 code 121 |

此阶段没有立即关闭重开整数 Schema；后续 Query 及更复杂 Schema 的真实重开另有记录。

## MV18-05：Query 切换、解析错误、服务器错误、取消和重开

在已有 Schema 草稿上切换为 Query，实际 Query 编辑区初始为空，没有自动显示
该 Schema 的等价 Query 文本。之后切回 Schema 时，曾恢复先前的 `score` Schema
编辑内容。本轮另用独立客户端创建的 `$jsonSchema` 集合首次打开时，Query 区可见原始
validator，但切换 JSON Schema 后属性表为空。因此不能假设切换总会双向转换、合并，
也不能由本轮推断其全部缓存或持久化元数据规则。

实际完成以下路径：

1. Query 输入 `{broken` 后切到预览，原版报
   `[Error] SyntaxError: missing : after property id`，并显示行列位置；
   关闭错误后保留输入和已编辑状态，没有服务器写入。
2. 改为 `{score: {$gte: 20}}`，预览生成 `collMod: "schema_ui_probe"` 和相同 validator。
   已有集合的保存 Sheet **没有名称输入框**，仍显示脚本及保存/取消。
   点取消后独立 catalog 仍为先前 JSON Schema。
3. 改为 `{score: {$unknown: 1}}`，此文本能进入真实保存 Sheet；最终保存后服务器报
   `[2][BadValue] Parsing of collection validator failed :: caused by :: unknown operator: $unknown`。
   关闭错误保留该输入与已编辑状态；独立读取确认旧 Schema、集合 UUID 和已有文档保留。
4. 再改回 `$gte: 20` 并保存，成功后 Query 文本重新格式化。独立读取为
   `validator: {score: {$gte: 20.0}}`、strict/error；原 UUID 和原有 3 条文档保留。
   整数 20 新写入成功，19 被拒绝（121），文档数为 4。
5. 关闭设计页，从对象列表“设计集合”重新打开，先显示索引页；转到验证页后实际为
   **Query**，格式化后的 `$gte: 20`、strict/error 均保留。

这证明本轮验证修改走 `collMod` 并保留对象及历史文档；不能据此宣称所有权限、
并发修改、所有 Query 操作符或错误恢复均已覆盖。

## MV18-06：嵌套数组、对象、字符串及多类型真实保存/重开

在自有 `type_catalog` 的 JSON Schema 编辑模式，通过原版 UI 建立 22 个合成属性行。
本轮明确替换该测试集合的 validator；没有把外部原始 Schema 当成已被属性表自动导入。
除单类型声明外，实际通过省略号编辑器、输入框和复选框构造：

```javascript
{
    p_array: {
        bsonType: "array",
        additionalItems: false,
        items: {
            bsonType: "object",
            required: ["code"],
            properties: {code: {bsonType: "string"}}
        },
        maxItems: 4,
        minItems: 1,
        uniqueItems: true
    },
    p_object: {
        bsonType: "object",
        properties: {child: {bsonType: "string", minLength: 2}}
    },
    p_string: {
        bsonType: "string",
        enum: ["AA", "BB"],
        maxLength: 4,
        minLength: 2,
        pattern: "^[A-Z]+$"
    },
    p_undefined: {bsonType: ["int", "null"]}
}
```

上述是完整 22 行中的四个已验证片段，不是完整 validator。
名称 `p_undefined` 只是合成字段名；它在本轮从 undefined 改为 Multiple。
最终更改预览及保存 Sheet 均包含该精确集合的 `collMod`、22 属性和 strict/error；
最终保存成功，更改区为“无更改”。独立 catalog 对完整对象逐项核对一致。

独立写入两个有效文档：一个同时包含合规嵌套数组、对象、枚举字符串和 null，
另一个只含整数多类型字段。随后 12 个无效样例均返回 code 121：

- 数组为空、超过 4 项、重复元素、元素缺少必需的 code、code 类型为整数。
- 对象 child 字符串过短、child 类型为整数。
- 字符串值分别为 `CC`、`A`、`AAAAA`、`aa`。
- 多类型字段值为字符串。

字符串四个反例同时可能违反 enum；它们**没有分别隔离证明** minLength、maxLength、
pattern 的执行原因。这三项的脚本、保存读回和 UI 重开值已核对，独立边界测试仍需补。
`additionalItems: false` 也只证明写入及读回，未隔离测试它在各 items 形态下的效果。

关闭设计页，再从“设计集合”重开后，验证页实际选择 **JSON Schema**，
strict/error 与全部 22 行均恢复；逐项选择数组、对象、字符串时，嵌套原始片段、
4/1/唯一项目、child schema、枚举、4/2/模式均恢复，多类型行仍显示 `(Multiple)`。
这与 MV18-05 的那次外部 Schema 模式切换是两个实际不同的状态，不将其概括为统一转换规则。

## 私有收据、清理和仍开放的门禁

独立创建、catalog、写入结果和精确清理收据保存在 Git 忽略的
`artifacts/reference-validation-20261111/fixture.json`；目录名沿用开始日期。
没有导出原始参考截图或既有业务信息。未开启 REA 分析会话。

本轮复用先前唯一合成参考配置，**连接前**将 endpoint 改为新建并核验的自有服务；
保存密码保持未勾选。结束时关闭本轮设计页、断开该连接，实际显示 Off、会话 0。
该配置仍保留且未保存密码，没有再积累连接配置；以后必须重新核验 endpoint，
不能直接连接已失效的旧端口。

清理前再次核验唯一容器 ID、owner 标签、loopback 映射、无用户指定挂载、
25 个生成集合及 1/4/2 条样例文档计数，确认取消命名的集合不存在、两个最终 validator
及 UUID 与此前读回一致。删除该精确容器及其由 Mongo 镜像自动创建的两个匿名卷，
确认容器 ID 和这两个卷均不存在。没有清理其它服务或代理的测试环境。

本轮新增证据覆盖了一个普通 MongoDB 8.0.34/macOS 状态下的 Schema/Query 主路径，
**没有关闭完整验证工具或跨平台 UI 门禁**。仍需保留：

- 根模式全部值、对象其它省略号、各原始片段编辑器的非法输入/取消/错误恢复。
- off/moderate/warn 的实际执行语义、清空验证器、特殊字符/转义和各 BSON 值编辑。
- 数值独占边界、所有数字类型的精度与溢出，以及字符串独立边界。
- 所有 schema 组合、未知关键字、外部 validator 载入/转换及往返保真规则。
- 权限不足、并发外部修改、其它 MongoDB 版本/拓扑/集合类型。
- Windows、Linux 原版对照，以及 OpenNavicat 的功能与 UI 实现验收。

原版行为记录不能替代 OpenNavicat 的实现、测试或平台验收；上述未知项不能因本轮主路径成功而移除。
