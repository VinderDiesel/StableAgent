"""P7 M5：无 server extra 时 ImportError 契约。"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest

import stableagent.api as api_pkg


def test_require_server_extra_message(monkeypatch: pytest.MonkeyPatch) -> None:
    real = importlib.import_module

    def boom(name: str, package: str | None = None) -> ModuleType:
        if name == "fastapi":
            raise ImportError("No module named fastapi")
        return real(name, package)

    monkeypatch.setattr(api_pkg.importlib, "import_module", boom)
    with pytest.raises(ImportError, match="server"):
        api_pkg.require_server_extra()
