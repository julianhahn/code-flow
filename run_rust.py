"""Compatibility launcher for the three pre-Rust command lines."""
import os
from pathlib import Path
import sys


def launch(*mode):
    root = Path(__file__).resolve().parent
    # Let Cargo check the worktree on each source launch so a stale release
    # binary from another revision cannot silently shadow the requested version.
    os.execvp("cargo", ["cargo", "run", "--quiet", "--locked", "--manifest-path",
                        str(root / "Cargo.toml"), "--", *mode, *sys.argv[1:]])
