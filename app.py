import base64
import hashlib
import hmac
import logging
import math
import os
import threading
import time
from collections import defaultdict, deque
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv()

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    logging.error("GOOGLE_API_KEY 環境變數未設定。")
    raise ValueError("GOOGLE_API_KEY 環境變數未設定。")

# 緊急停止 Google API 的開關。
# Render Environment 設 GOOGLE_API_ENABLED=false 後，
# 搜尋 / Geocoding / 圖片代理會立刻停止呼叫 Google。
GOOGLE_API_ENABLED = (
    os.getenv("GOOGLE_API_ENABLED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)

GOOGLE_GEOCODING_API_BASE_URL = (
    "https://maps.googleapis.com/maps/api/geocode/json"
)
GOOGLE_PLACES_NEARBY_SEARCH_NEW_URL = (
    "https://places.googleapis.com/v1/places:searchNearby"
)
OVERPASS_API_BASE_URL = "https://overpass-api.de/api/interpreter"

REQUEST_TIMEOUT = 15

# 搜尋結果設定
MAX_RESULTS = 60
GOOGLE_RESULTS_PER_REQUEST = 20
SEARCH_POINT_OFFSET_RATIO = 0.45
SAMPLE_RADIUS_RATIO = 0.80

# 前端採分頁渲染：手機每頁 5 家、桌機每頁 10 家。
# 因此後端可安全產生最多 60 家的照片 proxy URL；
# 實際 Photo API 只會在該頁卡片被渲染時載入。
# 仍可在 Render Environment 用 MAX_GOOGLE_PHOTOS_PER_SEARCH 調低上限。
MAX_GOOGLE_PHOTOS_PER_SEARCH = int(
    os.getenv("MAX_GOOGLE_PHOTOS_PER_SEARCH", "60")
)

# -------------------------------
# 匿名公開網站的軟性 Rate Limit
# -------------------------------
# 這些限制存在 Render process 記憶體中。
# 它們是「第二層防護」，真正的硬止血仍應在 Google Cloud 設 quota。
SEARCHES_PER_IP_PER_MINUTE = int(
    os.getenv("SEARCHES_PER_IP_PER_MINUTE", "3")
)
SEARCHES_PER_IP_PER_DAY = int(
    os.getenv("SEARCHES_PER_IP_PER_DAY", "20")
)
SEARCHES_GLOBAL_PER_HOUR = int(
    os.getenv("SEARCHES_GLOBAL_PER_HOUR", "30")
)
SEARCHES_GLOBAL_PER_DAY = int(
    os.getenv("SEARCHES_GLOBAL_PER_DAY", "100")
)

GEOCODE_PER_IP_PER_MINUTE = int(
    os.getenv("GEOCODE_PER_IP_PER_MINUTE", "10")
)
GEOCODE_PER_IP_PER_DAY = int(
    os.getenv("GEOCODE_PER_IP_PER_DAY", "60")
)

PHOTO_PER_IP_PER_MINUTE = int(
    os.getenv("PHOTO_PER_IP_PER_MINUTE", "60")
)
PHOTO_PER_IP_PER_DAY = int(
    os.getenv("PHOTO_PER_IP_PER_DAY", "300")
)
PHOTO_GLOBAL_PER_HOUR = int(
    os.getenv("PHOTO_GLOBAL_PER_HOUR", "600")
)
PHOTO_GLOBAL_PER_DAY = int(
    os.getenv("PHOTO_GLOBAL_PER_DAY", "1500")
)

_rate_lock = threading.Lock()
_rate_events = defaultdict(deque)


def google_api_is_enabled():
    return GOOGLE_API_ENABLED


def get_client_ip():
    """
    優先使用 Render/edge 常見的 CF-Connecting-IP。
    若沒有，退回 Flask 的 remote_addr。
    Rate limit 只是軟性防護，不應取代 Google Cloud quota。
    """
    cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        return cf_ip

    return request.remote_addr or "unknown"


def _rate_check(bucket, limit, window_seconds):
    now = time.time()
    key = str(bucket)

    with _rate_lock:
        events = _rate_events[key]
        cutoff = now - window_seconds

        while events and events[0] <= cutoff:
            events.popleft()

        if len(events) >= limit:
            retry_after = max(
                1,
                int(window_seconds - (now - events[0]))
            )
            return False, retry_after

        events.append(now)
        return True, 0


def enforce_limits(rules):
    """
    rules:
      [
        ("bucket-name", limit, window_seconds),
        ...
      ]

    若任何一條超限，回傳 429 Response；否則回傳 None。
    """
    for bucket, limit, window_seconds in rules:
        allowed, retry_after = _rate_check(
            bucket,
            limit,
            window_seconds
        )

        if not allowed:
            response = jsonify({
                "error": "請求太頻繁，請稍後再試。",
                "retry_after_seconds": retry_after
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response

    return None


def google_disabled_response():
    return jsonify({
        "error": "Google API 暫時停用，請稍後再試。"
    }), 503


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/reverse_geocode')
def reverse_geocode():
    if not google_api_is_enabled():
        return google_disabled_response()

    client_ip = get_client_ip()
    limited = enforce_limits([
        (
            f"geocode-minute:{client_ip}",
            GEOCODE_PER_IP_PER_MINUTE,
            60
        ),
        (
            f"geocode-day:{client_ip}",
            GEOCODE_PER_IP_PER_DAY,
            86400
        )
    ])

    if limited:
        return limited

    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)

    if lat is None or lng is None:
        return jsonify({
            "error": "Missing latitude or longitude"
        }), 400

    params = {
        "latlng": f"{lat},{lng}",
        "key": GOOGLE_API_KEY,
        "language": "zh-TW"
    }

    try:
        response = requests.get(
            GOOGLE_GEOCODING_API_BASE_URL,
            params=params,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        if data.get('status') == 'OK' and data.get('results'):
            formatted_address = (
                data['results'][0]['formatted_address']
            )
            return jsonify({
                "address": formatted_address
            })

        logging.warning(
            "Google Geocoding API 查詢失敗或無結果: %s",
            data.get('status')
        )
        return jsonify({
            "error": "無法解析地址",
            "details": data.get('status')
        }), 404

    except requests.exceptions.RequestException as e:
        logging.error(
            "反向地理編碼請求失敗: %s",
            e
        )
        return jsonify({
            "error": "反向地理編碼服務錯誤"
        }), 500


@app.route('/geocode_address', methods=['POST'])
def geocode_address():
    if not google_api_is_enabled():
        return google_disabled_response()

    client_ip = get_client_ip()
    limited = enforce_limits([
        (
            f"geocode-minute:{client_ip}",
            GEOCODE_PER_IP_PER_MINUTE,
            60
        ),
        (
            f"geocode-day:{client_ip}",
            GEOCODE_PER_IP_PER_DAY,
            86400
        )
    ])

    if limited:
        return limited

    data = request.get_json(silent=True) or {}
    address = (data.get('address') or '').strip()

    if not address:
        return jsonify({
            "error": "Missing address"
        }), 400

    params = {
        "address": address,
        "key": GOOGLE_API_KEY,
        "language": "zh-TW"
    }

    try:
        response = requests.get(
            GOOGLE_GEOCODING_API_BASE_URL,
            params=params,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        result = response.json()

        if (
            result.get('status') == 'OK'
            and result.get('results')
        ):
            location = (
                result['results'][0]['geometry']['location']
            )
            formatted_address = (
                result['results'][0]['formatted_address']
            )

            return jsonify({
                "lat": location['lat'],
                "lng": location['lng'],
                "formatted_address": formatted_address
            })

        logging.warning(
            "Google Geocoding API 地址轉換失敗或無結果: %s",
            result.get('status')
        )
        return jsonify({
            "error": "無法找到該地址的座標",
            "details": result.get('status')
        }), 404

    except requests.exceptions.RequestException as e:
        logging.error(
            "地址轉換請求失敗: %s",
            e
        )
        return jsonify({
            "error": "地址轉換服務錯誤"
        }), 500


@app.route('/nearby_search', methods=['POST'])
def nearby_search():
    if not google_api_is_enabled():
        return google_disabled_response()

    client_ip = get_client_ip()

    limited = enforce_limits([
        (
            f"search-minute:{client_ip}",
            SEARCHES_PER_IP_PER_MINUTE,
            60
        ),
        (
            f"search-day:{client_ip}",
            SEARCHES_PER_IP_PER_DAY,
            86400
        ),
        (
            "search-global-hour",
            SEARCHES_GLOBAL_PER_HOUR,
            3600
        ),
        (
            "search-global-day",
            SEARCHES_GLOBAL_PER_DAY,
            86400
        )
    ])

    if limited:
        logging.warning(
            "附近搜尋被 rate limit：ip=%s",
            client_ip
        )
        return limited

    data = request.get_json(silent=True) or {}

    lat = data.get('lat')
    lng = data.get('lng')
    place_type = data.get('type')
    radius = data.get('radius', 5000)

    if lat is None or lng is None or not place_type:
        return jsonify({
            "error": "Missing lat, lng, or type"
        }), 400

    try:
        lat = float(lat)
        lng = float(lng)
        radius = int(radius)
    except (TypeError, ValueError):
        return jsonify({
            "error": "Invalid lat, lng, or radius"
        }), 400

    radius = max(
        100,
        min(radius, 10000)
    )

    google_places = search_google_places_multi_point(
        origin_lat=lat,
        origin_lng=lng,
        place_type=place_type,
        radius=radius
    )

    # OSM 不使用 Google API key，也不會產生 Google Maps Platform 費用。
    osm_places = search_osm_places(
        lat=lat,
        lng=lng,
        place_type=place_type,
        radius=radius
    )

    final_unique_places = {}

    for place in google_places:
        final_unique_places[
            make_unique_key(place)
        ] = place

    for place in osm_places:
        unique_key = make_unique_key(place)

        if unique_key not in final_unique_places:
            final_unique_places[unique_key] = place

    final_places_list = list(
        final_unique_places.values()
    )

    final_places_list = [
        place
        for place in final_places_list
        if (
            place.get('distance') is not None
            and place['distance'] <= radius
        )
    ]

    # 已移除 rating / userRatingCount，
    # 避免只是為排序而把 Nearby Search 拉到更高價 SKU。
    # 現在排序以 Google 結果優先，再依距離與名稱。
    final_places_list.sort(
        key=lambda place: (
            place.get('source') == 'Google',
            -(place.get('distance') or float('inf')),
            place.get('name') or ''
        ),
        reverse=True
    )

    final_places_list = final_places_list[:MAX_RESULTS]

    attach_safe_photo_urls(
        final_places_list
    )

    logging.info(
        (
            "附近搜尋完成：ip=%s, Google=%d, "
            "OSM=%d, 回傳=%d"
        ),
        client_ip,
        len(google_places),
        len(osm_places),
        len(final_places_list)
    )

    return jsonify({
        "places": final_places_list,
        "meta": {
            "google_count": len(google_places),
            "osm_count": len(osm_places),
            "returned_count": len(final_places_list),
            "google_requests": 5,
            "google_photos_max": (
                MAX_GOOGLE_PHOTOS_PER_SEARCH
            ),
            "radius": radius
        }
    })


def search_google_places_multi_point(
    origin_lat,
    origin_lng,
    place_type,
    radius
):
    search_points = build_search_points(
        origin_lat,
        origin_lng,
        radius
    )

    sample_radius = max(
        100,
        int(radius * SAMPLE_RADIUS_RATIO)
    )

    sample_radius = min(
        sample_radius,
        radius
    )

    unique_google_places = {}

    for index, (
        sample_lat,
        sample_lng
    ) in enumerate(search_points):
        rank_preference = (
            "POPULARITY"
            if index == 0
            else "DISTANCE"
        )

        places = search_google_places_once(
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            sample_lat=sample_lat,
            sample_lng=sample_lng,
            place_type=place_type,
            sample_radius=sample_radius,
            rank_preference=rank_preference
        )

        for place in places:
            if place['distance'] > radius:
                continue

            place_id = place.get('id')

            if place_id:
                dedupe_key = (
                    f"google:{place_id}"
                )
            else:
                dedupe_key = (
                    "fallback:"
                    f"{place.get('name', '').lower().strip()}:"
                    f"{round(place['latitude'], 5)}:"
                    f"{round(place['longitude'], 5)}"
                )

            existing = unique_google_places.get(
                dedupe_key
            )

            if (
                existing is None
                or place['distance']
                < existing['distance']
            ):
                unique_google_places[
                    dedupe_key
                ] = place

    google_places = list(
        unique_google_places.values()
    )

    logging.info(
        "Google 5 點搜尋去重後 %d 筆",
        len(google_places)
    )

    return google_places


def search_google_places_once(
    origin_lat,
    origin_lng,
    sample_lat,
    sample_lng,
    place_type,
    sample_radius,
    rank_preference
):
    google_places = []

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        # 刻意不要求 rating / userRatingCount。
        "X-Goog-FieldMask": (
            "places.displayName,"
            "places.formattedAddress,"
            "places.location,"
            "places.id,"
            "places.photos"
        )
    }

    payload = {
        "includedTypes": [place_type],
        "maxResultCount": (
            GOOGLE_RESULTS_PER_REQUEST
        ),
        "rankPreference": rank_preference,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": sample_lat,
                    "longitude": sample_lng
                },
                "radius": sample_radius
            }
        },
        "languageCode": "zh-TW"
    }

    try:
        response = requests.post(
            GOOGLE_PLACES_NEARBY_SEARCH_NEW_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        google_data = response.json()

        for place in google_data.get(
            'places',
            []
        ):
            location = (
                place.get('location')
                or {}
            )

            place_lat = location.get(
                'latitude'
            )
            place_lng = location.get(
                'longitude'
            )

            display_name = (
                place.get('displayName')
                or {}
            ).get('text')

            if (
                not display_name
                or place_lat is None
                or place_lng is None
            ):
                continue

            distance = calculate_distance(
                origin_lat,
                origin_lng,
                place_lat,
                place_lng
            )

            place_id = place.get('id')

            if place_id:
                map_url = (
                    "https://www.google.com/maps/search/?api=1"
                    f"&query={quote_plus(display_name)}"
                    f"&query_place_id={quote_plus(place_id)}"
                )
            else:
                map_url = (
                    "https://www.google.com/maps/search/?api=1"
                    f"&query={quote_plus(f'{place_lat},{place_lng}')}"
                )

            photos = place.get("photos") or []
            photo_ref = None

            if photos:
                photo_ref = photos[0].get(
                    "name"
                )

            google_places.append({
                "id": place_id,
                "name": display_name,
                "formatted_address": (
                    place.get(
                        'formattedAddress'
                    )
                ),
                "vicinity": (
                    place.get(
                        'formattedAddress'
                    )
                ),
                "latitude": place_lat,
                "longitude": place_lng,
                "source": "Google",
                "distance": distance,
                # 只暫存在 server-side Python 物件，
                # 回傳前會移除。
                "_photo_ref": photo_ref,
                "photo_url": (
                    "/static/placeholder.jpg"
                ),
                "map_url": map_url,
                "food_search_url": (
                    "https://www.google.com/search?q="
                    f"{quote_plus(display_name)}"
                )
            })

    except requests.exceptions.RequestException as e:
        logging.error(
            (
                "Google Places API 搜尋失敗 "
                "(lat=%s, lng=%s, rank=%s): %s"
            ),
            sample_lat,
            sample_lng,
            rank_preference,
            e
        )
    except (
        KeyError,
        TypeError,
        ValueError
    ) as e:
        logging.error(
            "Google Places API 資料錯誤: %s",
            e
        )

    return google_places


def build_photo_token(photo_ref):
    encoded_ref = (
        base64.urlsafe_b64encode(
            photo_ref.encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )

    signature = hmac.new(
        GOOGLE_API_KEY.encode("utf-8"),
        encoded_ref.encode("ascii"),
        hashlib.sha256
    ).hexdigest()

    return encoded_ref, signature


def verify_and_decode_photo_token(
    encoded_ref,
    signature
):
    expected_signature = hmac.new(
        GOOGLE_API_KEY.encode("utf-8"),
        encoded_ref.encode("ascii"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        expected_signature,
        signature
    ):
        return None

    padding = "=" * (
        (-len(encoded_ref)) % 4
    )

    try:
        decoded = base64.urlsafe_b64decode(
            encoded_ref + padding
        ).decode("utf-8")
    except (
        ValueError,
        UnicodeDecodeError
    ):
        return None

    # 只允許 Places Photo resource name，
    # 防止把 proxy 變成任意 Google API relay。
    if (
        not decoded.startswith("places/")
        or "/photos/" not in decoded
    ):
        return None

    return decoded


def attach_safe_photo_urls(
    places
):
    used = 0

    for place in places:
        photo_ref = place.pop(
            "_photo_ref",
            None
        )

        if (
            place.get("source") == "Google"
            and photo_ref
            and used
            < MAX_GOOGLE_PHOTOS_PER_SEARCH
        ):
            encoded_ref, signature = (
                build_photo_token(
                    photo_ref
                )
            )

            place["photo_url"] = (
                "/place_photo"
                f"?ref={quote_plus(encoded_ref)}"
                f"&sig={signature}"
            )

            used += 1
        else:
            place["photo_url"] = (
                "/static/placeholder.jpg"
            )


@app.route('/place_photo')
def place_photo():
    if not google_api_is_enabled():
        return google_disabled_response()

    client_ip = get_client_ip()

    limited = enforce_limits([
        (
            f"photo-minute:{client_ip}",
            PHOTO_PER_IP_PER_MINUTE,
            60
        ),
        (
            f"photo-day:{client_ip}",
            PHOTO_PER_IP_PER_DAY,
            86400
        ),
        (
            "photo-global-hour",
            PHOTO_GLOBAL_PER_HOUR,
            3600
        ),
        (
            "photo-global-day",
            PHOTO_GLOBAL_PER_DAY,
            86400
        )
    ])

    if limited:
        return limited

    encoded_ref = (
        request.args.get("ref")
        or ""
    ).strip()

    signature = (
        request.args.get("sig")
        or ""
    ).strip()

    if not encoded_ref or not signature:
        return jsonify({
            "error": "Invalid photo token"
        }), 400

    photo_ref = (
        verify_and_decode_photo_token(
            encoded_ref,
            signature
        )
    )

    if not photo_ref:
        return jsonify({
            "error": "Invalid photo token"
        }), 403

    google_url = (
        f"https://places.googleapis.com/v1/"
        f"{photo_ref}/media"
    )

    params = {
        "key": GOOGLE_API_KEY,
        "maxWidthPx": 400
    }

    try:
        google_response = requests.get(
            google_url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
        google_response.raise_for_status()

        content_type = (
            google_response.headers.get(
                "Content-Type"
            )
            or "image/jpeg"
        )

        response = Response(
            google_response.content,
            status=200,
            content_type=content_type
        )

        # 不在自己的伺服器長期快取 Places Photo。
        # 瀏覽器只允許短暫快取，主要目的為避免單頁重複載入。
        response.headers[
            "Cache-Control"
        ] = "private, max-age=300"

        return response

    except requests.exceptions.RequestException as e:
        logging.error(
            "Place Photo 代理失敗: %s",
            e
        )

        # 圖片 API 出問題時直接回 placeholder，
        # 不讓前端出現破圖。
        try:
            with open(
                os.path.join(
                    app.static_folder,
                    "placeholder.jpg"
                ),
                "rb"
            ) as file:
                return Response(
                    file.read(),
                    status=200,
                    content_type="image/jpeg"
                )
        except OSError:
            return jsonify({
                "error": "圖片服務暫時不可用"
            }), 502


def build_search_points(
    lat,
    lng,
    radius
):
    offset_meters = (
        radius
        * SEARCH_POINT_OFFSET_RATIO
    )

    lat_delta = (
        offset_meters
        / 111320.0
    )

    cos_lat = math.cos(
        math.radians(lat)
    )
    cos_lat = max(
        abs(cos_lat),
        0.01
    )

    lng_delta = (
        offset_meters
        / (
            111320.0
            * cos_lat
        )
    )

    return [
        (lat, lng),
        (lat + lat_delta, lng),
        (lat - lat_delta, lng),
        (lat, lng + lng_delta),
        (lat, lng - lng_delta)
    ]


def search_osm_places(
    lat,
    lng,
    place_type,
    radius
):
    osm_places = []

    osm_tags_map = {
        "restaurant": {
            "amenity": "restaurant"
        },
        "cafe": {
            "amenity": "cafe"
        },
        "bar": {
            "amenity": "bar"
        },
        "bakery": {
            "shop": "bakery"
        },
        "meal_delivery": {
            "amenity": "food_court"
        },
        "meal_takeaway": {
            "amenity": "fast_food"
        },
        "amusement_park": {
            "leisure": "amusement_park"
        },
        "park": {
            "leisure": "park"
        },
        "museum": {
            "tourism": "museum"
        },
        "movie_theater": {
            "amenity": "cinema"
        },
        "bowling_alley": {
            "leisure": "bowling_alley"
        },
        "shopping_mall": {
            "shop": "mall"
        },
        "spa": {
            "amenity": "spa"
        },
        "beauty_salon": {
            "shop": "beauty"
        },
        "gym": {
            "leisure": "fitness_centre"
        },
        "zoo": {
            "tourism": "zoo"
        },
        "tourist_attraction": {
            "tourism": "attraction"
        },
        "night_club": {
            "amenity": "nightclub"
        },
        "aquarium": {
            "tourism": "aquarium"
        },
        "art_gallery": {
            "tourism": "art_gallery"
        },
        "casino": {
            "amenity": "casino"
        }
    }

    osm_tag = osm_tags_map.get(
        place_type
    )

    if not osm_tag:
        return osm_places

    osm_key = next(
        iter(osm_tag)
    )
    osm_value = osm_tag[
        osm_key
    ]

    overpass_query = f"""
        [out:json];
        (
          node["{osm_key}"="{osm_value}"](around:{radius},{lat},{lng});
          way["{osm_key}"="{osm_value}"](around:{radius},{lat},{lng});
          relation["{osm_key}"="{osm_value}"](around:{radius},{lat},{lng});
        );
        out center;
    """

    try:
        response = requests.post(
            OVERPASS_API_BASE_URL,
            data=overpass_query,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        osm_data = response.json()

        for element in osm_data.get(
            'elements',
            []
        ):
            tags = (
                element.get('tags')
                or {}
            )
            center = (
                element.get('center')
                or {}
            )

            element_lat = (
                element.get('lat')
            )
            element_lng = (
                element.get('lon')
            )

            if element_lat is None:
                element_lat = (
                    center.get('lat')
                )

            if element_lng is None:
                element_lng = (
                    center.get('lon')
                )

            if (
                element_lat is None
                or element_lng is None
            ):
                continue

            name = tags.get(
                'name',
                'N/A'
            )

            if (
                name == 'N/A'
                and place_type not in [
                    "park",
                    "tourist_attraction"
                ]
            ):
                continue

            address = build_osm_address(
                tags
            )

            osm_places.append({
                "id": (
                    f"osm-{element['id']}"
                ),
                "osm_id": element['id'],
                "name": name,
                "formatted_address": address,
                "vicinity": address,
                "latitude": element_lat,
                "longitude": element_lng,
                "source": "OSM",
                "distance": calculate_distance(
                    lat,
                    lng,
                    element_lat,
                    element_lng
                ),
                "photo_url": (
                    "/static/placeholder.jpg"
                ),
                "url": (
                    "https://www.openstreetmap.org/"
                    f"?mlat={element_lat}"
                    f"&mlon={element_lng}"
                    "&zoom=18"
                ),
                "food_search_url": (
                    "https://www.google.com/search?q="
                    f"{quote_plus(name)}"
                )
            })

    except requests.exceptions.RequestException as e:
        logging.error(
            "OSM Overpass API 搜尋失敗: %s",
            e
        )
    except (
        KeyError,
        TypeError,
        ValueError
    ) as e:
        logging.error(
            "OSM 資料處理失敗: %s",
            e
        )

    return osm_places


def build_osm_address(tags):
    if tags.get('addr:full'):
        return (
            tags['addr:full'].strip()
        )

    parts = []

    if tags.get('addr:city'):
        parts.append(
            tags['addr:city']
        )

    if tags.get('addr:district'):
        parts.append(
            tags['addr:district']
        )

    street = tags.get(
        'addr:street'
    )
    house_number = tags.get(
        'addr:housenumber'
    )

    if street:
        if house_number:
            parts.append(
                f"{street} {house_number}"
            )
        else:
            parts.append(
                street
            )

    if (
        not parts
        and tags.get('addr:housename')
    ):
        parts.append(
            tags['addr:housename']
        )

    return (
        ", ".join(parts)
        if parts
        else "無地址資訊"
    )


def make_unique_key(place):
    if (
        place.get('source') == 'Google'
        and place.get('id')
    ):
        return (
            'google',
            place['id']
        )

    name = (
        place.get('name')
        or ''
    ).lower().strip()

    address = (
        place.get(
            'formatted_address'
        )
        or place.get('vicinity')
        or ''
    ).lower().strip()

    lat = float(
        place.get('latitude')
    )
    lng = float(
        place.get('longitude')
    )

    return (
        name,
        address,
        round(lat, 5),
        round(lng, 5)
    )


def calculate_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    earth_radius = 6371000

    phi1 = math.radians(
        lat1
    )
    phi2 = math.radians(
        lat2
    )

    delta_phi = math.radians(
        lat2 - lat1
    )

    delta_lambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(
            delta_phi / 2
        ) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(
            delta_lambda / 2
        ) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return (
        earth_radius
        * c
    )


if __name__ == '__main__':
    port = int(
        os.getenv(
            "PORT",
            "5031"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
