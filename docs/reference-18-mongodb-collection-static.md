# Navicat 18 MongoDB 普通集合设计：编译界面资源补证

本稿记录 Downloads 中 Navicat Premium 18.0.5（build 321）的定向静态调查，
用于安排普通集合设计器的后续实测与实现。**本轮没有启动或操作原版，没有创建集合，
没有读取用户连接配置，也没有执行目标包内脚本或数据库命令。以下内容不是普通集合设计器
已经运行、保存成功或达到 1:1 的证明。**

此前 [M18-02 / M18-03](ui-reference-18-mongodb.md)
记录了实际对象列表、信息栏及右键的新建／设计入口；这两项没有执行普通集合设计的保存。
此前取得的独立索引设计器和时序集合线索也不能替代普通集合的行为证据。

## 身份、方法与可复核范围

源为 `/Users/wangshubo/Downloads/Navicat Premium.app`。复制的 `Info.plist` 由 REA
识别为 `com.navicat.NavicatPremium`、18.0.5、321；其 SHA-256 为
`3350c306fb699d0af18b1baf5de569640be232cd36106e80eb56370162d0f9b2`，
与[既有同版身份记录](reference-18-static.md)一致。本轮为 resource-only session，
REA 的 executable digest 为 `null`，没有打开原版可执行文件进行代码分析。

既有 R18-IB 只覆盖原根前 64 份档案，R18-F 为另一组 29 份定向档案，均没有完整覆盖
本次目标。因此将下列 11 份 NIB 和原 `Info.plist` 逐字节复制到忽略目录中的
`focus.app`。逐文件核验原文件与副本 SHA 相同后，对该**派生资源子集**进行 REA 解码。
没有可执行文件或厂商图像随子集复制；编译档案、完整图和派生 JSON 均未提交至源码。

| 引用 | REA Evidence ID | 说明 |
| --- | --- | --- |
| R18-MC-P | `ev_d49db98d65a48bf7b872523e6b18efb716334389c1f02d2e75138d8686d1d846` | 派生子集中逐字节相同的 Info.plist |
| R18-MC-IB | `ev_c3689d56e350df3d64a5ff9e34ec4ad4c572a16e442ffda9887ff8dbebe08c98` | 11 份 NIB 的识别对象、层级与连接图 |
| R18-MC-S | `ev_536067de0be510f1fe9a862415bbab36aff8e5985b09dc99b928cbdd42f9dfaa` | 图的计数、覆盖、限制概要 |
| R18-MC-D | `ev_d0c03702402a1a639edd569af1894d1c25ab7b5999d898d87080c222cec73931` | 11 份文档的 SHA、对象数及层级覆盖分页 |

原始导出与推导材料位于本调查工作树被忽略的 `artifacts/mongo-collection-static/`：
`rea-collection-resources.json`、`copy-manifest.json`、`collection-ui-derived.json`、
`verification.json`。派生 JSON 仅在**同一 NIB 内**沿 `objectID` 解析 outlet 目标、
cell 的 `NSContents` 和本地化对象的 `NSDev`，没有把数字 flags 翻译为推测的控件状态。

解码识别出 1,709 个节点和 1,533 条边；11 份选定档案均进入图，但图仍标记
`truncated=true`。45 个覆盖 facet 中 32 个 complete、13 个 partial；6 份档案共有
7 个未投影的层级连接，另有 8 条因不支持的连接种类而未解析目标的边。
这里的完整仅针对列出的输入档案，
不能推导整个应用没有其他模块、动态创建的页面或运行时重排。

## 模块与控件：已知的是资源归属，而非最终页签

下列名称均为 `Contents/Resources/Base.lproj/` 下的实际资源名。每个模块的 File's
Owner 对象 ID 为 `2`；它们通过 `view` 等 outlet 指向自己的视图。资源名和各自 view
只证明存在这些编译组件，**没有证明它们在“新建常规集合”或“设计集合”中全部可达，
也没有证明最终页签的文字、数量、顺序和条件启用规则**。

