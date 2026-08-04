#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

API_URL = "https://capi.showtimes.com.tw/4/events/seatsAvailability"
BOOKING_URL = (
    "https://www.showtimes.com.tw/ticketing/selectEvents/"
    "91/12698?date=2026-08-07"
)
BASELINE_FILE = Path("baseline-event-ids.json")

# 依照目前觀察，8/7 的蜘蛛人場次可能會落在這個缺口。
# 它只用來在通知信中標示命中，不是唯一判斷條件。
TARGET_EVENT_IDS = {4738021, 4738022, 4738023, 4738024}

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


def extract_event_ids(data: dict[str, Any]) -> set[int]:
    try:
        raw = data["payload"]["seatsAvailability"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("API JSON did not contain payload.seatsAvailability") from exc

    if not isinstance(raw, dict):
        raise RuntimeError("payload.seatsAvailability was not an object")

    event_ids: set[int] = set()

    # 只讀 JSON 左側的 key。
    # 右側的剩餘座位數會一直變，完全不參與判斷。
    for event_id_raw in raw.keys():
        try:
            event_id = int(event_id_raw)
        except (TypeError, ValueError):
            continue

        if event_id > 0:
            event_ids.add(event_id)

    if not event_ids:
        raise RuntimeError("No numeric event IDs were found in the API response")

    return event_ids


def load_baseline() -> tuple[set[int], dict[str, Any]]:
    if not BASELINE_FILE.is_file():
        raise RuntimeError(
            f"{BASELINE_FILE} is missing. "
            "Run the Initialize ShowTimes baseline workflow first."
        )

    try:
        metadata = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read {BASELINE_FILE}") from exc

    if not isinstance(metadata, dict) or not isinstance(
        metadata.get("event_ids"), list
    ):
        raise RuntimeError(f"{BASELINE_FILE} has an invalid structure")

    baseline_ids: set[int] = set()
    for value in metadata["event_ids"]:
        try:
            event_id = int(value)
        except (TypeError, ValueError):
            continue

        if event_id > 0:
            baseline_ids.add(event_id)

    if not baseline_ids:
        raise RuntimeError(f"{BASELINE_FILE} contains no usable event IDs")

    return baseline_ids, metadata


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value


def send_email(subject: str, body: str) -> None:
    gmail_user = require_env("GMAIL_USER")
    gmail_app_password = require_env("GMAIL_APP_PASSWORD").replace(" ", "")

    # NOTIFY_TO 沒設定時，直接寄回寄件 Gmail 自己。
    notify_to = os.environ.get("NOTIFY_TO", gmail_user).strip() or gmail_user

    message = EmailMessage()
    message["From"] = gmail_user
    message["To"] = notify_to
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context,
        timeout=30,
    ) as smtp:
        smtp.login(gmail_user, gmail_app_password)
        smtp.send_message(message)


def write_github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    safe_value = value.replace("\r", " ").replace("\n", " ")
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"{name}={safe_value}\n")


def format_ids(event_ids: list[int], limit: int = 100) -> str:
    shown = event_ids[:limit]
    text = ", ".join(str(event_id) for event_id in shown)

    if len(event_ids) > limit:
        text += f" ... and {len(event_ids) - limit} more"

    return text or "none"


def main() -> int:
    force_test_email = (
        os.environ.get("FORCE_TEST_EMAIL", "").lower() == "true"
    )

    baseline_ids, baseline_metadata = load_baseline()
    current_data = fetch_json(API_URL)
    current_ids = extract_event_ids(current_data)

    # 核心判斷：目前的 key 集合減掉基準 key 集合。
    new_ids = sorted(current_ids - baseline_ids)

    # 只記錄，不觸發。舊場次消失很正常。
    removed_ids = sorted(baseline_ids - current_ids)

    # 命中你推測的 4738021-4738024 時，在信中另外標示。
    target_hits = sorted(set(new_ids) & TARGET_EVENT_IDS)

    baseline_max = max(baseline_ids)
    current_max = max(current_ids)
    max_increased = current_max > baseline_max

    # 只要多出任何一個原本不存在的 key，就通知。
    triggered = bool(new_ids)

    print(f"baseline_count={len(baseline_ids)}")
    print(f"current_count={len(current_ids)}")
    print(f"new_ids_count={len(new_ids)}")
    print(f"removed_ids_count={len(removed_ids)}")
    print(f"baseline_max_event_id={baseline_max}")
    print(f"current_max_event_id={current_max}")
    print(f"max_increased={max_increased}")
    print(f"target_hits={target_hits}")
    print(f"new_ids={format_ids(new_ids)}")
    print(f"triggered={triggered}")

    write_github_output("triggered", str(triggered).lower())
    write_github_output("email_sent", "false")
    write_github_output("new_ids_count", str(len(new_ids)))
    write_github_output("new_ids", format_ids(new_ids, limit=50))
    write_github_output("target_hits", format_ids(target_hits, limit=50))
    write_github_output("baseline_max_event_id", str(baseline_max))
    write_github_output("current_max_event_id", str(current_max))

    if force_test_email:
        subject = "✅ 秀泰蜘蛛人監控：Gmail 測試成功"
        body = f"""這是一封 GitHub Actions 測試信。

監控 API：{API_URL}
基準 event 數量：{len(baseline_ids)}
目前 event 數量：{len(current_ids)}
基準最大 event ID：{baseline_max}
目前最大 event ID：{current_max}

收到這封信代表 Gmail App Password 與 GitHub Secrets 設定正確。
這次測試不會建立「已通知」標記，也不會停止正式監控。
"""
        send_email(subject, body)
        write_github_output("email_sent", "true")
        write_github_output("test_email", "true")
        print("Test email sent.")
        return 0

    write_github_output("test_email", "false")

    if not triggered:
        print("No new event ID keys. No email sent.")
        return 0

    captured_at = baseline_metadata.get("captured_at_utc", "unknown")

    subject = "🚨 大巨蛋蜘蛛人 8/7 可能已開放訂票"
    body = f"""秀泰 seatsAvailability API 出現基準中沒有的新 event ID。

這次只比較 JSON 左側的 event ID keys；
右側的剩餘座位數值完全忽略。

依照你觀察到的秀泰同步更新方式，這是高可信度的開票訊號，
但仍可能由其他場次造成小機率誤報。

基準建立時間（UTC）：{captured_at}
基準 event 數量：{len(baseline_ids)}
目前 event 數量：{len(current_ids)}
新增 event ID 數量：{len(new_ids)}
新增 event IDs：{format_ids(new_ids)}
命中推測區段 4738021-4738024：{format_ids(target_hits)}
基準最大 event ID：{baseline_max}
目前最大 event ID：{current_max}
最大 ID 是否增加：{max_increased}

請立刻開啟 2026-08-07 售票頁確認：
{BOOKING_URL}

監控 API：
{API_URL}
"""

    send_email(subject, body)
    write_github_output("email_sent", "true")
    print("Notification email sent.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
