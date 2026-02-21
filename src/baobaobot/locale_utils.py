"""Timezone-to-locale mapping utilities.

Single source of truth for mapping IANA timezone names to BCP 47 locale codes.
Used by both the setup wizard (main.py) and user profile detection (persona/profile.py).
"""

from __future__ import annotations

# Timezone → BCP 47 locale code
TZ_LOCALE_MAP: dict[str, str] = {
    # East Asia
    "Asia/Taipei": "zh-TW",
    "Asia/Hong_Kong": "zh-HK",
    "Asia/Shanghai": "zh-CN",
    "Asia/Chongqing": "zh-CN",
    "Asia/Tokyo": "ja-JP",
    "Asia/Seoul": "ko-KR",
    # Southeast Asia
    "Asia/Bangkok": "th-TH",
    "Asia/Ho_Chi_Minh": "vi-VN",
    "Asia/Jakarta": "id-ID",
    "Asia/Kuala_Lumpur": "ms-MY",
    # South Asia
    "Asia/Kolkata": "hi-IN",
    # Europe
    "Europe/Berlin": "de-DE",
    "Europe/Paris": "fr-FR",
    "Europe/Madrid": "es-ES",
    "Europe/Rome": "it-IT",
    "Europe/Lisbon": "pt-PT",
    "Europe/Moscow": "ru-RU",
    # Americas
    "America/Sao_Paulo": "pt-BR",
}
