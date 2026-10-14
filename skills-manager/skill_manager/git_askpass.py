"""Answer Git HTTPS authentication prompts using container environment variables."""

import os
import sys


def main() -> int:
    prompt = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if prompt.startswith("username"):
        value = os.environ.get("username", "")
    elif prompt.startswith("password"):
        value = os.environ.get("password", "")
    else:
        return 1
    if not value:
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
