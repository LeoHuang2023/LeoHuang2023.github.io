"""nearby_places_overpass.py

免費方案：使用 OpenStreetMap (OSM) + Overpass API 以「經緯度 + 半徑」查詢附近地點。

目標：讓你可以把原本 Google Places 的查詢替換成 Overpass，但主程式仍可用
「固定輸出格式」處理結果。

輸出格式（每筆一定只有這 4 個 key）：
- name: str | None
- address: str | None
- rating: float | None   # OSM/Overpass 沒有 Google 那種評論評分，固定回傳 None
- distance_m: int        # 距離（公尺，四捨五入）

主要函式：
- search_nearby_veterinary(latitude, longitude, radius_m=1500, top_n=20)
- search_nearby_pet_friendly_food(latitude, longitude, radius_m=1500, top_n=20, strict=True)

依賴：requests

注意：Overpass 是共享公共服務，可能會限流或繁忙（429/5xx）。本模組內建退避重試。
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

# 你可以用環境變數或直接改這個 URL，切換不同 Overpass 實例
DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 建議改成你自己的聯絡資訊（有助於公共服務方追蹤濫用流量）
DEFAULT_USER_AGENT = "nearby-places-overpass/1.0 (contact: you@example.com)"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _build_address(tags: Dict[str, str]) -> Optional[str]:
    """Best-effort address formatter from OSM tags."""
    if not tags:
        return None

    # 若有人填 addr:full
    if tags.get("addr:full"):
        return tags["addr:full"].strip() or None

    parts: List[str] = []
    for k in ("addr:housenumber", "addr:street", "addr:district", "addr:city", "addr:postcode"):
        v = tags.get(k)
        if v:
            parts.append(v)

    # 有些店會只填短地址
    if not parts and tags.get("contact:address"):
        parts.append(tags["contact:address"])

    addr = " ".join(parts).strip()
    return addr or None


def _extract_center(el: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) for node/way/relation results."""
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    center = el.get("center")
    if isinstance(center, dict) and center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None