| 资源 | 直接识别的控件或内容 | 尚不能从该档案确定的部分 |
| --- | --- | --- |
| `TableDesignFieldsModule_mongodb.nib` | `_fieldTableView`（364）、`_searchView`（423）、`_documentTypePopUpButton`（293）；枚举、最小／最大值、长度、对象属性数、数组项数、Pattern、Exclusive Minimum/Maximum、Unique Items 输入 | 实际字段表列、BSON 类型菜单、预定义对象选择、空集合首行与默认值 |
| `TableDesignCollectionOptionModule_mongodb.nib` | capped、clustered、容量与文档数、过期、change-stream pre/post images 及旧存储选项，详见下节 | 服务器版本适用范围、普通／时序集合差异、已有集合中是否可改、选项初始值及联动 |
| `TableDesignValidationModule_mongodb.nib` | Validation Level、Validation Action、Validator Type；编辑器及设计视图容器 | 实际 level/action/type 选项、是否及何时嵌入 JSON Schema 组件、初始化与验证失败行为 |
| `TableDesignForeignKeysModule_mongodb.nib` | `_fkTableView`（8）与模块 view（59） | 实际列与编辑规则、虚拟关系／数据库约束含义，以及在普通集合设计中是否显示 |
| `TableDesignIndexesModule_mongodb.nib` | `_indexTableView`（134）；Unique、Background、Sparse、TTL 和 Bucket Size | 实际索引表列、旧／新服务器条件、与独立索引设计器的关系及保存次序 |
| `TableDesignIndexOptionModule_mongodb.nib` | `_eeEditor` → `NEEEditorView`（8），模块 view（14） | 该编辑器承载的实际语言、内容、父页面和提交动作 |

实际目录中未发现名为 `TableDesignGeneralModule_mongodb.nib` 的档案。不能把它当成已识别
的普通集合“常规”页，也不能由文件缺失推导该运行时页面不存在。时序集合有独立的
`TableDesignTimeSeriesGeneralModule_mongodb.nib` 与
`TableDesignTimeSeriesCollectionOptionModule_mongodb.nib`；独立索引有 `IndexDesign*`
资源，本次均未混入普通集合子集。

## 集合选项与具体 action 关系

`TableDesignCollectionOptionModule_mongodb.nib` 中，owner 2 的 outlet 指向以下对象。
这里的英文词是 Base 档案的 `NSDev`，并非本轮观察到的中文运行界面。

| outlet / 对象 ID | 编译资源标签或用途 | 直接关联的 action |
| --- | --- | --- |
| `_cappedCollectionCheckbox` → 13 | Capped Collection | 此子集没有识别出该控件的 target-action；不能据此推断没有代码联动 |
| `_maxSizeInBytesTextField` → 47 | Max Size (Bytes) | delegate 等静态连接不能证明输入校验规则 |
| `_maxNumOfDocTextField` → 67 | Max No. of Documents | delegate → owner 2 |
| `_clusteredCollectionCheckbox` → 103 | Clustered Collection | 103 → owner 2 的 `ibClusteredCollection:` |
| `_clusteredIndexNameTextField` → 123 | Clustered Index Name | delegate → owner 2 |
| `_enableExpireAfterSecCheckbox` → 181 | Expire After Seconds | 181 → owner 2 的 `ibExpireAfterSeconds:` |
| `_expireAfterSecTextField` → 136 | 过期秒数输入 | delegate → owner 2 |
| `_changeStreamPreAndPostImagesCheckbox` → 210 | Change Stream Pre and Post Images | 210 → owner 2 的 `ibChangeStreamPreAndPostImages:` |
| `_autoIndexIdCheckbox` → 223 | Auto Index Id | 实际服务器上的可见性与含义未知 |
| `_usePowerOf2SizeCheckbox` → 236 | Use Power of 2 Sizes | 实际服务器上的可见性与含义未知 |
| `_noPaddingCheckbox` → 249 | No Padding | 实际服务器上的可见性与含义未知 |

这些 selector 是档案中的声明，**没有证明二进制存在对应实现、执行时读取哪些输入，
或最终发出何种 MongoDB command**。旧存储选项出现在档案中也不代表 MongoDB 8 实际支持。

## 验证与 JSON Schema 的分层线索

`TableDesignValidationModule_mongodb.nib` 将 `_validationLevelComboBox` 连到 31、
`_validationActionComboBox` 连到 86、`_validationTypePopUpButton` 连到 112。
112 的 target-action 是 owner 2 的 `ibValidationType:`。
`_eeEditor` 指向 `NEEEditorView`（162），另有 `_validationTypeDesignView`（150）、
`_queryView`（172）和 tab bar/tab view（198/182）。这些容器关系可以定位未来实测入口，
不能证明某个菜单值会切换到某个编辑器。

该模块只编码了一个标签为 `Query` 的 `NSTabViewItem`（193），且
`NSSelectedTabViewItem` 指向 193。类型 popup 的资源是 `Item 1`、`Item 2`、`Item 3`，
其中第一项编码 `NSState=1`。因此本轮**没有取得运行时 validator type 列表或实际默认值**。
Validation Level/Action 的 combo cell 文本为空；不能填入推测的 `strict`／`error` 默认值。

另两份资源提供 JSON Schema 编辑组件的独立证据：

