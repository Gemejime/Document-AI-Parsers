"""Entry points package for rag-document-ai-worker binaries.

Preserves standard library `cmd` symbols (such as `Cmd`) so tools like `pdb`
can import `cmd` without collision when running in the repository root.
"""

import importlib.util
import os
import sysconfig
from typing import Any


_stdlib_cmd: Any = None
_stdlib_path = sysconfig.get_paths().get("stdlib")
if _stdlib_path:
    _cmd_file = os.path.join(_stdlib_path, "cmd.py")
    if os.path.exists(_cmd_file):
        _spec = importlib.util.spec_from_file_location("_stdlib_cmd", _cmd_file)
        if _spec and _spec.loader:
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _stdlib_cmd = _mod

if _stdlib_cmd is not None:
    Cmd = getattr(_stdlib_cmd, "Cmd", None)
    IDENTCHARS = getattr(_stdlib_cmd, "IDENTCHARS", "")
    PROMPT = getattr(_stdlib_cmd, "PROMPT", "")


def __getattr__(name: str) -> Any:
    if _stdlib_cmd is not None and hasattr(_stdlib_cmd, name):
        return getattr(_stdlib_cmd, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
