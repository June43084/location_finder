import base64
import hashlib
import hmac
import logging
import math
import os
from datetime import datetime
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request
from flask_cors import CORS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY 未設定")

GOOGLE_API_ENABLED = os.getenv("GOOGLE_API_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

REQUEST_TIMEOUT = 15
MAX_RESULTS = 60
MAX_RADIUS = 15000
GOOGLE_RESULTS_PER_REQUEST = 20

# 5 區均衡取樣：
# 中心保留 20 家，北/南/東/西各保留 10 家，合計最多 60 家。
ZONE_LIMITS = {
    "center": 20,
    "north": 10,
    "south": 10,
    "east": 10,
    "west": 10,
}

ZONE_ORDER = [
    "center",
    "north",
    "south",
    "east",
    "west",
]

# 每月安全上限：保留約 10% 緩衝，不顯示在前端。
MONTHLY_NEARBY_LIMIT = int(os.getenv("MONTHLY_NEARBY_LIMIT", "4500"))
MONTHLY_PHOTO_LIMIT = int(os.getenv("MONTHLY_PHOTO_LIMIT", "900"))
MONTHLY_GEOCODE_LIMIT = int(os.getenv("MONTHLY_GEOCODE_LIMIT", "9000"))

PACIFIC = ZoneInfo("America/Los_Angeles")


class BudgetStoreError(Exception):
    pass


def billing_month():
    return datetime.now(PACIFIC).strftime("%Y-%m")


def redis_cmd(command):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise BudgetStoreError("Upstash 環境變數未設定")

    try:
        r = requests.post(
            UPSTASH_URL,
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type": "application/json",
            },
            json=command,
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise BudgetStoreError(str(exc)) from exc

    if data.get("error"):
        raise BudgetStoreError(data["error"])
    return data.get("result")


def reserve_budget(kind, amount, limit):
    """先用 Redis 原子 INCRBY 保留額度；超過就 rollback，完全不呼叫 Google。"""
    key = f"location_finder:google:{billing_month()}:{kind}"
    value = int(redis_cmd(["INCRBY", key, int(amount)]))

    if value <= limit:
        return True

    try:
        redis_cmd(["INCRBY", key, -int(amount)])
    except BudgetStoreError:
        logging.exception("Budget rollback failed: %s", key)

    return False


def fail_closed():
    return jsonify({
        "error": "使用量保護服務暫時無法確認額度，為避免產生額外費用，Google API 已暫停。"
    }), 503


def month_limit(service):
    return jsonify({
        "error": f"{service}本月使用量已達安全上限，將於下個月自動恢復。"
    }), 503


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/reverse_geocode")
def reverse_geocode():
    if not GOOGLE_API_ENABLED:
        return jsonify({"error": "Google API 暫時停用。"}), 503

    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if lat is None or lng is None:
        return jsonify({"error": "Missing latitude or longitude"}), 400

    try:
        if not reserve_budget("geocode", 1, MONTHLY_GEOCODE_LIMIT):
            return month_limit("地址定位服務")
    except BudgetStoreError:
        logging.exception("Geocode budget store unavailable")
        return fail_closed()

    try:
        r = requests.get(
            GEOCODE_URL,
            params={"latlng": f"{lat},{lng}", "key": GOOGLE_API_KEY, "language": "zh-TW"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        if data.get("status") == "OK" and data.get("results"):
            return jsonify({"address": data["results"][0]["formatted_address"]})

        return jsonify({"error": "無法解析地址", "details": data.get("status")}), 404
    except requests.RequestException:
        logging.exception("Reverse geocode failed")
        return jsonify({"error": "反向地理編碼服務錯誤"}), 500


@app.route("/geocode_address", methods=["POST"])
def geocode_address():
    if not GOOGLE_API_ENABLED:
        return jsonify({"error": "Google API 暫時停用。"}), 503

    address = ((request.get_json(silent=True) or {}).get("address") or "").strip()
    if not address:
        return jsonify({"error": "Missing address"}), 400

    try:
        if not reserve_budget("geocode", 1, MONTHLY_GEOCODE_LIMIT):
            return month_limit("地址定位服務")
    except BudgetStoreError:
        logging.exception("Geocode budget store unavailable")
        return fail_closed()

    try:
        r = requests.get(
            GEOCODE_URL,
            params={"address": address, "key": GOOGLE_API_KEY, "language": "zh-TW"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        if data.get("status") == "OK" and data.get("results"):
            first = data["results"][0]
            loc = first["geometry"]["location"]
            return jsonify({
                "lat": loc["lat"],
                "lng": loc["lng"],
                "formatted_address": first["formatted_address"],
            })

        return jsonify({"error": "無法找到該地址的座標", "details": data.get("status")}), 404
    except requests.RequestException:
        logging.exception("Geocode address failed")
        return jsonify({"error": "地址轉換服務錯誤"}), 500


@app.route("/nearby_search", methods=["POST"])
def nearby_search():
    if not GOOGLE_API_ENABLED:
        return jsonify({"error": "Google API 暫時停用。"}), 503

    body = request.get_json(silent=True) or {}
    lat, lng = body.get("lat"), body.get("lng")
    place_type = body.get("type")
    radius = body.get("radius", 5000)

    if lat is None or lng is None or not place_type:
        return jsonify({"error": "Missing lat, lng, or type"}), 400

    try:
        lat, lng, radius = float(lat), float(lng), int(radius)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid lat, lng, or radius"}), 400

    radius = max(100, min(radius, MAX_RADIUS))

    # 固定做 5 次 Google Nearby Search：
    # center / north / south / east / west
    try:
        if not reserve_budget("nearby", 5, MONTHLY_NEARBY_LIMIT):
            return month_limit("附近搜尋服務")
    except BudgetStoreError:
        logging.exception("Nearby budget store unavailable")
        return fail_closed()

    # Google 結果已在 google_multi_search() 裡完成「五區均衡挑選」。
    google_places = google_multi_search(
        lat,
        lng,
        place_type,
        radius
    )

    # OSM 只負責 Google 不足 60 家時補位，不額外消耗 Google 額度。
    osm_places = osm_search(
        lat,
        lng,
        place_type,
        radius
    )

    places = list(google_places)

    # Google 不足 60 家時，才由 OSM 按距離由近到遠補足。
    if len(places) < MAX_RESULTS:
        existing_osm_keys = {
            unique_key(place)
            for place in places
        }

        osm_places = [
            place
            for place in osm_places
            if (
                place.get("distance") is not None
                and place["distance"] <= radius
            )
        ]

        osm_places.sort(
            key=lambda place: (
                place.get("distance", float("inf")),
                place.get("name") or "",
            )
        )

        for place in osm_places:
            key = unique_key(place)

            if key in existing_osm_keys:
                continue

            places.append(place)
            existing_osm_keys.add(key)

            if len(places) >= MAX_RESULTS:
                break

    places = places[:MAX_RESULTS]

    add_photo_proxy_urls(places)

    # 不回傳 quota / usage / 剩餘百分比。
    return jsonify({
        "places": places
    })


def google_place_key(place):
    """
    Google Places 去重 key。
    正常情況優先用 Place ID。
    """
    place_id = place.get("id")

    if place_id:
        return f"google:{place_id}"

    return (
        f"fallback:"
        f"{(place.get('name') or '').strip().lower()}:"
        f"{round(place['latitude'], 5)}:"
        f"{round(place['longitude'], 5)}"
    )


def take_next_unique(zone_places, cursor, used_keys):
    """
    從某一區的 Google 結果中，取得下一個尚未被其他區選走的地點。
    回傳 (place, new_cursor)。
    """
    while cursor < len(zone_places):
        place = zone_places[cursor]
        cursor += 1

        key = google_place_key(place)

        if key in used_keys:
            continue

        return place, cursor

    return None, cursor


def google_multi_search(origin_lat, origin_lng, place_type, radius):
    """
    5 點搜尋仍然只打 5 次 Google Nearby API，
    但不再把所有候選最後用「離使用者最近」排序。

    配額：
      center = 20
      north  = 10
      south  = 10
      east   = 10
      west   = 10

    顯示順序先 round-robin：
      center -> north -> south -> east -> west
    因此：
      手機每頁 5 家時，前幾頁會自然混合五個方向；
      電腦每頁 10 家時，前幾頁大約每區各 2 家。

    如果某區不足或被其他區重複吃掉，
    最後再用其他尚未選到的 Google 候選補滿 60。
    """
    points = search_points(
        origin_lat,
        origin_lng,
        radius
    )

    sample_radius = min(
        radius,
        max(100, int(radius * 0.8))
    )

    zone_results = {
        zone: []
        for zone in ZONE_ORDER
    }

    # 每區只呼叫 Google 一次。
    for zone, sample_lat, sample_lng in points:
        rank = (
            "POPULARITY"
            if zone == "center"
            else "DISTANCE"
        )

        results = google_search_once(
            origin_lat,
            origin_lng,
            sample_lat,
            sample_lng,
            place_type,
            sample_radius,
            rank
        )

        # Google 原始回傳順序保留：
        # center = POPULARITY 順序
        # outer zones = DISTANCE 順序
        for place in results:
            if place["distance"] <= radius:
                zone_results[zone].append(place)

    selected = []
    used_keys = set()
    cursors = {
        zone: 0
        for zone in ZONE_ORDER
    }
    selected_per_zone = {
        zone: 0
        for zone in ZONE_ORDER
    }

    # 第一階段：
    # 五區輪流取，先讓北南東西各拿滿 10，
    # center 同時先取得 10。
    for _ in range(10):
        for zone in ZONE_ORDER:
            if selected_per_zone[zone] >= ZONE_LIMITS[zone]:
                continue

            place, new_cursor = take_next_unique(
                zone_results[zone],
                cursors[zone],
                used_keys
            )
            cursors[zone] = new_cursor

            if place is None:
                continue

            selected.append(place)
            used_keys.add(
                google_place_key(place)
            )
            selected_per_zone[zone] += 1

    # 第二階段：
    # center 的目標是 20，因此再補中心熱門候選到 20。
    while selected_per_zone["center"] < ZONE_LIMITS["center"]:
        place, new_cursor = take_next_unique(
            zone_results["center"],
            cursors["center"],
            used_keys
        )
        cursors["center"] = new_cursor

        if place is None:
            break

        selected.append(place)
        used_keys.add(
            google_place_key(place)
        )
        selected_per_zone["center"] += 1

    # 第三階段：
    # 某些區可能因為店少 / 五區重疊而不足。
    # 將所有尚未使用的 Google 候選收集起來補到 60。
    leftovers = []

    for zone in ZONE_ORDER:
        for place in zone_results[zone]:
            key = google_place_key(place)

            if key in used_keys:
                continue

            leftovers.append(place)

    # leftovers 先依「離使用者距離」排序，
    # 只用在配額不足的補位，不會再破壞主要的五區均衡。
    leftovers.sort(
        key=lambda place: (
            place.get("distance", float("inf")),
            place.get("name") or "",
        )
    )

    for place in leftovers:
        key = google_place_key(place)

        if key in used_keys:
            continue

        selected.append(place)
        used_keys.add(key)

        if len(selected) >= MAX_RESULTS:
            break

    logging.info(
        (
            "Balanced Google selection: total=%s "
            "center=%s north=%s south=%s east=%s west=%s"
        ),
        len(selected),
        selected_per_zone["center"],
        selected_per_zone["north"],
        selected_per_zone["south"],
        selected_per_zone["east"],
        selected_per_zone["west"],
    )

    return selected[:MAX_RESULTS]


def google_search_once(origin_lat, origin_lng, lat, lng, place_type, radius, rank):
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        # 不要求 rating / userRatingCount，避免使用更高價欄位。
        "X-Goog-FieldMask": (
            "places.displayName,places.formattedAddress,places.location,"
            "places.id,places.photos"
        ),
    }
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": GOOGLE_RESULTS_PER_REQUEST,
        "rankPreference": rank,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius,
            }
        },
        "languageCode": "zh-TW",
    }

    try:
        r = requests.post(
            NEARBY_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        logging.exception("Google Nearby Search failed")
        return []

    output = []
    for place in data.get("places", []):
        loc = place.get("location") or {}
        plat, plng = loc.get("latitude"), loc.get("longitude")
        name = (place.get("displayName") or {}).get("text")

        if not name or plat is None or plng is None:
            continue

        pid = place.get("id")
        map_url = (
            "https://www.google.com/maps/search/?api=1"
            f"&query={quote_plus(name)}"
            f"&query_place_id={quote_plus(pid)}"
            if pid else
            "https://www.google.com/maps/search/?api=1"
            f"&query={quote_plus(f'{plat},{plng}')}"
        )

        photos = place.get("photos") or []
        photo_ref = photos[0].get("name") if photos else None

        output.append({
            "id": pid,
            "name": name,
            "formatted_address": place.get("formattedAddress"),
            "vicinity": place.get("formattedAddress"),
            "latitude": plat,
            "longitude": plng,
            "source": "Google",
            "distance": distance_m(origin_lat, origin_lng, plat, plng),
            "_photo_ref": photo_ref,
            "photo_url": "/static/placeholder.jpg",
            "map_url": map_url,
        })

    return output


def photo_token(photo_ref):
    encoded = base64.urlsafe_b64encode(photo_ref.encode()).decode().rstrip("=")
    sig = hmac.new(
        GOOGLE_API_KEY.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    return encoded, sig


def decode_photo_token(encoded, sig):
    expected = hmac.new(
        GOOGLE_API_KEY.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        return None

    try:
        raw = base64.urlsafe_b64decode(
            encoded + "=" * ((-len(encoded)) % 4)
        ).decode()
    except (ValueError, UnicodeDecodeError):
        return None

    return raw if raw.startswith("places/") and "/photos/" in raw else None


def add_photo_proxy_urls(places):
    for place in places:
        ref = place.pop("_photo_ref", None)

        if place.get("source") == "Google" and ref:
            encoded, sig = photo_token(ref)
            place["photo_url"] = (
                f"/place_photo?ref={quote_plus(encoded)}&sig={sig}"
            )
        else:
            place["photo_url"] = "/static/placeholder.jpg"


@app.route("/place_photo")
def place_photo():
    """安全快速圖片代理：Render 驗證與計數後，302 到 Google 圖片 CDN。"""
    if not GOOGLE_API_ENABLED:
        return redirect("/static/placeholder.jpg", code=302)

    encoded = (request.args.get("ref") or "").strip()
    sig = (request.args.get("sig") or "").strip()
    photo_ref = decode_photo_token(encoded, sig) if encoded and sig else None

    if not photo_ref:
        return redirect("/static/placeholder.jpg", code=302)

    try:
        if not reserve_budget("photo", 1, MONTHLY_PHOTO_LIMIT):
            return redirect("/static/placeholder.jpg", code=302)
    except BudgetStoreError:
        logging.exception("Photo budget store unavailable")
        return redirect("/static/placeholder.jpg", code=302)

    try:
        r = requests.get(
            f"https://places.googleapis.com/v1/{photo_ref}/media",
            params={
                "key": GOOGLE_API_KEY,
                "maxWidthPx": 400,
                "skipHttpRedirect": "true",
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        photo_uri = r.json().get("photoUri")

        if not photo_uri:
            return redirect("/static/placeholder.jpg", code=302)

        parsed = urlparse(photo_uri)
        host = (parsed.hostname or "").lower()

        if parsed.scheme != "https" or not (
            host == "googleusercontent.com" or host.endswith(".googleusercontent.com")
        ):
            logging.warning("Unexpected photo host rejected: %s", host)
            return redirect("/static/placeholder.jpg", code=302)

        response = redirect(photo_uri, code=302)
        response.headers["Cache-Control"] = "private, max-age=300"
        return response

    except (requests.RequestException, ValueError):
        logging.exception("Place Photo failed")
        return redirect("/static/placeholder.jpg", code=302)


def search_points(lat, lng, radius):
    """
    回傳五個有方向標籤的採樣點。

    offset = 原始搜尋半徑的 45%
    """
    offset = radius * 0.45

    dlat = offset / 111320.0

    dlng = (
        offset
        /
        (
            111320.0
            *
            max(
                abs(
                    math.cos(
                        math.radians(lat)
                    )
                ),
                0.01
            )
        )
    )

    return [
        ("center", lat, lng),
        ("north", lat + dlat, lng),
        ("south", lat - dlat, lng),
        ("east", lat, lng + dlng),
        ("west", lat, lng - dlng),
    ]


def osm_search(lat, lng, place_type, radius):
    tags = {
        "restaurant": ("amenity", "restaurant"),
        "cafe": ("amenity", "cafe"),
        "bar": ("amenity", "bar"),
        "bakery": ("shop", "bakery"),
        "meal_delivery": ("amenity", "food_court"),
        "meal_takeaway": ("amenity", "fast_food"),
        "amusement_park": ("leisure", "amusement_park"),
        "park": ("leisure", "park"),
        "museum": ("tourism", "museum"),
        "movie_theater": ("amenity", "cinema"),
        "bowling_alley": ("leisure", "bowling_alley"),
        "shopping_mall": ("shop", "mall"),
        "spa": ("amenity", "spa"),
        "beauty_salon": ("shop", "beauty"),
        "gym": ("leisure", "fitness_centre"),
        "zoo": ("tourism", "zoo"),
        "tourist_attraction": ("tourism", "attraction"),
        "night_club": ("amenity", "nightclub"),
    }

    if place_type not in tags:
        return []

    key, value = tags[place_type]
    query = f"""
    [out:json];
    (
      node["{key}"="{value}"](around:{radius},{lat},{lng});
      way["{key}"="{value}"](around:{radius},{lat},{lng});
      relation["{key}"="{value}"](around:{radius},{lat},{lng});
    );
    out center;
    """

    try:
        r = requests.post(OVERPASS_URL, data=query, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        logging.exception("OSM Overpass failed")
        return []

    output = []
    for e in data.get("elements", []):
        t = e.get("tags") or {}
        center = e.get("center") or {}
        plat = e.get("lat", center.get("lat"))
        plng = e.get("lon", center.get("lon"))

        if plat is None or plng is None:
            continue

        name = t.get("name", "N/A")
        if name == "N/A" and place_type not in {"park", "tourist_attraction"}:
            continue

        address = osm_address(t)
        output.append({
            "id": f"osm-{e['id']}",
            "osm_id": e["id"],
            "name": name,
            "formatted_address": address,
            "vicinity": address,
            "latitude": plat,
            "longitude": plng,
            "source": "OSM",
            "distance": distance_m(lat, lng, plat, plng),
            "photo_url": "/static/placeholder.jpg",
            "url": (
                "https://www.openstreetmap.org/"
                f"?mlat={plat}&mlon={plng}&zoom=18"
            ),
        })

    return output


def osm_address(tags):
    if tags.get("addr:full"):
        return tags["addr:full"].strip()

    parts = [
        tags[k]
        for k in ("addr:city", "addr:district")
        if tags.get(k)
    ]

    street = tags.get("addr:street")
    number = tags.get("addr:housenumber")
    if street:
        parts.append(f"{street} {number}" if number else street)

    return ", ".join(parts) if parts else "無地址資訊"


def unique_key(place):
    if place.get("source") == "Google" and place.get("id"):
        return ("google", place["id"])

    return (
        (place.get("name") or "").lower().strip(),
        (place.get("formatted_address") or "").lower().strip(),
        round(float(place["latitude"]), 5),
        round(float(place["longitude"]), 5),
    )


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5031")),
    )
