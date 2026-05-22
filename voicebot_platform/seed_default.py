from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault(
    "BOT_LOG_DIR",
    str(Path(__file__).resolve().parent.parent / "logs"),
)

from bot import _HARDCODED_BOT_CONFIG

from .config_store import seed_default_bot
from .mongo import ensure_indexes


def main() -> None:
    ensure_indexes()
    result = seed_default_bot(_HARDCODED_BOT_CONFIG)
    bot = result["bot"]
    version = result["version"]
    print(
        "Seeded default bot "
        f"{bot['name']} assistant_id={bot['assistant_id']} version={version['version']}"
    )


if __name__ == "__main__":
    main()