- `JsonSchemaDesignModule_mongodb.nib`：`_propertiesTableView`（17）包含标识为
  `propertyName`、`bsonType`、`required`、`title`、`description` 的列，相应 Base 标签为
  Name、BSON Type、Required、Title、Description。自身 tab view（130）有 Properties
  （174）与 Root Schema（181）项，归档的 selected item 指向 Properties。没有跨资源
  连接能证明它在普通集合设计中的实际嵌入位置或运行时默认选中项。
- `JsonSchemaGeneralPropertiesDesignModule_mongodb.nib`：owner outlets 覆盖 BSON Type、
  Enum、Multiple Of、上下界与 exclusive 开关、字符串长度与 pattern、对象属性数、
  Required／Properties／Additional Properties／Pattern Properties、Items／Additional
  Items、数组项数、Dependencies、Unique Items、Title、Description。它们是独立组件的
  输入面，不足以证明每个字段在每种 BSON 类型下都可见或可编辑。

`DocumentDesignFieldsModule_mongodb.nib` 另有字段约束表和 Comment 输入。该命名与普通
集合的 `TableDesignFieldsModule_mongodb.nib` 不同；本次没有证据将两者视为同一个页面，
也没有把此资源存在等同于已实现“文档设计”工作流。

## 归档初值的边界

可以复核的归档值包括：集合容量／文档数／clustered 名称输入 cell 的 `NSContents=""`；
字段约束多个输入 cell 同样为空；上述 Properties、Query tab 的 selected-item 引用；
两个类型 popup 的 `Item 1` 占位。相关多个控件编码 `NSEnabled=true`。

本稿不从 `NSCellFlags`／`NSButtonFlags` 的位值猜测 checkbox 默认勾选。
即使存在明确的空文本、selected-item 或 enabled 值，也只描述解码前的编译档案；
`awakeFromNib`、模型装载、服务器能力、用户偏好和动态布局均可能改变它们。
因此不能将归档空值说成新建集合的产品默认值，也不能将 enabled 值说成用户一定可操作。

## 当前实现与静态证据的边界

分支中的首个普通集合设计器 UI 已接入创建、验证器、collation、原始 BSON、精确脚本预览、
删除/清空永久确认、重命名、时间序列创建/设计、集合复制、验证/重建索引、worker-only 保存和取消路径；实现和测试见
[普通集合设计器 checkpoint](mongodb-collection-designer-backend.md)。Linux WSL 的独立
owned-Mongo runner 当前 98 项零跳过，但尚未在 Windows/macOS 运行参考应用执行本设计器。
因此它只证明 OpenNavicat 自身行为，不关闭本稿列出的原版 tab 顺序、默认值、字段/索引页、
保存确认与失败/取消状态门禁。

## 保存、命名与脚本预览：仍缺运行链

通用 `NAVSavePanel.nib` 的 owner 是 `__NAVSavePanelWindowController`（2）。它有
`_nameTextField`（40）、`_nameLabel`（52，Base 标签 Object Name）、
`_nameViewContainer`（38）、`_customControlViewContainer`（22）、`_warningLabel`（73），
以及 window（114）。Cancel（8）和 OK（29）分别连接到 owner 的
`ibCancelButtonAction:`、`ibOKButtonAction:`。资源中的 name cell 为空而 OK 编码 enabled；
这不能证明运行时允许空名，也不能证明此通用面板被普通集合保存调用。

`SQLPreviewModule.nib` 只识别出 owner 的 `_sqlPreviewModuleView` 与 `view` 都指向
`SQLPreviewModuleView`（8）。本次 11 份资源的图没有建立从普通集合模块到通用保存面板、
再到预览模块的可验证调用链。没有从 selector 名推导 command，也没有找到可证明集合
保存确认、取消、预览 suppression 或失败后状态的完整流程。

因此，此次仍未确认：新建名称 Sheet 是否及何时出现、普通集合脚本的实际语法、预览
刷新时机、取消是否保留草稿、已有集合哪些变更允许保存、失败／权限／只读／断开／重开
的行为。独立索引已实测的保存偏好不自动扩展为普通集合证据。

## 下一次动态核验范围

恢复可靠原版应用句柄后，应仅在新建自有 marker 集合中依次观察：入口打开后的实际主
tab 与页签；空集合与已有集合的所有字段和初始状态；验证 type/level/action 的真实选项
及 schema/query 切换；集合选项在当前服务器上的启用条件；索引与虚拟关系组件是否出现；
名称／最终脚本预览／取消／保存，以及独立 wire 核验与确切清理。
该实测完成前不应据本稿预造并声称 1:1 的页签、默认值或保存语义。

REA Evidence 已导出，随后执行 `close_binary`，`binary_session` 确认
`open=false`、`cleanup_pending=false`。本轮没有深反编译器、注入、原版 UI 操作或数据库
操作；没有改动产品代码、依赖、版本、发布标签或完整功能门禁状态。
