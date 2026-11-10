# Navicat reference investigation

**Current reference after the user's explicit resume:**
`/Users/wangshubo/Downloads/Navicat Premium.app`, **18.0.5 / build 321**.
The new static identity, full capability categories, version-pair differences,
resource Evidence and concrete UI entry cues are recorded in
[reference-18-static.md](reference-18-static.md). Runtime/UI observations remain
a separate evidence chain. The following 16.1.1 report is historical evidence;
it must not be treated as acceptance of the updated reference.

Reference examined on 2026-11-10: `/Applications/Navicat Premium.app`.
The artifact identifies itself as **Navicat Premium 16.1.1, build 51**.
This report records a focused static resource investigation, not full behavioral
equivalence or a completed reconstruction. The new application's implementation
is independent; no Navicat code, icons or artwork are used by it.

## Investigation checklist

| Question | Result | Evidence and boundary |
| --- | --- | --- |
| Which installed reference/version? | Answered | Info.plist identifies bundle `com.navicat.NavicatPremium`, version 16.1.1/build 51. |
| Main product workspaces and navigation? | Partially answered | Main window and localization resources declare connection navigation, information pane, query, model and charts workspaces. Dynamic menu layout and runtime enablement were not observed. |
| Connection settings? | Partially answered | MySQL and SQLite form resources plus shared SSH/TLS settings were inspected. Live authentication/protocol behavior remains untested. |
| Query workflow? | Partially answered | Localization declares New Query, Run, Run Selected, Run Current Statement, Results, Query Timeout and Export Result. Statement parsing and transaction behavior remain unknown. |
| Schema/table design? | Partially answered | SQLite table form declares defaults, collation, auto increment and generated values; localization declares New Table and Design Table. Actual generated DDL was not captured. |
| Import/export? | Partially answered | Dedicated wizard form resources declare import/export format selectors; localization declares Execute SQL File, Import SQL, Export SQL, export result and saved query. Supported format lists and mapping semantics remain unknown. |
| Data/structure synchronization? | Partially answered | Option forms declare insert/update/delete and detailed schema compare choices. Algorithms, DDL ordering, conflict behavior and cross-engine semantics remain unknown. |
| Modeling, transfer and backup? | Partially answered | Localized product actions and model object export resources establish declared product surfaces. Runtime workflows and file formats remain unknown. |
| Runtime/pixel behavior and protocol compatibility? | Unresolved | No target launch, debugger attachment, database connection or runtime trace was part of this pass. |

## Declared product surfaces and reconstruction implications

These are observations of shipped UI declarations. A label establishes the
existence of that declaration, not that a feature is enabled, implemented or
correct at runtime.

| Surface | Observed declarations | Independent implementation implications |
| --- | --- | --- |
| Workbench | Navigation Pane, Information Pane, Show Only Active Objects, Flatten Connection; new connection, query, model, charts; import/export connections | A connection explorer and tabbed object/query workspaces are the central workflow. Pane state and connection management should persist. |
| MySQL connection | Connection name, host, port placeholder 3306, user name, password, save password | Keep connection metadata separate from secrets and allow testing before saving. |
| SQLite connection | Existing Database File, New SQLite 3, database file, connection name | Support file selection and database creation directly. SQLite 2 and encrypted-file compatibility need separate evidence and implementation. |
| Secure connection | SSH host/port/user, password/public key, private key/passphrase, compression; TLS client key/certificate, root/CA certificate, verify-ca and verify-full | Implement and test tunnel ownership/cleanup and certificate verification explicitly before claiming parity. |
| Query | Run, Run Selected, Run Current Statement, Results, Query Timeout, Commit, Export Result, Export Saved Query | Support selection execution, per-result metadata, cancellation, saved SQL and history. Transaction/session semantics need dedicated acceptance tests. |
| Table design | New Table, Design Table; SQLite default value, auto increment, collation, generated value/type, NOT NULL conflict policy | Introspection and DDL previews should preserve native types and constraints. Do not infer constraints from display strings alone. |
| Import/export | Wizard import/export format selectors, SQL import/export and Execute SQL File | Implement mapping, NULL representation, encoding, typed conversion, transactional failure handling and progress for each advertised format. |
| Data sync | Insert records absent from destination; update records different from source; delete extra destination records | A compare/preview stage and explicit deployment stage are distinct requirements. Safe row identity and conflict semantics matter. |
| Structure sync | Compare tables, PKs, FKs, indexes, uniques, checks, views, functions, triggers, events, sequences, rules, partitions, character sets, table options; identifier case sensitivity | A dialect-aware schema comparison engine is required for broad parity; generic text comparison is insufficient. |
| Model | Model documents (`ndm`, `ndm2`, `ndmc`, `ndmc2`, `ndml`, `ndml2`); model export objects; import tables/entities; relationship/foreign-key validation declarations | Start with schema visualization from actual introspection. Native Navicat model file compatibility remains uninvestigated. |
| Other product surfaces | Data Transfer, Backup, Restore Backup, history, model/query activities, charts workspace and cloud-related actions | Track these as outstanding product scope until implemented and accepted, rather than hiding them behind inactive controls. |

