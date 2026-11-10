# Controlled JavaScript helper prototype

This branch is an independent development prototype, not enabled in released
OpenNavicat 0.6.1. Build with `python scripts/build-quickjs.py`. It fetches the
official QuickJS-NG v0.18.0 commit archive, verifies the pinned SHA-256, extracts
fresh sources, builds the core static library and links `runner.c`. No system
installation or Python extension is required. The source license is retained in
`LICENSE.quickjs-ng`; the build copies it beside the helper too.

The engine's official platform documentation is
<https://quickjs-ng.github.io/quickjs/supported_platforms/>. This prototype is
qualified only on native macOS ARM64. POSIX framing/process control must be
replaced and independently qualified on Windows, and tested on both Linux and
the second Mac architecture before six-platform packaging. Upstream's pinned
Linux ARM CI rows are commented out; do not infer acceptance from OS support.

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
