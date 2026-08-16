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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv(
    "GOOGLE_API_KEY",
    ""
).strip()

UPSTASH_URL = os.getenv(
    "UPSTASH_REDIS_REST_URL",
    ""
).rstrip("/")

UPSTASH_TOKEN = os.getenv(
    "UPSTASH_REDIS_REST_TOKEN",
    ""
).strip()

if not GOOGLE_API_KEY:
    raise ValueError(
        "GOOGLE_API_KEY 未設定"
    )

GOOGLE_API_ENABLED = (
    os.getenv(
        "GOOGLE_API_ENABLED",
        "true"
    ).lower()
    in {
        "1",
        "true",
        "yes",
        "on"
    }
)

GEOCODE_URL = (
    "https://maps.googleapis.com/"
    "maps/api/geocode/json"
)

NEARBY_URL = (
    "https://places.googleapis.com/"
    "v1/places:searchNearby"
)

OVERPASS_URL = (
    "https://overpass-api.de/"
    "api/interpreter"
)

REQUEST_TIMEOUT = 15

MAX_RESULTS = 60

MAX_RADIUS = 15000

GOOGLE_RESULTS_PER_REQUEST = 20


# =========================
# 每月安全上限
# =========================
#
# 不會顯示在前端。
#
# Nearby Search Pro：
# Google 免費 5000
# 我們 4500 停止
#
# Photo：
# Google 免費 1000
# 我們 900 停止
#
# Geocoding：
# Google 免費 10000
# 我們 9000 停止
#
# 留約 10% buffer。
#

MONTHLY_NEARBY_LIMIT = int(
    os.getenv(
        "MONTHLY_NEARBY_LIMIT",
        "4500"
    )
)

MONTHLY_PHOTO_LIMIT = int(
    os.getenv(
        "MONTHLY_PHOTO_LIMIT",
        "900"
    )
)

MONTHLY_GEOCODE_LIMIT = int(
    os.getenv(
        "MONTHLY_GEOCODE_LIMIT",
        "9000"
    )
)

PACIFIC = ZoneInfo(
    "America/Los_Angeles"
)


class BudgetStoreError(Exception):
    pass


def billing_month():
    """
    Google Maps 免費額度使用
    Pacific Time 每月重新計算。

    Redis key 會長這樣：

    location_finder:google:2026-08:nearby

    到下個月自然會換成：

    location_finder:google:2026-09:nearby
    """

    return datetime.now(
        PACIFIC
    ).strftime(
        "%Y-%m"
    )


def redis_cmd(command):
    """
    呼叫 Upstash Redis REST API。

    如果 Upstash 掛掉，
    Google API 直接 fail-closed。

    也就是寧願暫時不能搜尋，
    也不要失去額度保護。
    """

    if (
        not UPSTASH_URL
        or not UPSTASH_TOKEN
    ):
        raise BudgetStoreError(
            "Upstash 環境變數未設定"
        )

    try:
        response = requests.post(
            UPSTASH_URL,
            headers={
                "Authorization":
                    f"Bearer {UPSTASH_TOKEN}",

                "Content-Type":
                    "application/json",
            },
            json=command,
            timeout=5,
        )

        response.raise_for_status()

        data = response.json()

    except (
        requests.RequestException,
        ValueError
    ) as exc:

        raise BudgetStoreError(
            str(exc)
        ) from exc

    if data.get(
        "error"
    ):
        raise BudgetStoreError(
            data["error"]
        )

    return data.get(
        "result"
    )


def reserve_budget(
    kind,
    amount,
    limit
):
    """
    在真正呼叫 Google 前，
    先保留本月 API 額度。

    Redis INCRBY 是 atomic。

    例如附近搜尋：

    current = 4495
    reserve +5
    => 4500
    可以搜尋

    下一個：

    4500 + 5
    => 4505
    超過限制

    rollback -5
    => 4500

    並且完全不呼叫 Google。
    """

    key = (
        "location_finder:"
        f"google:{billing_month()}:{kind}"
    )

    value = int(
        redis_cmd([
            "INCRBY",
            key,
            int(amount)
        ])
    )

    if value <= limit:
        return True

    try:
        redis_cmd([
            "INCRBY",
            key,
            -int(amount)
        ])

    except BudgetStoreError:
        logging.exception(
            "Budget rollback failed: %s",
            key
        )

    return False


