from __future__ import annotations

import os
from pathlib import Path

import pytest

IRON_GOD_LOCAL_DIR = Path(
    os.environ.get(
        "IRON_GOD_LOCAL_DIR",
        Path.home() / "AppData" / "Local" / "ExponentialEngine" / "IronGodLocal",
    )
)


def requires_iron_god_local(*filenames: str) -> pytest.MarkDecorator:
    missing = [name for name in filenames if not (IRON_GOD_LOCAL_DIR / name).is_file()]
    reason = (
        "requires the external IronGodLocal checkout; missing "
        + ", ".join(missing)
    )
    return pytest.mark.skipif(bool(missing), reason=reason)
