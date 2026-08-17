from __future__ import annotations

from urllib.parse import urlencode


def build_google_maps_url(country: str, region: str) -> str:
    country = country.strip()
    region = region.strip()
    if not country or not region:
        return ""
    query = f"{region}, {country}"
    return "https://www.google.com/maps/search/?" + urlencode(
        {"api": "1", "query": query}
    )
