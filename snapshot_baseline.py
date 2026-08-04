#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_URL = "https://capi.showtimes.com.tw/4/events/seatsAvailability"
OUTPUT_FILE = Path("baseline-event-ids.json")
USER_AGENT = (
    "Mozilla/5.0 (compatible; ShowTimesTicketWatcher/2.0; "
    "+https://github.com/actions)"
)


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"API returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API request failed: {exc.reason}") from exc

    if status != 200:
        raise RuntimeError(f"API returned unexpected HTTP {status}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("API response was not valid JSON") from exc

    if not isinstance(data, dict):
        raise RuntimeError("API response root was not an object")

    return data


def extract_event_ids(data: dict[str, Any]) -> list[int]:
    try:
        raw = data["payload"]["seatsAvailability"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("API JSON did not contain payload.seatsAvailability") from exc

    if not isinstance(raw, dict):
        raise RuntimeError("payload.seatsAvailability was not an object")

    event_ids: set[int] = set()
    for event_id_raw in raw.keys():
        try:
            event_id = int(event_id_raw)
        except (TypeError, ValueError):
            continue

        if event_id > 0:
            event_ids.add(event_id)

    if not event_ids:
        raise RuntimeError("No numeric event IDs were found in the API response")

    return sorted(event_ids)


def main() -> int:
    data = fetch_json(API_URL)
    event_ids = extract_event_ids(data)

    snapshot = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": API_URL,
        "event_count": len(event_ids),
        "min_event_id": event_ids[0],
        "max_event_id": event_ids[-1],
        "event_ids": event_ids,
    }

    OUTPUT_FILE.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {OUTPUT_FILE}")
    print(f"event_count={len(event_ids)}")
    print(f"min_event_id={event_ids[0]}")
    print(f"max_event_id={event_ids[-1]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