def _overpass_post(
    query: str,
    overpass_url: str = DEFAULT_OVERPASS_URL,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout_s: int = 45,
    max_retries: int = 6,
) -> Dict[str, Any]:
    """POST query to Overpass with exponential backoff on common transient errors."""
    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    backoff = 1.25
    last_err: Optional[Exception] = None

    for _ in range(max_retries):
        try:
            resp = requests.post(overpass_url, data={"data": query}, headers=headers, timeout=timeout_s)
            if resp.status_code in (429, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            time.sleep(backoff)
            backoff *= 2

    raise RuntimeError(f"Overpass request failed after retries: {last_err}")


def _to_output_record(name: Optional[str], address: Optional[str], distance_m: int) -> Dict[str, Any]:
    """Normalize to the required 4-key output format."""
    return {
        "name": name,
        "address": address,
        "rating": None,  # Overpass/OSM does not provide Google-like ratings
        "distance_m": int(distance_m),
    }


def search_nearby_veterinary(
    latitude: float,
    longitude: float,
    radius_m: int = 1500,
    top_n: int = 20,
    overpass_url: str = DEFAULT_OVERPASS_URL,
    user_agent: str = DEFAULT_USER_AGENT,
) -> List[Dict[str, Any]]:
    """查詢附近寵物醫院/獸醫院（amenity=veterinary）。"""

    query = f"""
    [out:json][timeout:25];
    (
      node[\"amenity\"=\"veterinary\"](around:{radius_m},{latitude},{longitude});
      way[\"amenity\"=\"veterinary\"](around:{radius_m},{latitude},{longitude});
      relation[\"amenity\"=\"veterinary\"](around:{radius_m},{latitude},{longitude});
    );
    out center tags;
    """

    data = _overpass_post(query, overpass_url=overpass_url, user_agent=user_agent)

    records: List[Dict[str, Any]] = []
    for el in data.get("elements", []):
        center = _extract_center(el)
        if not center:
            continue
        plat, plon = center

        tags = el.get("tags") or {}
        name = tags.get("name") or None
        address = _build_address(tags)
        dist = round(_haversine_m(latitude, longitude, plat, plon))

        records.append(_to_output_record(name, address, dist))

    records.sort(key=lambda x: x["distance_m"])
    return records[: max(0, int(top_n))]


def search_nearby_pet_friendly_food(
    latitude: float,
    longitude: float,
    radius_m: int = 1500,
    top_n: int = 20,
    strict: bool = True,
    overpass_url: str = DEFAULT_OVERPASS_URL,
    user_agent: str = DEFAULT_USER_AGENT,
) -> List[Dict[str, Any]]:
    """查詢「寵物友善餐廳/咖啡廳」

    - strict=True：只回傳有 dog/pets 標籤的店（較精準但可能較少）
    - strict=False：回傳附近所有 restaurant/cafe（覆蓋更廣，但不等於都寵物友善）

    注意：OSM 的 pet-friendly 標籤填寫不一致，因此 strict=False 常用於
    先抓餐廳，再用你自己的規則（例如關鍵字、白名單）做第二段篩選。
    """

    if strict:
        # dog=yes 或 dog=outside 視為友善；另加 pets=yes
        dog_filter = "[\\\"dog\\\"~\\\"^(yes|outside)$\\\"]"
        pets_filter = "[\\\"pets\\\"=\\\"yes\\\"]"

        query = f"""
        [out:json][timeout:25];
        (
          node[\"amenity\"=\"restaurant\"]{dog_filter}(around:{radius_m},{latitude},{longitude});
          way[\"amenity\"=\"restaurant\"]{dog_filter}(around:{radius_m},{latitude},{longitude});
          relation[\"amenity\"=\"restaurant\"]{dog_filter}(around:{radius_m},{latitude},{longitude});

          node[\"amenity\"=\"cafe\"]{dog_filter}(around:{radius_m},{latitude},{longitude});
          way[\"amenity\"=\"cafe\"]{dog_filter}(around:{radius_m},{latitude},{longitude});
          relation[\"amenity\"=\"cafe\"]{dog_filter}(around:{radius_m},{latitude},{longitude});

          node[\"amenity\"=\"restaurant\"]{pets_filter}(around:{radius_m},{latitude},{longitude});
          way[\"amenity\"=\"restaurant\"]{pets_filter}(around:{radius_m},{latitude},{longitude});
          relation[\"amenity\"=\"restaurant\"]{pets_filter}(around:{radius_m},{latitude},{longitude});

          node[\"amenity\"=\"cafe\"]{pets_filter}(around:{radius_m},{latitude},{longitude});
          way[\"amenity\"=\"cafe\"]{pets_filter}(around:{radius_m},{latitude},{longitude});
          relation[\"amenity\"=\"cafe\"]{pets_filter}(around:{radius_m},{latitude},{longitude});
        );
        out center tags;
        """
    else:
        query = f"""
        [out:json][timeout:25];
        (
          node[\"amenity\"~\"^(restaurant|cafe)$\"](around:{radius_m},{latitude},{longitude});
          way[\"amenity\"~\"^(restaurant|cafe)$\"](around:{radius_m},{latitude},{longitude});
          relation[\"amenity\"~\"^(restaurant|cafe)$\"](around:{radius_m},{latitude},{longitude});
        );
        out center tags;
        """

    data = _overpass_post(query, overpass_url=overpass_url, user_agent=user_agent)

    # 去重：同一個 name+address 有時會被 node/way 重複命中
    seen = set()
    records: List[Dict[str, Any]] = []

    for el in data.get("elements", []):
        center = _extract_center(el)
        if not center:
            continue
        plat, plon = center

        tags = el.get("tags") or {}
        name = tags.get("name") or None
        address = _build_address(tags)
        dist = round(_haversine_m(latitude, longitude, plat, plon))

        key = (name or "", address or "")
        if key in seen:
            continue
        seen.add(key)

        records.append(_to_output_record(name, address, dist))

    records.sort(key=lambda x: x["distance_m"])
    return records[: max(0, int(top_n))]


def search_nearby(
    latitude: float,
    longitude: float,
    radius_m: int = 1500,
    top_n: int = 20,
    mode: str = "veterinary",
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """統一入口：mode 可用 'veterinary' 或 'pet_friendly_food'."""
    mode = (mode or "").strip().lower()
    if mode == "veterinary":
        return search_nearby_veterinary(latitude, longitude, radius_m=radius_m, top_n=top_n, **kwargs)
    if mode in ("pet_friendly_food", "pet_food", "food"):
        return search_nearby_pet_friendly_food(latitude, longitude, radius_m=radius_m, top_n=top_n, **kwargs)
    raise ValueError("mode must be 'veterinary' or 'pet_friendly_food'")


# ----------------------------
# 相容層（可選）
# ----------------------------
# 如果你原本主程式寫的是 Google 版的函式名稱（例如 search_nearby_veterinary_legacy / v1），
# 可以直接改成 import 這個模組後，不用大改呼叫方式。
# 這些函式的 api_key / language / field_mask 參數會被忽略（Overpass 不需要）。

def search_nearby_veterinary_legacy(
    api_key: str,
    latitude: float,
    longitude: float,
    radius: int = 1500,
    language: Optional[str] = None,
    top_n: int = 20,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    _ = (api_key, language)
    return search_nearby_veterinary(latitude, longitude, radius_m=radius, top_n=top_n, **kwargs)


def search_nearby_veterinary_v1(
    api_key: str,
    latitude: float,
    longitude: float,
    radius: float = 1500.0,
    max_results: int = 20,
    field_mask: Optional[str] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    _ = (api_key, field_mask)
    return search_nearby_veterinary(latitude, longitude, radius_m=int(radius), top_n=max_results, **kwargs)


if __name__ == "__main__":
    lat = float(input("Latitude: ").strip())
    lon = float(input("Longitude: ").strip())
    radius = int(input("Radius meters (e.g. 1500): ").strip() or "1500")
    topn = int(input("Top N (e.g. 10): ").strip() or "10")

    print("\n--- Nearby veterinary ---")
    vets = search_nearby(latitude=lat, longitude=lon, radius_m=radius, top_n=topn, mode="veterinary")
    for i, r in enumerate(vets, 1):
        print(f"{i:02d}. {r['name']} | {r['address']} | rating={r['rating']} | {r['distance_m']}m")

    print("\n--- Nearby pet-friendly food (strict=True) ---")
    foods = search_nearby(latitude=lat, longitude=lon, radius_m=radius, top_n=topn, mode="pet_friendly_food", strict=True)
    for i, r in enumerate(foods, 1):
        print(f"{i:02d}. {r['name']} | {r['address']} | rating={r['rating']} | {r['distance_m']}m")
