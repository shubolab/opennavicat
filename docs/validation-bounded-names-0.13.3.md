# Bounded object-name presentation checkpoint

Baseline: `adf9221ecac6b51d27ccc56cea9acc6d4d8cc819` (2026-11-14).
This is an application rendering correction, not new reference acceptance.

The reviewed actual Cocoa GIF's phase 28 showed the fixed 105px pipeline source
circle cropping the middle of the generated collection name. The 115px stage
button also cropped a longer operator. Both controls now paint a right ellipsis
inside their actual font/style content rectangle, preserve their existing sizes,
and expose the complete original text in their tooltip and accessible name.
The pipeline definition, source selection, stage order and operator are unchanged.
Stage buttons still use the native `CE_PushButton` style drawing for their states.
The source label uses plain text so a collection name cannot become label markup.

The narrow inspector's object, connection and schema labels now expose their full
text as tooltips; the object heading also has an explicit accessible name. This
does not fix the inspector's entire title layout or close the long-text gate.

Verification used a physical Python 3.13.11 environment, PySide6 6.12.0, macOS arm64,
and the **offscreen** QPA. The same five new test cases fail on the baseline and
pass on the correction; existing pipeline and inspector tests plus those cases
pass **28/28**. Tests compare real rendered cards with ordinary Qt controls
containing the expected bounded text, including a short name, a long unbroken
name, bare 12px/24px fonts and dark/light themes. They retain complete source text
and drafts, verify the original 105px/115px geometry, and exercise inspector
selection changes so old tooltips are replaced. The ordinary short source label
is pixel-identical to the standard QLabel rendering, including its border.
`git diff --check` passes.

Ignored local evidence is under `artifacts/visual-clipping/`: same-byte old-red
and new-green JUnit/logs, source/test hashes, and generated-only before/after
pipeline screenshots and capture receipts. The two complete before/after PNGs
were viewed directly; they show explicit ellipsis replacing raw clipping. They
are application screenshots from generated names, with no database connection,
reference artwork, personal hostname, credentials or business rows.

Actual Cocoa requalification, Windows/Linux rendering, native default/focus/hover
states, narrow combo text, the complete inspector layout, the remaining U15/U08
scope and full function parity remain open. The README GIF refresh and any
six-target installer/release qualification are separate integration work.
