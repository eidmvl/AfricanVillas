from urllib.parse import parse_qs, urlparse

from african_villas.maps import build_google_maps_url


def test_google_maps_url_contains_region_and_country() -> None:
    url = build_google_maps_url("Танзания", "Занзибар")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.google.com"
    assert query["api"] == ["1"]
    assert query["query"] == ["Занзибар, Танзания"]


def test_google_maps_url_is_empty_without_location() -> None:
    assert build_google_maps_url("", "") == ""
    assert build_google_maps_url("Танзания", "") == ""
    assert build_google_maps_url("", "Занзибар") == ""
