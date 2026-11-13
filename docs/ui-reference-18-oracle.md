# Navicat 18 Oracle runtime observations

## OR18-01 Basic connection, settings and owned table/view browsing

On 2026-11-13, the actual macOS reference **18.0.5 build 321** was observed
through CUA. A new generated Oracle profile connected only to a new disposable
native Linux ARM64 Oracle AI Database 26ai Free **23.26.3.0.0** instance. A
separate controller created a generated application schema, one table and one
view with synthetic data. Existing profiles, TNS files, wallets and databases
were not opened or edited. This is a custom live observation ID, not a REA
Evidence ID or OpenNavicat Oracle acceptance.

### Connection form

The new Oracle form has a collapsible connection-profile pane, the main profile,
an add-profile button, a connection diagram and five page controls in this
order: **常规 / 高级 / 数据库 / SSH / 备注**. Bottom controls include the
profile-pane toggle, URI, Test Connection, Cancel, Back and OK; editing a saved
profile omits Back. No separate SSL page appeared in this observed form. This
does not establish the absence of Oracle TLS or other connection paths.

| Page/control | Actual observed options and initial state | Execution boundary |
| --- | --- | --- |
| General | Connection name; connection type **基本 / TNS**; Basic host, port **1521**, value **ORCL**, **服务名称 / SID** radios (service selected); role **默认 / SYSDBA / SYSOPER**; OS authentication unchecked; username/password; Save Password initially checked | Actual successful reference connection used Basic, service, Default and password only. Other modes remain unexecuted in Navicat. |
| TNS selection | Basic host/port/service controls were replaced by a **网络服务名称** combo | Selected and observed only; no existing TNS configuration was accessed. An earlier click-only attempt did not establish a stable form and was discarded. |
| OS authentication | Checking it removed the editable username/password fields from AX and disabled Save Password | Switched on and back off without executing this authentication mode. |
| Advanced | Settings location and browse control; Auto Connect off; connection-session limit off (disabled field shows **0**, AX placeholder **10**); keepalive off, disabled **240** seconds; client character set visually **自动**; Initial Query plus an editor/browse control | Settings location and Initial Query were not edited. Session/keepalive/charset/query behavior remains open. |
| Database | Custom database list off; disabled list with active/name/auto-open columns and visible add/remove/refresh controls | Enabling it on an earlier blank-host, unsaved form left a busy list. That form was cancelled; successful custom-list retrieval and filtering were not established. |
| SSH | Use SSH Tunnel off; host, port **22**, username; methods **密码 / 公钥 / 密码和公钥**. Public Key exposes private key, passphrase and Save Passphrase (off). Combined mode adds password and Save Password | All three method forms were opened, then SSH was switched off. No tunnel, key or external SSH endpoint was used. |
| Notes | Multiline text and a notice that changes apply to all connection profiles | Observed only; no note was saved. |

The client character-set menu exposed one automatic/blank AX selection and
**247 distinct named choices**. Its complete observed named order is retained
below; the menu is a declaration, not validation of each encoding or support by
a proposed Thin adapter.

