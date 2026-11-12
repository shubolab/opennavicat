# MongoDB collection collation controls

The ordinary collection collation page now uses the reference's structured
controls instead of one raw Extended JSON editor. A new collection starts with
an empty editable `区域设置` combo and nine disabled dependent fields. Selecting
a locale enables:

- `大小写级别`: off / on
- `大小写优先`: upper / lower / off
- `强度`: primary / secondary / tertiary / quaternary / identical level
- `数值排序`: off / on
- `替代`: non-ignorable / shifted
- `最大变量`: punct / space
- `逆向`: off / on
- `规范化`: off / on

The locale field remains editable so a MongoDB locale not present in the local
suggestion list can still be entered. The menu carries the 152 ordered,
distinct non-empty locale values enumerated from the 18.0.5 reference, including
MongoDB variants such as `ar@collation=compat`, `en_US_POSIX`, the German,
Korean, Chinese and other suffix families, and `zh_Hant`. Known values map to
their BSON names and numbers; blank dependent fields are omitted so the server
applies its documented defaults. Server-added `version` and any unknown collation fields are retained
when the page is loaded and converted back into the reviewed draft.

Existing collections display their normalized server collation values in these
controls and keep every control disabled. MongoDB collection collation is
treated as immutable rather than silently rebuilding the collection.

## Live reference observation

On a new owned MongoDB 8.0.34 fixture, the accessibility model first reported
100 entries at the top of the locale list and reported 153 after scrolling. A
page-by-page read produced 152 distinct non-empty values; adjacent selected
values around the boundary (`mk`, `ml`, `mn`, `mr`, `ms`) remained stable.
The first values were `simple`, `af`, `am`, `ar`, `ar@collation=compat`, and the
last were `zh`, four `zh@collation=…` variants, `zh_Hant`, `zu`. The 100→153
accessibility-count discrepancy remains an explicit evidence limitation.
Entering `en` normalized to `en_US` and enabled all dependent controls.

The observed choices were off/on for case level, upper/lower/off for case first,
five strength levels, off/on for numeric ordering, non-ignorable/shifted for
alternate, punct/space for max variable, and off/on for backwards and
normalization. Accessibility changes to the dependent combos did not enter the
original application's script model, so they were not treated as successful
reference writes. A locale-only collection was saved and independently read:

```json
{
  "locale": "en_US",
  "caseLevel": false,
  "caseFirst": "off",
  "strength": 3,
  "numericOrdering": false,
  "alternate": "non-ignorable",
  "maxVariable": "punct",
  "normalization": false,
  "backwards": false,
  "version": "57.1"
}
```

Reopening the collection showed those six non-version values in the structured
controls with every control disabled. The generated container, documents and
profiles were disconnected and the exact container ID was removed.

## Verification boundary

The structured editor has unit coverage for blank/new state, locale enablement,
all observed value mappings, unknown-field/version retention, clearing locale,
read-only existing state and exact collection-page drafts. A real MainWindow
test creates a new collection with every structured value and independently
reads the server-expanded catalog. The expanded collection runner passed **240
cases with zero skips/failures/errors**, native Cocoa collation checks passed
**6/6**, and the full source suite passed **1776 cases with 552 conditionally
skipped and zero failures/errors**. The source smoke passed 6 rows × 4 columns.
The single tracked application GIF was re-recorded from the actual Cocoa UI as
18 phases, 245 encoded frames and 40.84 seconds; representative frames from
every phase passed the privacy review.

The 18.0.5 locale list values are now reproduced, while the accessibility
count discrepancy, all server/locale-specific defaults, structured controls for
other collection types and Windows/Linux reference UI remain open gates.
