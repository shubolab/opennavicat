"""Data-only execution receipts shared with the VM-only qualification."""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class CommandReceipt:
    sequence: int
    verb: str
    writes: bool
    outcome: str  # rejected, acknowledged, acknowledged_error, uncertain
    affected: int | None = None
    error: str = ""
    reply: dict | None = field(default=None, repr=False)