```text
AL16UTF16 AL24UTFFSS AL32UTF8 AR8ADOS710 AR8ADOS710T AR8ADOS720
AR8ADOS720T AR8APTEC715 AR8APTEC715T AR8ARABICMAC AR8ARABICMACS AR8ARABICMACT
AR8ASMO708PLUS AR8ASMO8X AR8EBCDIC420S AR8EBCDICX AR8HPARABIC8T AR8ISO8859P6
AR8MSWIN1256 AR8MUSSAD768 AR8MUSSAD768T AR8NAFITHA711 AR8NAFITHA711T AR8NAFITHA721
AR8NAFITHA721T AR8SAKHR706 AR8SAKHR707 AR8SAKHR707T AR8XBASIC AZ8ISO8859P9E
BG8MSWIN BG8PC437S BLT8CP921 BLT8EBCDIC1112 BLT8EBCDIC1112S BLT8ISO8859P13
BLT8MSWIN1257 BLT8PC775 BN8BSCII CDN8PC863 CE8BS2000 CEL8ISO8859P14
CH7DEC CL8BS2000 CL8EBCDIC1025 CL8EBCDIC1025C CL8EBCDIC1025R CL8EBCDIC1025S
CL8EBCDIC1025X CL8EBCDIC1158 CL8EBCDIC1158R CL8ISO8859P5 CL8ISOIR111 CL8KOI8R
CL8KOI8U CL8MACCYRILLIC CL8MACCYRILLICS CL8MSWIN1251 D7DEC D7SIEMENS9780X
D8BS2000 D8EBCDIC1141 D8EBCDIC273 DK7SIEMENS9780X DK8BS2000 DK8EBCDIC1142
DK8EBCDIC277 E7DEC E7SIEMENS9780X E8BS2000 EE8BS2000 EE8EBCDIC870
EE8EBCDIC870C EE8EBCDIC870S EE8ISO8859P2 EE8MACCE EE8MACCES EE8MACCROATIAN
EE8MACCROATIANS EE8MSWIN1250 EE8PC852 EEC8EUROASCI EEC8EUROPA3 EL8DEC
EL8EBCDIC423R EL8EBCDIC875 EL8EBCDIC875R EL8EBCDIC875S EL8GCOS7 EL8ISO8859P7
EL8MACGREEK EL8MACGREEKS EL8MSWIN1253 EL8PC437S EL8PC737 EL8PC851
EL8PC869 ET8MSWIN923 F7DEC F7SIEMENS9780X F8BS2000 F8EBCDIC1147
F8EBCDIC297 HU8ABMOD HU8CWI2 I7DEC I7SIEMENS9780X I8EBCDIC1144
I8EBCDIC280 IN8ISCII IS8MACICELANDIC IS8MACICELANDICS IS8PC861 IW7IS960
IW8EBCDIC1086 IW8EBCDIC424 IW8EBCDIC424S IW8ISO8859P8 IW8MACHEBREW IW8MACHEBREWS
IW8MSWIN1255 IW8PC1507 JA16DBCS JA16DBCSFIXED JA16EBCDIC930 JA16EUC
JA16EUCFIXED JA16EUCTILDE JA16EUCYEN JA16MACSJIS JA16SJIS JA16SJISFIXED
JA16SJISTILDE JA16SJISYEN JA16VMS KO16DBCS KO16DBCSFIXED KO16KSC5601
KO16KSC5601FIXED KO16KSCCS KO16MSWIN949 LA8ISO6937 LA8PASSPORT LT8MSWIN921
LT8PC772 LT8PC774 LV8PC1117 LV8PC8LR LV8RST104090 N7SIEMENS9780X
N8PC865 NDK7DEC NE8ISO8859P10 NEE8ISO8859P4 NL7DEC RU8BESTA
RU8PC855 RU8PC866 S7DEC S7SIEMENS9780X S8BS2000 S8EBCDIC1143
S8EBCDIC278 SE8ISO8859P3 SF7ASCII SF7DEC TH8MACTHAI TH8MACTHAIS
TH8TISASCII TH8TISEBCDIC TH8TISEBCDICS TR7DEC TR8DEC TR8EBCDIC1026
TR8EBCDIC1026S TR8MACTURKISH TR8MACTURKISHS TR8MSWIN1254 TR8PC857 US7ASCII
US8BS2000 US8ICL US8PC437 UTF8 UTFE VN8MSWIN1258
VN8VN3 WE8BS2000 WE8BS2000E WE8BS2000L5 WE8DEC WE8DG
WE8EBCDIC1047 WE8EBCDIC1047E WE8EBCDIC1140 WE8EBCDIC1140C WE8EBCDIC1145 WE8EBCDIC1146
WE8EBCDIC1148 WE8EBCDIC1148C WE8EBCDIC284 WE8EBCDIC285 WE8EBCDIC37 WE8EBCDIC37C
WE8EBCDIC500 WE8EBCDIC500C WE8EBCDIC871 WE8EBCDIC924 WE8GCOS7 WE8HP
WE8ICL WE8ISO8859P1 WE8ISO8859P15 WE8ISO8859P9 WE8ISOICLUK WE8MACROMAN8
WE8MACROMAN8S WE8MSWIN1252 WE8NCR4970 WE8NEXTSTEP WE8PC850 WE8PC858
WE8PC860 WE8ROMAN8 YUG7ASCII ZHS16CGB231280 ZHS16CGB231280FIXED ZHS16DBCS
ZHS16DBCSFIXED ZHS16GBK ZHS16GBKFIXED ZHS16MACCGB231280 ZHS32GB18030 ZHT16BIG5
ZHT16BIG5FIXED ZHT16CCDC ZHT16DBCS ZHT16DBCSFIXED ZHT16DBT ZHT16HKSCS
ZHT16HKSCS31 ZHT16MSWIN950 ZHT32EUC ZHT32EUCFIXED ZHT32SOPS ZHT32TRIS
ZHT32TRISFIXED
```

