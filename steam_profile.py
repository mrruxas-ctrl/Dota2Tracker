from __future__ import annotations

import html
import re
import urllib.request

from db import extract_digits


STEAM_ID64_BASE = 76561197960265728


def steam_id64_from_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    steam2 = re.search(r"STEAM_[0-5]:([01]):(\d+)", text, re.IGNORECASE)
    if steam2:
        universe_offset = int(steam2.group(1))
        account_number = int(steam2.group(2))
        return str(STEAM_ID64_BASE + account_number * 2 + universe_offset)

    steam3 = re.search(r"\[U:1:(\d+)\]", text, re.IGNORECASE)
    if steam3:
        return str(STEAM_ID64_BASE + int(steam3.group(1)))

    digits = extract_digits(text)
    if not digits:
        return ""

    if len(digits) >= 17:
        return digits

    return str(STEAM_ID64_BASE + int(digits))


def steam_profile_url_from_id(value: str) -> str:
    steam_id64 = steam_id64_from_text(value)
    if not steam_id64:
        return ""
    return f"https://steamcommunity.com/profiles/{steam_id64}"


def parse_steam_level(profile_html: str) -> int | None:
    text = str(profile_html or "")
    patterns = (
        r'<span[^>]*class=["\'][^"\']*friendPlayerLevelNum[^"\']*["\'][^>]*>\s*(\d+)\s*</span>',
        r'class=["\'][^"\']*friendPlayerLevelNum[^"\']*["\'][^>]*>\s*(\d+)',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return int(match.group(1))
    return None


def fetch_steam_profile_level(value: str, timeout: float = 8.0) -> int | None:
    url = steam_profile_url_from_id(value)
    if not url:
        return None

    request = urllib.request.Request(
        f"{url}?l=english",
        headers={
            "User-Agent": "Mozilla/5.0 Dota2Tracker",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "ignore")
    return parse_steam_level(html.unescape(body))
