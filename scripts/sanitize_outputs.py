from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from security_utils import redact_structure

TARGETS = (Path("data/news.json"), Path("data/stock_data.json"))


def sanitize_file(path: Path) -> bool:
    if not path.exists():
        return False
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    sanitized = redact_structure(payload)
    before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    after = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    path.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
    return before != after


def main() -> None:
    changed = []
    for path in TARGETS:
        try:
            if sanitize_file(path):
                changed.append(str(path))
        except Exception as exc:
            raise SystemExit(f"Failed to sanitize {path}: {type(exc).__name__}") from None
    print("Secret sanitization complete" + (f": {', '.join(changed)}" if changed else ": no exposed values found"))


if __name__ == "__main__":
    main()