### Actual connection and object workflow

The generated profile used loopback and this fixture's dynamically published
port, the **FREEPDB1** service and its generated application-schema user. Save
Password was unchecked. Unchecking it cleared the inline password field: Test
Connection requested the password in a separate dialog. The actual Test then
displayed **连接成功** and the 26ai Free server version. The profile was saved,
reopened through Edit Connection and checked for preserved host, port, service,
Default role, OS-auth off and Save Password off. No saved credential is claimed.

Opening the saved connection requested the password again. One **ORA-12545**
warning appeared during this sequence. After closing the warning the new
profile was actually **On**, with one session and **30 schemas**. The warning's
source is unknown: an earlier cancelled blank-host custom-list fetch also
existed. Neither that fetch nor the successful saved connection is assigned as
its cause. No listener, server or reference configuration was changed to make
the warning disappear. Separate fixture-side Thin reads remained successful;
absence of a corresponding listener-log string does not establish attribution.

Only the generated schema was opened. Its navigation exposed **表 / 视图 /
实体化视图 / 函数 / 查询**. Selecting Table and waiting for completion listed
one generated table; selecting View and waiting listed one generated view.
Initial transient zero-object counts were not treated as completed results.
Both were opened as actual data tabs and read one synthetic record. AX retained
the exact displayed large integer **9007199254740993**, a generated Chinese
label and **1234.5**. The unobscured view screenshot confirms all three displayed
values. A first-use AI Autofill coach partly obscured the table screenshot;
it was not an executed AI request. This is displayed-value evidence, not proof
of Navicat's private numeric types, every decimal value, all Oracle types or
mutation behavior.

The table tab displayed an owner-qualified SELECT with quoted identifiers,
`ROWID "NAVICAT_ROWID"`, `OFFSET 0 ROWS FETCH NEXT 1000 ROWS ONLY`; the view tab
displayed a qualified SELECT without that extra ROWID. These are observed UI
SQL strings, not a complete command trace. Table tools included Table Profile,
Begin Transaction, Cell Editor, Filter & Sort, Columns, History, AI Autofill,
Data Analysis, Import, Export, Data Generation, Data Masking, Create Chart and
Focus Mode, plus row/paging/view controls. They were not executed here.

The object-list columns were:

- Table (28): 名称、状态、表类型、表空间名称、集群名称、集群所有者、IOT 名称、IOT 类型、
  日志、已备份、行、度数、实例、缓存、表锁定、样本大小、上次分析、分区、临时、次要、嵌套、
  正在监控、依赖性、行移动、压缩、已生成、缓冲池、注释。
- View (13): 名称、状态、文本长度、类型文本、类型文本长度、OID 文本、OID 文本长度、视图类型、
  视图类型所有者、超级视图名称、版本视图、只读、注释。

