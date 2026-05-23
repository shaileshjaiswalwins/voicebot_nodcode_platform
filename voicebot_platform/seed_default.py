from __future__ import annotations

from .config_store import seed_default_bot
from .default_bot_config import build_default_bot_config
from .mongo import ensure_indexes
from .phrase_library import seed_default_phrases


def main() -> None:
    ensure_indexes()
    seed_default_phrases()
    result = seed_default_bot(build_default_bot_config())
    bot = result["bot"]
    version = result["version"]
    print(
        "Seeded default bot "
        f"{bot['name']} assistant_id={bot['assistant_id']} version={version['version']}"
    )


if __name__ == "__main__":
    main()