## Finding ledger

All references below are REA Evidence IDs retained in
`docs/evidence/navicat-resources.json`. That local bundle is diagnostic evidence
and is not needed to run or distribute OpenNavicat. Treat raw evidence as
reference material, not runtime assets.

| Ref | Evidence ID | Observation | Confidence |
| --- | --- | --- | --- |
| E1 | `ev_c82afde597bf9982531d17760a088ca5f6639df81efe19668df626970866caec` | Info.plist identity, document associations, main nib and platform declarations | Observed resource metadata |
| E2 | `ev_e86b9d1deb1869fd5cc3ee16a8182ae1980733a7f985dd51ffb7fdecf86817a3` | MainWindowController.strings | Observed shipped declarations |
| E3 | `ev_0dd11414e280019b7a639675515bccd9352afaebbfa25212dca829ae71199259` | MySQL connection form | Observed shipped declarations |
| E4 | `ev_486c40df7017738ebb258adc5388a3ee0b3412b16336aab22f5954f02e1dc0b7` | SQLite connection form | Observed shipped declarations |
| E5 | `ev_33e778c0e8646a70bec86d96ca705a36c536c67ba61f331dec32afdb73fd6f73` | SSH settings form | Observed shipped declarations |
| E6 | `ev_65c5cabd5ba5343d40b958e8cda008a75d6a75445fd324cd255e9e6f0551b0c7` | TLS settings form | Observed shipped declarations |
| E7 | `ev_f854a93a09b3085eb9b38411b05f79b0dd3ef3f13423ebea4660240ce3539570` | SQLite table field design form | Observed shipped declarations |
| E8 | `ev_897eb2d41edc8b56c3c7da73cf10f97a252870d0d75e36b8159c0d7d37c66fac` | Import format selector | Observed selector label; format list unknown |
| E9 | `ev_53c3150a96812db3165dfc5fefedb684241047fc7880bcf1762b2f86ebe46fdb` | Export format selector | Observed selector label; format list unknown |
| E10 | `ev_b05ea4a0b90b089295734a6df204fd1ddadbffa40cdb109d2c35765e9985f4a0` | Data sync insert/update/delete options | Observed shipped declarations |
| E11 | `ev_ee91eb8fe71aea116d465586d545b2a7b04632d8dce39770f9a6afd0bf2d3208` | Structure sync options | Observed shipped declarations |
| E12 | `ev_592553bce15ddc472af74eef092fa043d470b03699ed9172b0f0201c79120b2e` | Model export file and object selectors | Observed shipped declarations |
| E13 | `ev_d7b00c628d38b7c65b70b12af8475f0bd6523ba4aa24261d658dd011b80ddf07` | Localizable.strings (3,051 entries), central product action names | Observed declarations; dynamic use unknown |
| E14 | `ev_6397252f4d81e00f3dbb75bbe03b8236eea91bea36cbbc30db3e8c3ec768ad33` | Localizable_Diagram.strings (417 entries) | Observed declarations; model behavior unknown |
| E15 | `ev_64e2ab6236f6fda3be4454feb4e1edcc6f6d21e5689b91dd0b635a074387e4de` | Bounded Interface Builder graph, first 64 discovered archives | Derived static graph; incomplete app coverage |

## Coverage and evidence limitations

The resource-only REA route deliberately does not resolve or hash the app
executable. The plist observations therefore have no executable SHA-256 binding.
The Interface Builder decoder is bounded to 64 documents and reports 5,320
nodes, 4,143 edges, and truncation; many main application forms are outside that
graph. Its summary is `ev_dbef94d809f3181ad6fff1d0fda81e2bb0ae3b197d97e9cc5a8f1b43892fce7f`.
Private/unrecognized serialized classes, unresolved handlers, dynamic UI and
runtime pixel geometry remain unknown. The feature findings above use the
specifically inspected localization resources rather than claiming completeness
from this bounded graph.

QueryDesignSQLEditorModule.strings and QueryOutputModule.strings were empty;
this is a bounded observation of those two localization files, not evidence
that the query editor/output lacks controls. MainMenu.strings mostly declares
standard macOS menu items, while product action names are supplied elsewhere.
The REA resource session was exported and closed after this investigation.

## Next evidence needed for parity

1. Capture user-visible workflows against disposable SQLite fixtures: connect,
   browse, edit with NULL/BLOB/composite keys, selected/current statement run,
   transaction rollback, save query, CSV import/export and SQL dump restore.
2. Capture equivalent workflows on disposable MySQL and PostgreSQL servers;
   record schema namespace handling, unsupported types and cancellation effects.
3. Inspect compare/preview/apply workflows for transfer and synchronization with
   explicit conflicting rows, schema dependencies and failed statements.
4. Compare model, backup, automation, charts and additional database engines as
   separately testable product capabilities.
