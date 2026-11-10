#!/usr/bin/env python3
"""Stage verified helper inputs for a future bundle; never changes release dist.

The caller must separately include bootstrap.js as package data. No installer
Spec/workflow is modified by this script, and no packaged acceptance is implied.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opennavicat.js_engine.payload import helper_name, stage_helper_payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', type=Path, default=ROOT/'artifacts/js-engine'/helper_name())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT/'dist') or output == ROOT:
        parser.error('Use an independent artifact directory rather than the release dist')
    print(stage_helper_payload(args.helper, output))