def fail_closed():
    return jsonify({
        "error":
            "使用量保護服務暫時無法確認額度，"
            "為避免產生額外費用，"
            "Google API 已暫停。"
    }), 503


def month_limit(
    service
):
    return jsonify({
        "error":
            f"{service}本月使用量已達安全上限，"
            "將於下個月自動恢復。"
    }), 503


# =========================
# 首頁
# =========================

@app.route("/")
def index():
    return render_template(
        "index.html"
    )


# =========================
# GPS 座標 -> 地址
# =========================

@app.route(
    "/reverse_geocode"
)
def reverse_geocode():

    if not GOOGLE_API_ENABLED:
        return jsonify({
            "error":
                "Google API 暫時停用。"
        }), 503

    lat = request.args.get(
        "lat",
        type=float
    )

    lng = request.args.get(
        "lng",
        type=float
    )

    if (
        lat is None
        or lng is None
    ):
        return jsonify({
            "error":
                "Missing latitude or longitude"
        }), 400

    try:

        allowed = reserve_budget(
            "geocode",
            1,
            MONTHLY_GEOCODE_LIMIT
        )

        if not allowed:
            return month_limit(
                "地址定位服務"
            )

    except BudgetStoreError:

        logging.exception(
            "Geocode budget store unavailable"
        )

        return fail_closed()

    try:

        response = requests.get(
            GEOCODE_URL,

            params={
                "latlng":
                    f"{lat},{lng}",

                "key":
                    GOOGLE_API_KEY,

                "language":
                    "zh-TW"
            },

            timeout=
                REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        if (
            data.get(
                "status"
            ) == "OK"
            and data.get(
                "results"
            )
        ):

            return jsonify({
                "address":
                    data[
                        "results"
                    ][0][
                        "formatted_address"
                    ]
            })

        return jsonify({
            "error":
                "無法解析地址",

            "details":
                data.get(
                    "status"
                )
        }), 404

    except requests.RequestException:

        logging.exception(
            "Reverse geocode failed"
        )

        return jsonify({
            "error":
                "反向地理編碼服務錯誤"
        }), 500


# =========================
# 地址 -> GPS 座標
# =========================

@app.route(
    "/geocode_address",
    methods=["POST"]
)
def geocode_address():

    if not GOOGLE_API_ENABLED:

        return jsonify({
            "error":
                "Google API 暫時停用。"
        }), 503

    body = (
        request.get_json(
            silent=True
        )
        or {}
    )

    address = (
        body.get(
            "address"
        )
        or ""
    ).strip()

    if not address:

        return jsonify({
            "error":
                "Missing address"
        }), 400

    try:

        allowed = reserve_budget(
            "geocode",
            1,
            MONTHLY_GEOCODE_LIMIT
        )

        if not allowed:

            return month_limit(
                "地址定位服務"
            )

    except BudgetStoreError:

        logging.exception(
            "Geocode budget store unavailable"
        )

        return fail_closed()

    try:

        response = requests.get(
            GEOCODE_URL,

            params={
                "address":
                    address,

                "key":
                    GOOGLE_API_KEY,

                "language":
                    "zh-TW"
            },

            timeout=
                REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        if (
            data.get(
                "status"
            ) == "OK"
            and data.get(
                "results"
            )
        ):

            first = (
                data[
                    "results"
                ][0]
            )

            location = (
                first[
                    "geometry"
                ][
                    "location"
                ]
            )

            return jsonify({
                "lat":
                    location[
                        "lat"
                    ],

                "lng":
                    location[
                        "lng"
                    ],

                "formatted_address":
                    first[
                        "formatted_address"
                    ]
            })

        return jsonify({
            "error":
                "無法找到該地址的座標",

            "details":
                data.get(
                    "status"
                )
        }), 404

    except requests.RequestException:

        logging.exception(
            "Geocode address failed"
        )

        return jsonify({
            "error":
                "地址轉換服務錯誤"
        }), 500


# =========================
# 附近搜尋
# =========================

@app.route(
    "/nearby_search",
    methods=["POST"]
)
def nearby_search():

    if not GOOGLE_API_ENABLED:

        return jsonify({
            "error":
                "Google API 暫時停用。"
        }), 503

    body = (
        request.get_json(
            silent=True
        )
        or {}
    )

    lat = body.get(
        "lat"
    )

    lng = body.get(
        "lng"
    )

    place_type = body.get(
        "type"
    )

    radius = body.get(
        "radius",
        5000
    )

    if (
        lat is None
        or lng is None
        or not place_type
    ):

        return jsonify({
            "error":
                "Missing lat, lng, or type"
        }), 400

    try:

        lat = float(
            lat
        )

        lng = float(
            lng
        )

        radius = int(
            radius
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "error":
                "Invalid lat, lng, or radius"
        }), 400

    radius = max(
        100,
        min(
            radius,
            MAX_RADIUS
        )
    )

    # -------------------------
    # 一次網站搜尋固定 5 個點
    #
    # center
    # north
    # south
    # east
    # west
    #
    # 所以先一次保留
    # 5 次 Nearby API 額度。
    # -------------------------

    try:

        allowed = reserve_budget(
            "nearby",
            5,
            MONTHLY_NEARBY_LIMIT
        )

        if not allowed:

            return month_limit(
                "附近搜尋服務"
            )

    except BudgetStoreError:

        logging.exception(
            "Nearby budget store unavailable"
        )

        return fail_closed()

    google_places = (
        google_multi_search(
            lat,
            lng,
            place_type,
            radius
        )
    )

    osm_places = (
        osm_search(
            lat,
            lng,
            place_type,
            radius
        )
    )

    merged = {}

    for place in google_places:

        merged[
            unique_key(
                place
            )
        ] = place

    for place in osm_places:

        merged.setdefault(
            unique_key(
                place
            ),
            place
        )

    places = [
        place
        for place
        in merged.values()

        if (
            place.get(
                "distance"
            )
            is not None

            and place[
                "distance"
            ] <= radius
        )
    ]

    places.sort(
        key=lambda place: (

            0
            if place.get(
                "source"
            ) == "Google"
            else 1,

            place.get(
                "distance",
                float(
                    "inf"
                )
            ),

            place.get(
                "name"
            )
            or ""
        )
    )

    places = (
        places[
            :MAX_RESULTS
        ]
    )

    add_photo_proxy_urls(
        places
    )

    # 注意：
    # 不回傳任何 API quota、
    # 使用率或剩餘百分比。

    return jsonify({
        "places":
            places
    })


# =========================
# Google 多點搜尋
# =========================

def google_multi_search(
    origin_lat,
    origin_lng,
    place_type,
    radius
):

    points = search_points(
        origin_lat,
        origin_lng,
        radius
    )

    sample_radius = min(
        radius,

        max(
            100,
            int(
                radius * 0.8
            )
        )
    )

    unique = {}

    for index, (
        sample_lat,
        sample_lng
    ) in enumerate(
        points
    ):

        rank = (
            "POPULARITY"
            if index == 0
            else "DISTANCE"
        )

        results = (
            google_search_once(
                origin_lat,
                origin_lng,

                sample_lat,
                sample_lng,

                place_type,
                sample_radius,

                rank
            )
        )

        for place in results:

            if (
                place[
                    "distance"
                ] > radius
            ):
                continue

            place_id = (
                place.get(
                    "id"
                )
            )

            if place_id:

                key = (
                    f"google:"
                    f"{place_id}"
                )

            else:

                key = (
                    f"{place.get('name', '').lower()}:"
                    f"{round(place['latitude'], 5)}:"
                    f"{round(place['longitude'], 5)}"
                )

            old = unique.get(
                key
            )

            if (
                old is None
                or place[
                    "distance"
                ]
                <
                old[
                    "distance"
                ]
            ):

                unique[
                    key
                ] = place

    return list(
        unique.values()
    )


def google_search_once(
    origin_lat,
    origin_lng,

    lat,
    lng,

    place_type,
    radius,

    rank
):

    headers = {

        "Content-Type":
            "application/json",

        "X-Goog-Api-Key":
            GOOGLE_API_KEY,

        # 刻意不抓 rating、
        # userRatingCount。
        #
        # 避免把搜尋拉到更高價欄位。
        "X-Goog-FieldMask":
            (
                "places.displayName,"
                "places.formattedAddress,"
                "places.location,"
                "places.id,"
                "places.photos"
            ),
    }

    payload = {

        "includedTypes": [
            place_type
        ],

        "maxResultCount":
            GOOGLE_RESULTS_PER_REQUEST,

        "rankPreference":
            rank,

        "locationRestriction": {
            "circle": {

                "center": {
                    "latitude":
                        lat,

                    "longitude":
                        lng
                },

                "radius":
                    radius,
            }
        },

        "languageCode":
            "zh-TW",
    }

    try:

        response = requests.post(
            NEARBY_URL,

            headers=
                headers,

            json=
                payload,

            timeout=
                REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = (
            response.json()
        )

    except requests.RequestException:

        logging.exception(
            "Google Nearby Search failed"
        )

        return []

    output = []

    for place in data.get(
        "places",
        []
    ):

        location = (
            place.get(
                "location"
            )
            or {}
        )

        place_lat = (
            location.get(
                "latitude"
            )
        )

        place_lng = (
            location.get(
                "longitude"
            )
        )

        name = (
            place.get(
                "displayName"
            )
            or {}
        ).get(
            "text"
        )

        if (
            not name
            or place_lat is None
            or place_lng is None
        ):
            continue

        place_id = (
            place.get(
                "id"
            )
        )

        if place_id:

            map_url = (
                "https://www.google.com/"
                "maps/search/?api=1"
                f"&query={quote_plus(name)}"
                f"&query_place_id="
                f"{quote_plus(place_id)}"
            )

        else:

            map_url = (
                "https://www.google.com/"
                "maps/search/?api=1"
                f"&query="
                f"{quote_plus(f'{place_lat},{place_lng}')}"
            )

        photos = (
            place.get(
                "photos"
            )
            or []
        )

        photo_ref = (
            photos[0].get(
                "name"
            )
            if photos
            else None
        )

        output.append({

            "id":
                place_id,

            "name":
                name,

            "formatted_address":
                place.get(
                    "formattedAddress"
                ),

            "vicinity":
                place.get(
                    "formattedAddress"
                ),

            "latitude":
                place_lat,

            "longitude":
                place_lng,

            "source":
                "Google",

            "distance":
                distance_m(
                    origin_lat,
                    origin_lng,

                    place_lat,
                    place_lng
                ),

            "_photo_ref":
                photo_ref,

            "photo_url":
                "/static/placeholder.jpg",

            "map_url":
                map_url,
        })

    return output


# =========================
# Google Photo
# =========================

def photo_token(
    photo_ref
):

    encoded = (
        base64
        .urlsafe_b64encode(
            photo_ref.encode()
        )
        .decode()
        .rstrip("=")
    )

    signature = hmac.new(

        GOOGLE_API_KEY.encode(),

        encoded.encode(),

        hashlib.sha256

    ).hexdigest()

    return (
        encoded,
        signature
    )


def decode_photo_token(
    encoded,
    signature
):

    expected = hmac.new(

        GOOGLE_API_KEY.encode(),

        encoded.encode(),

        hashlib.sha256

    ).hexdigest()

    if not hmac.compare_digest(
        expected,
        signature
    ):
        return None

    try:

        raw = (
            base64
            .urlsafe_b64decode(

                encoded
                +
                "="
                *
                (
                    (
                        -len(
                            encoded
                        )
                    )
                    % 4
                )
            )
            .decode()
        )

    except (
        ValueError,
        UnicodeDecodeError
    ):

        return None

    if (
        raw.startswith(
            "places/"
        )

        and
        "/photos/"
        in raw
    ):

        return raw

    return None


def add_photo_proxy_urls(
    places
):

    for place in places:

        photo_ref = (
            place.pop(
                "_photo_ref",
                None
            )
        )

        if (
            place.get(
                "source"
            ) == "Google"

            and photo_ref
        ):

            encoded, signature = (
                photo_token(
                    photo_ref
                )
            )

            place[
                "photo_url"
            ] = (
                "/place_photo"
                f"?ref={quote_plus(encoded)}"
                f"&sig={signature}"
            )

        else:

            place[
                "photo_url"
            ] = (
                "/static/placeholder.jpg"
            )


@app.route(
    "/place_photo"
)
def place_photo():

    """
    Browser
      ↓
    Render /place_photo
      ↓
    驗證 signed token
      ↓
    Redis 月度 Photo counter
      ↓
    Google Place Photo
      ↓
    取得 photoUri
      ↓
    302 到 Google 圖片 CDN

    API key 永遠不出現在 Browser URL。
    """

    if not GOOGLE_API_ENABLED:

        return redirect(
            "/static/placeholder.jpg",
            code=302
        )

    encoded = (
        request.args.get(
            "ref"
        )
        or ""
    ).strip()

    signature = (
        request.args.get(
            "sig"
        )
        or ""
    ).strip()

    photo_ref = (

        decode_photo_token(
            encoded,
            signature
        )

        if (
            encoded
            and signature
        )

        else None
    )

    if not photo_ref:

        return redirect(
            "/static/placeholder.jpg",
            code=302
        )

    try:

        allowed = reserve_budget(
            "photo",
            1,
            MONTHLY_PHOTO_LIMIT
        )

        if not allowed:

            # Photo 免費額度接近上限。
            #
            # 不關掉整個網站，
            # 直接全部變鹿圖。
            #
            # 因此不再產生
            # Google Photo API request。

            return redirect(
                "/static/placeholder.jpg",
                code=302
            )

    except BudgetStoreError:

        logging.exception(
            "Photo budget store unavailable"
        )

        # fail closed

        return redirect(
            "/static/placeholder.jpg",
            code=302
        )

    try:

        response = requests.get(

            (
                "https://places.googleapis.com/"
                f"v1/{photo_ref}/media"
            ),

            params={

                "key":
                    GOOGLE_API_KEY,

                "maxWidthPx":
                    400,

                "skipHttpRedirect":
                    "true",
            },

            timeout=
                REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        photo_uri = (
            response
            .json()
            .get(
                "photoUri"
            )
        )

        if not photo_uri:

            return redirect(
                "/static/placeholder.jpg",
                code=302
            )

        parsed = urlparse(
            photo_uri
        )

        hostname = (
            parsed.hostname
            or ""
        ).lower()

        # 防止 open redirect。
        #
        # 只接受 Google 圖片 CDN。

        if (
            parsed.scheme
            !=
            "https"

            or not (

                hostname
                ==
                "googleusercontent.com"

                or

                hostname.endswith(
                    ".googleusercontent.com"
                )
            )
        ):

            logging.warning(
                "Unexpected photo host rejected: %s",
                hostname
            )

            return redirect(
                "/static/placeholder.jpg",
                code=302
            )

        result = redirect(
            photo_uri,
            code=302
        )

        result.headers[
            "Cache-Control"
        ] = (
            "private, max-age=300"
        )

        return result

    except (
        requests.RequestException,
        ValueError
    ):

        logging.exception(
            "Place Photo failed"
        )

        return redirect(
            "/static/placeholder.jpg",
            code=302
        )


# =========================
# 5 點搜尋座標
# =========================

def search_points(
    lat,
    lng,
    radius
):

    offset = (
        radius
        *
        0.45
    )

    lat_delta = (
        offset
        /
        111320.0
    )

    cos_lat = max(

        abs(
            math.cos(
                math.radians(
                    lat
                )
            )
        ),

        0.01
    )

    lng_delta = (
        offset
        /
        (
            111320.0
            *
            cos_lat
        )
    )

    return [

        (
            lat,
            lng
        ),

        (
            lat + lat_delta,
            lng
        ),

        (
            lat - lat_delta,
            lng
        ),

        (
            lat,
            lng + lng_delta
        ),

        (
            lat,
            lng - lng_delta
        ),
    ]


# =========================
# OSM
# =========================

def osm_search(
    lat,
    lng,
    place_type,
    radius
):

    tags = {

        "restaurant":
            (
                "amenity",
                "restaurant"
            ),

        "cafe":
            (
                "amenity",
                "cafe"
            ),

        "bar":
            (
                "amenity",
                "bar"
            ),

        "bakery":
            (
                "shop",
                "bakery"
            ),

        "meal_delivery":
            (
                "amenity",
                "food_court"
            ),

        "meal_takeaway":
            (
                "amenity",
                "fast_food"
            ),

        "amusement_park":
            (
                "leisure",
                "amusement_park"
            ),

        "park":
            (
                "leisure",
                "park"
            ),

        "museum":
            (
                "tourism",
                "museum"
            ),

        "movie_theater":
            (
                "amenity",
                "cinema"
            ),

        "bowling_alley":
            (
                "leisure",
                "bowling_alley"
            ),

        "shopping_mall":
            (
                "shop",
                "mall"
            ),

        "spa":
            (
                "amenity",
                "spa"
            ),

        "beauty_salon":
            (
                "shop",
                "beauty"
            ),

        "gym":
            (
                "leisure",
                "fitness_centre"
            ),

        "zoo":
            (
                "tourism",
                "zoo"
            ),

        "tourist_attraction":
            (
                "tourism",
                "attraction"
            ),

        "night_club":
            (
                "amenity",
                "nightclub"
            ),
    }

    if (
        place_type
        not in tags
    ):
        return []

    key, value = (
        tags[
            place_type
        ]
    )

    query = f"""
    [out:json];

    (
      node["{key}"="{value}"]
      (around:{radius},{lat},{lng});

      way["{key}"="{value}"]
      (around:{radius},{lat},{lng});

      relation["{key}"="{value}"]
      (around:{radius},{lat},{lng});
    );

    out center;
    """

    try:

        response = requests.post(
            OVERPASS_URL,

            data=
                query,

            timeout=
                REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = (
            response.json()
        )

    except (
        requests.RequestException,
        ValueError
    ):

        logging.exception(
            "OSM Overpass failed"
        )

        return []

    output = []

    for element in data.get(
        "elements",
        []
    ):

        item_tags = (
            element.get(
                "tags"
            )
            or {}
        )

        center = (
            element.get(
                "center"
            )
            or {}
        )

        place_lat = element.get(
            "lat",
            center.get(
                "lat"
            )
        )

        place_lng = element.get(
            "lon",
            center.get(
                "lon"
            )
        )

        if (
            place_lat is None
            or place_lng is None
        ):
            continue

        name = (
            item_tags.get(
                "name",
                "N/A"
            )
        )

        if (
            name == "N/A"

            and place_type not in {
                "park",
                "tourist_attraction"
            }
        ):
            continue

        address = osm_address(
            item_tags
        )

        output.append({

            "id":
                f"osm-{element['id']}",

            "osm_id":
                element[
                    "id"
                ],

            "name":
                name,

            "formatted_address":
                address,

            "vicinity":
                address,

            "latitude":
                place_lat,

            "longitude":
                place_lng,

            "source":
                "OSM",

            "distance":
                distance_m(
                    lat,
                    lng,

                    place_lat,
                    place_lng
                ),

            "photo_url":
                "/static/placeholder.jpg",

            "url":
                (
                    "https://www.openstreetmap.org/"
                    f"?mlat={place_lat}"
                    f"&mlon={place_lng}"
                    "&zoom=18"
                ),
        })

    return output


def osm_address(
    tags
):

    if tags.get(
        "addr:full"
    ):

        return (
            tags[
                "addr:full"
            ].strip()
        )

    parts = [

        tags[key]

        for key in (
            "addr:city",
            "addr:district"
        )

        if tags.get(
            key
        )
    ]

    street = tags.get(
        "addr:street"
    )

    number = tags.get(
        "addr:housenumber"
    )

    if street:

        parts.append(
            (
                f"{street} {number}"
                if number
                else street
            )
        )

    if parts:

        return ", ".join(
            parts
        )

    return "無地址資訊"


# =========================
# 去重
# =========================

def unique_key(
    place
):

    if (
        place.get(
            "source"
        ) == "Google"

        and

        place.get(
            "id"
        )
    ):

        return (
            "google",
            place[
                "id"
            ]
        )

    return (

        (
            place.get(
                "name"
            )
            or ""
        )
        .lower()
        .strip(),

        (
            place.get(
                "formatted_address"
            )
            or ""
        )
        .lower()
        .strip(),

        round(
            float(
                place[
                    "latitude"
                ]
            ),
            5
        ),

        round(
            float(
                place[
                    "longitude"
                ]
            ),
            5
        ),
    )


# =========================
# 距離
# =========================

def distance_m(
    lat1,
    lon1,
    lat2,
    lon2
):

    earth_radius = (
        6371000
    )

    p1 = math.radians(
        lat1
    )

    p2 = math.radians(
        lat2
    )

    delta_lat = math.radians(
        lat2 - lat1
    )

    delta_lon = math.radians(
        lon2 - lon1
    )

    a = (

        math.sin(
            delta_lat / 2
        ) ** 2

        +

        math.cos(
            p1
        )

        *

        math.cos(
            p2
        )

        *

        math.sin(
            delta_lon / 2
        ) ** 2
    )

    return (

        earth_radius
        *
        2
        *
        math.atan2(

            math.sqrt(
                a
            ),

            math.sqrt(
                1 - a
            )
        )
    )


if __name__ == "__main__":

    app.run(

        host=
            "0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                "5031"
            )
        ),
    )
