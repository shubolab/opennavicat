# Controlled JavaScript helper prototype

This branch is an independent development prototype, not enabled in released
OpenNavicat 0.6.1. Build with `python scripts/build-quickjs.py`. It fetches the
official QuickJS-NG v0.18.0 commit archive, verifies the pinned SHA-256, extracts
fresh sources, builds the core static library and links `runner.c`. No system
installation or Python extension is required. The source license is retained in
`LICENSE.quickjs-ng`; the build copies it beside the helper too.

The engine's official platform documentation is
<https://quickjs-ng.github.io/quickjs/supported_platforms/>. This prototype is
qualified initially on native macOS ARM64. The portable helper now uses bounded
C buffers, Windows binary pipe mode/QPC or POSIX monotonic clocks, and a parent
pipe-reader thread plus exact-process Windows kill/reap without a console popup.
Other targets require `--allow-unqualified-platform` while the independent native
qualification workflow establishes actual evidence. Upstream's pinned
Linux ARM CI rows are commented out; do not infer acceptance from OS support.

The feature branch's `js-engine-qualification.yml` has six native source/helper
targets and a separate fresh-owned MongoDB protocol job. It never publishes or
builds application installers and does not change the existing release workflow.
Windows 2022 uses its installed Visual Studio 2022 x64 toolchain; Windows 11 ARM
uses installed Visual Studio 2026/ARM64, matching the pinned NG native CI route.
The build receipt parses the real PE/ELF/Mach-O header and checks its machine
against the runner. A platform-support claim is not substituted for execution.

The helper exposes no Python, filesystem, process, socket, or `std/os` module.
It evaluates JavaScript source, never untrusted serialized bytecode. Removing
host APIs is not an operating-system sandbox or a C-engine security proof.
See the official security policy at the pinned source revision:
<https://github.com/quickjs-ng/quickjs/blob/v0.18.0/SECURITY.md>.

Every operation has framed, bounded, structured BSON IPC with a per-run nonce
supplied out of JavaScript. The parent owns the selected database, command
validation, read-only authority, native Mongo transport, cursor/session cleanup,
acknowledgement records and cancellation. Script wrappers cannot expand that
authority. The native VM deadline bounds JavaScript work; an independent parent
watchdog must also terminate/reap the child while a host DB operation blocks.

Confirmed writes are never presented as rolled back when a later command fails.
Write concern/network acknowledgement uncertainty is explicit. This milestone
will implement real `find`/`findOne`/aggregate chains, runCommand and basic writes
with generated fixture data. Complete mongosh/Navicat helper/global semantics,
streaming cursors and arbitrary raw-BSON representation remain open gates.