The top toolbar changed for Oracle and included **实体化视图** and **数据泵**.
The actual Other popup exposed these **15** categories in this order:
**目录、公用数据库链接、公用同义词、表空间、数据库链接、索引、Java、实体化视图日志、包、
回收站、序列、同义词、触发器、类型、XML 架构**. This enumerates retained tool surfaces;
it does not establish their designers or behavior. Cloud/product-account items
remain excluded according to the project scope; Oracle objects and database
authentication remain required.

The two data tabs were closed without edits. Only the generated profile was
disconnected, then observed **Off / sessions 0**. Normal Quit completed: the
first immediate inventory still showed running, the next inventory showed not
running, followed immediately by the CUA completion summary. The generated
profile's metadata remains disconnected and Save Password remained off; the
reference's underlying credential store was not independently audited. The
controller then removed its exact container and tmpfs. All three separately
created Oracle research/reference containers were independently confirmed
absent, with **zero data volumes**; the reference DB and credential-bridge ports
refused connections. No broad Docker cleanup was used.

### Separate dependency and fixture research

Official [python-oracledb 26.1.0 metadata](https://pypi.org/project/oracledb/26.1.0/)
declares Python 3.10+ and CPython 3.13 wheels for all six target OS/architectures,
including native `win_arm64` and a macOS universal2 wheel. Five distinct wheel
payloads were downloaded and checked against PyPI sizes/SHA256; their twenty
native-extension headers matched the required PE, Mach-O slices and ELF
architectures. This is dependency availability and static architecture evidence,
not six native imports, packaging or installed execution.

A pinned public [gvenzl Oracle Free image](https://github.com/gvenzl/oci-oracle-free)
was actually pulled and run natively as ARM64, using tag
`23.26.3-slim-faststart` and child digest
`sha256:b0c7703767ca9403b50c8408d4c6aa824ee6bd93fbed8dc765a9073acf087740`.
This is a publisher image, not an Oracle-official image. Each fresh fixture was
configured with loopback-only port publishing, owner/task/run labels,
4 GiB memory / 2 CPU / 1 GiB shared-memory caps, no host data bind and no data
volume. The second and reference receipts also independently recorded/asserted
the actual HostConfig caps; the first did not record those cap fields.
Controller-generated secrets stayed in process memory and a new tmpfs; the
reference received only the temporary app-user secret
through a bounded, capability-protected loopback bridge. No credentials were
written to host evidence or Docker environment/arguments.

Separate driver-only research actually exercised Thin password/service,
new-schema table/view catalog/readback, a newly generated TNS alias, SID with
SYSTEM and password SYSDBA to the CDB. These are controlled Python clients,
not additional Navicat or OpenNavicat workflows. The default `NUMBER(12,2)`
fetch was a Python float: successful small-value comparison does not establish
generic Decimal fidelity. Driver-specific precision/LOB/time-zone handling,
transactions and cancellation need dedicated tests.

The [pinned driver feature matrix](https://python-oracledb.readthedocs.io/en/v26.1.0/user_guide/appendix_a.html)
and [initialization rules](https://python-oracledb.readthedocs.io/en/v26.1.0/user_guide/initialization.html)
also require explicit handling of Thin/Thick process mode and unsupported auth
paths. The 26.1 dedicated TLS external-auth chapter and general external-auth
notes differ; that exact path remains unknown until tested. Existing TNS/wallets
and global Oracle client configuration must not be inspected or modified as a
shortcut to testing.

Private evidence is under `artifacts/next-oracle-reference-prep/`, particularly
`actual-form-r1/verification.json`, the three separate `research-*` receipts,
`independent-cleanup.json` and `wheel-api-review/`. Raw screenshots, local paths,
profile names, connection endpoints and generated credentials are excluded from
tracked artifacts and application packages. The next implementation slice is a
dedicated Oracle adapter and reference-shaped settings/catalog workflow. All
Oracle tool/UI gates, other authentication/security paths, complete types and
transactions, Data Pump and other object tools, six native targets and
Windows/Linux reference acceptance remain open.
