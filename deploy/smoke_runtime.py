"""OCI smoke process using installer-injected HTTP check endpoints."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from .smoke import REQUIRED_CHECKS, run_smoke_checks


def _http_check(name: str):
    def check() -> tuple[bool, str]:
        key = f"CMUL8_SMOKE_{name.upper()}_URL"
        url = os.environ.get(key)
        if not url:
            return False, f"{key} is required"
        request = Request(url, headers={"User-Agent": "cmul8-smoke/1"})
        with urlopen(request, timeout=10) as response:  # nosec: endpoints are operator supplied
            body = response.read(4096)
            return 200 <= response.status < 300, f"HTTP {response.status}, {len(body)} bytes"
    return check


def main() -> int:
    results = run_smoke_checks({name: _http_check(name) for name in REQUIRED_CHECKS})
    print(json.dumps([result.__dict__ for result in results], sort_keys=True))
    return 0 if all(result.ok for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
