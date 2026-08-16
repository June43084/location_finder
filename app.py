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
    format="%(asctime)s - %(levelname)s - %(message)s",
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
    )
    .strip()
    .lower()
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


# ==========================================
# 五區均衡
#
# 中心 20
# 北 10
# 南 10
# 東 10
# 西 10
#
# 最多 60 家
# ==========================================

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


# ==========================================
# 每月免費額度安全停止值
# ==========================================


# 一般 Nearby Search Pro
#
# 免費額度 5000
# 我們 4500 停止

MONTHLY_NEARBY_LIMIT = int(

    os.getenv(

        "MONTHLY_NEARBY_LIMIT",

        "4500"

    )

)


# 勾選「只看目前營業中」
#
# 需要 currentOpeningHours
# 使用 Nearby Search Enterprise
#
# 免費額度 1000
# 我們 900 停止

MONTHLY_NEARBY_ENTERPRISE_LIMIT = int(

    os.getenv(

        "MONTHLY_NEARBY_ENTERPRISE_LIMIT",

        "900"

    )

)


# Place Photos
#
# 免費 1000
# 900 停止

MONTHLY_PHOTO_LIMIT = int(

    os.getenv(

        "MONTHLY_PHOTO_LIMIT",

        "900"

    )

)


# Geocoding
#
# 免費 10000
# 9000 停止

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


# ==========================================
# 月份
# ==========================================

def billing_month():

    return datetime.now(
        PACIFIC
    ).strftime(
        "%Y-%m"
    )


# ==========================================
# Upstash
# ==========================================

def redis_cmd(
    command
):

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

            json=
                command,

            timeout=
                5,
        )


        response.raise_for_status()


        data = (
            response.json()
        )


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


# ==========================================
# 保留 API 月度額度
# ==========================================

def reserve_budget(
    kind,
    amount,
    limit
):

    key = (

        "location_finder:"
        f"google:{billing_month()}:{kind}"

    )


    try:

        new_value = int(

            redis_cmd([

                "INCRBY",

                key,

                int(
                    amount
                )

            ])

        )


    except (
        TypeError,
        ValueError
    ) as exc:

        raise BudgetStoreError(
            "Upstash counter 回傳格式錯誤"
        ) from exc


    if (
        new_value <=
        limit
    ):

        return True


    # 超過就 rollback
    #
    # Google API 完全不會被呼叫

    try:

        redis_cmd([

            "INCRBY",

            key,

            -int(
                amount
            )

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


# ==========================================
# 首頁
# ==========================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# ==========================================
# GPS -> 地址
# ==========================================

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
                    "zh-TW",
            },

            timeout=
                REQUEST_TIMEOUT,
        )


        response.raise_for_status()


        data = (
            response.json()
        )


        if (
            data.get(
                "status"
            ) == "OK"

            and

            data.get(
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


# ==========================================
# 地址 -> GPS
# ==========================================

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
                    "zh-TW",
            },

            timeout=
                REQUEST_TIMEOUT,
        )


        response.raise_for_status()


        data = (
            response.json()
        )


        if (
            data.get(
                "status"
            ) == "OK"

            and

            data.get(
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


# ==========================================
# Nearby Search
# ==========================================

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

    open_now_only = bool(
        body.get(
            "open_now",
            False
        )
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


    # ======================================
    # 每次網站搜尋固定打 5 次 Google
    #
    # 一般搜尋：
    # nearby +5
    #
    # 營業中：
    # nearby_enterprise +5
    # ======================================

    try:

        if open_now_only:

            allowed = reserve_budget(

                "nearby_enterprise",

                5,

                MONTHLY_NEARBY_ENTERPRISE_LIMIT

            )


            if not allowed:

                return month_limit(
                    "營業中篩選服務"
                )


        else:

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


    google_places = google_multi_search(

        origin_lat=
            lat,

        origin_lng=
            lng,

        place_type=
            place_type,

        radius=
            radius,

        open_now_only=
            open_now_only,
    )


    places = list(
        google_places
    )


    # ======================================
    # 只有一般搜尋可以用 OSM 補到 60 家
    #
    # 營業中模式不使用 OSM：
    #
    # 因為無法可靠確認 OSM 店家
    # 此刻是否真的營業中。
    # ======================================

    if (
        not open_now_only

        and

        len(
            places
        ) < MAX_RESULTS
    ):

        osm_places = osm_search(

            lat=
                lat,

            lng=
                lng,

            place_type=
                place_type,

            radius=
                radius,
        )


        used_keys = {

            unique_key(
                place
            )

            for place in places

        }


        osm_places = [

            place

            for place in osm_places

            if (

                place.get(
                    "distance"
                )
                is not None

                and

                place[
                    "distance"
                ] <= radius
            )

        ]


        osm_places.sort(

            key=
                lambda place: (

                    place.get(
                        "distance",
                        float(
                            "inf"
                        )
                    ),

                    place.get(
                        "name"
                    )
                    or "",
                )

        )


        for place in osm_places:

            key = unique_key(
                place
            )


            if (
                key in used_keys
            ):

                continue


            places.append(
                place
            )


            used_keys.add(
                key
            )


            if (
                len(
                    places
                )
                >=
                MAX_RESULTS
            ):

                break


    places = (
        places[
            :MAX_RESULTS
        ]
    )


    add_photo_proxy_urls(
        places
    )


    return jsonify({

        "places":
            places

    })


# ==========================================
# Google 去重 key
# ==========================================

def google_place_key(
    place
):

    place_id = (
        place.get(
            "id"
        )
    )


    if place_id:

        return (
            f"google:"
            f"{place_id}"
        )


    return (

        f"fallback:"

        f"{(place.get('name') or '').strip().lower()}:"

        f"{round(place['latitude'], 5)}:"

        f"{round(place['longitude'], 5)}"

    )


# ==========================================
# 從區域結果拿下一個未重複店家
# ==========================================

def take_next_unique(
    zone_places,
    cursor,
    used_keys
):

    while (
        cursor <
        len(
            zone_places
        )
    ):

        place = (
            zone_places[
                cursor
            ]
        )


        cursor += 1


        key = google_place_key(
            place
        )


        if (
            key in used_keys
        ):

            continue


        return (
            place,
            cursor
        )


    return (
        None,
        cursor
    )


# ==========================================
# 五點搜尋 + 五區均衡
# ==========================================

def google_multi_search(

    origin_lat,

    origin_lng,

    place_type,

    radius,

    open_now_only=False,
):

    points = search_points(

        origin_lat,

        origin_lng,

        radius,
    )


    sample_radius = min(

        radius,

        max(

            100,

            int(
                radius *
                0.8
            )

        )

    )


    zone_results = {

        zone: []

        for zone
        in ZONE_ORDER

    }


    # ======================================
    # 每區只打一次 Google
    #
    # 中心：
    # POPULARITY
    #
    # 北南東西：
    # DISTANCE
    # ======================================

    for (
        zone,
        sample_lat,
        sample_lng
    ) in points:


        rank = (

            "POPULARITY"

            if zone == "center"

            else

            "DISTANCE"

        )


        results = google_search_once(

            origin_lat=
                origin_lat,

            origin_lng=
                origin_lng,

            lat=
                sample_lat,

            lng=
                sample_lng,

            place_type=
                place_type,

            radius=
                sample_radius,

            rank=
                rank,

            include_opening_hours=
                open_now_only,
        )


        for place in results:


            if (
                place[
                    "distance"
                ] > radius
            ):

                continue


            # 營業中模式：
            #
            # 只有 Google 明確 openNow=True
            # 才保留。
            #
            # None 也不保留。

            if (
                open_now_only

                and

                place.get(
                    "open_now"
                )
                is not True
            ):

                continue


            zone_results[
                zone
            ].append(
                place
            )


    selected = []

    used_keys = set()


    cursors = {

        zone: 0

        for zone
        in ZONE_ORDER

    }


    selected_per_zone = {

        zone: 0

        for zone
        in ZONE_ORDER

    }


    # ======================================
    # 第一階段
    #
    # 先跑 10 輪：
    #
    # 中
    # 北
    # 南
    # 東
    # 西
    #
    # 這樣手機每頁 5 家時，
    # 前幾頁會自然混合五區。
    # ======================================

    for _ in range(
        10
    ):


        for zone in ZONE_ORDER:


            if (

                selected_per_zone[
                    zone
                ]

                >=

                ZONE_LIMITS[
                    zone
                ]

            ):

                continue


            place, new_cursor = (
                take_next_unique(

                    zone_results[
                        zone
                    ],

                    cursors[
                        zone
                    ],

                    used_keys,
                )
            )


            cursors[
                zone
            ] = new_cursor


            if (
                place is None
            ):

                continue


            selected.append(
                place
            )


            used_keys.add(

                google_place_key(
                    place
                )

            )


            selected_per_zone[
                zone
            ] += 1


    # ======================================
    # 第二階段
    #
    # 中心目標 20
    #
    # 再補 10 家中心熱門店
    # ======================================

    while (

        selected_per_zone[
            "center"
        ]

        <

        ZONE_LIMITS[
            "center"
        ]

    ):


        place, new_cursor = (
            take_next_unique(

                zone_results[
                    "center"
                ],

                cursors[
                    "center"
                ],

                used_keys,
            )
        )


        cursors[
            "center"
        ] = new_cursor


        if (
            place is None
        ):

            break


        selected.append(
            place
        )


        used_keys.add(

            google_place_key(
                place
            )

        )


        selected_per_zone[
            "center"
        ] += 1


    # ======================================
    # 第三階段
    #
    # 某區可能：
    #
    # 店太少
    # 或與其他區重複
    #
    # 所以收集剩餘 Google 候選
    # 再補滿 60
    # ======================================

    leftovers = []


    for zone in ZONE_ORDER:


        for place in zone_results[
            zone
        ]:


            key = google_place_key(
                place
            )


            if (
                key in used_keys
            ):

                continue


            leftovers.append(
                place
            )


    leftovers.sort(

        key=
            lambda place: (

                place.get(
                    "distance",
                    float(
                        "inf"
                    )
                ),

                place.get(
                    "name"
                )
                or "",
            )

    )


    for place in leftovers:


        key = google_place_key(
            place
        )


        if (
            key in used_keys
        ):

            continue


        selected.append(
            place
        )


        used_keys.add(
            key
        )


        if (
            len(
                selected
            )
            >=
            MAX_RESULTS
        ):

            break


    logging.info(

        (
            "Balanced Google selection: "
            "total=%s "
            "center=%s "
            "north=%s "
            "south=%s "
            "east=%s "
            "west=%s "
            "open_now_only=%s"
        ),

        len(
            selected
        ),

        selected_per_zone[
            "center"
        ],

        selected_per_zone[
            "north"
        ],

        selected_per_zone[
            "south"
        ],

        selected_per_zone[
            "east"
        ],

        selected_per_zone[
            "west"
        ],

        open_now_only,
    )


    return (
        selected[
            :MAX_RESULTS
        ]
    )


# ==========================================
# 單次 Google Nearby Search
# ==========================================

def google_search_once(

    origin_lat,

    origin_lng,

    lat,

    lng,

    place_type,

    radius,

    rank,

    include_opening_hours=False,
):

    # 一般搜尋只拿 Pro 欄位

    field_mask = (

        "places.displayName,"

        "places.formattedAddress,"

        "places.location,"

        "places.id,"

        "places.photos"

    )


    # 只有使用者勾「只看目前營業中」
    #
    # 才加 Enterprise 欄位

    if include_opening_hours:

        field_mask += (
            ",places.currentOpeningHours"
        )


    headers = {

        "Content-Type":
            "application/json",

        "X-Goog-Api-Key":
            GOOGLE_API_KEY,

        "X-Goog-FieldMask":
            field_mask,
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
                        lng,
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

                f"&query="
                f"{quote_plus(name)}"

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


        open_now = None


        if include_opening_hours:

            open_now = (

                place.get(
                    "currentOpeningHours"
                )

                or {}

            ).get(
                "openNow"
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

                    place_lng,
                ),

            "open_now":
                open_now,

            "_photo_ref":
                photo_ref,

            "photo_url":
                "/static/placeholder.jpg",

            "map_url":
                map_url,
        })


    return output


# ==========================================
# Photo signed token
# ==========================================

def photo_token(
    photo_ref
):

    encoded = (

        base64
        .urlsafe_b64encode(

            photo_ref.encode(
                "utf-8"
            )

        )
        .decode(
            "ascii"
        )
        .rstrip(
            "="
        )

    )


    signature = hmac.new(

        GOOGLE_API_KEY.encode(
            "utf-8"
        ),

        encoded.encode(
            "ascii"
        ),

        hashlib.sha256,

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

        GOOGLE_API_KEY.encode(
            "utf-8"
        ),

        encoded.encode(
            "ascii"
        ),

        hashlib.sha256,

    ).hexdigest()


    if not hmac.compare_digest(

        expected,

        signature,

    ):

        return None


    padding = "=" * (

        (
            -len(
                encoded
            )
        )

        % 4

    )


    try:

        raw = (

            base64
            .urlsafe_b64decode(

                encoded +
                padding

            )
            .decode(
                "utf-8"
            )

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


# ==========================================
# 建立安全 Photo URL
# ==========================================

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

            and

            photo_ref

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

                f"?ref="
                f"{quote_plus(encoded)}"

                f"&sig="
                f"{signature}"

            )


        else:

            place[
                "photo_url"
            ] = (
                "/static/placeholder.jpg"
            )


# ==========================================
# Google Photo 安全快速代理
# ==========================================

@app.route(
    "/place_photo"
)
def place_photo():

    if not GOOGLE_API_ENABLED:

        return redirect(

            "/static/placeholder.jpg",

            code=302,
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


    if (
        encoded
        and signature
    ):

        photo_ref = (
            decode_photo_token(

                encoded,

                signature,

            )
        )

    else:

        photo_ref = None


    if not photo_ref:

        return redirect(

            "/static/placeholder.jpg",

            code=302,
        )


    # Photo 月度安全上限

    try:

        allowed = reserve_budget(

            "photo",

            1,

            MONTHLY_PHOTO_LIMIT,

        )


        if not allowed:

            return redirect(

                "/static/placeholder.jpg",

                code=302,
            )


    except BudgetStoreError:

        logging.exception(
            "Photo budget store unavailable"
        )


        return redirect(

            "/static/placeholder.jpg",

            code=302,
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

                code=302,
            )


        parsed = urlparse(
            photo_uri
        )


        hostname = (
            parsed.hostname
            or ""
        ).lower()


        # 防止 open redirect
        #
        # 只允許 Google 圖片 CDN

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

                code=302,
            )


        result = redirect(

            photo_uri,

            code=302,
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

            code=302,
        )


# ==========================================
# 五個採樣點
# ==========================================

def search_points(
    lat,
    lng,
    radius
):

    offset = (
        radius *
        0.45
    )


    dlat = (

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

        0.01,
    )


    dlng = (

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
            "center",
            lat,
            lng
        ),

        (
            "north",
            lat + dlat,
            lng
        ),

        (
            "south",
            lat - dlat,
            lng
        ),

        (
            "east",
            lat,
            lng + dlng
        ),

        (
            "west",
            lat,
            lng - dlng
        ),
    ]


# ==========================================
# OSM
# ==========================================

def osm_search(

    lat,

    lng,

    place_type,

    radius,
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


        place_lat = (
            element.get(
                "lat",
                center.get(
                    "lat"
                )
            )
        )


        place_lng = (
            element.get(
                "lon",
                center.get(
                    "lon"
                )
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

            and

            place_type not in {

                "park",

                "tourist_attraction",

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

                    place_lng,
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


# ==========================================
# OSM 地址
# ==========================================

def osm_address(
    tags
):

    if tags.get(
        "addr:full"
    ):

        return (
            tags[
                "addr:full"
            ]
            .strip()
        )


    parts = [

        tags[
            key
        ]

        for key in (

            "addr:city",

            "addr:district",

        )

        if tags.get(
            key
        )

    ]


    street = (
        tags.get(
            "addr:street"
        )
    )


    number = (
        tags.get(
            "addr:housenumber"
        )
    )


    if street:

        parts.append(

            f"{street} {number}"

            if number

            else street

        )


    if parts:

        return ", ".join(
            parts
        )


    return "無地址資訊"


# ==========================================
# 去重
# ==========================================

def unique_key(
    place
):

    if (

        place.get(
            "source"
        )
        == "Google"

        and

        place.get(
            "id"
        )

    ):

        return (

            "google",

            place[
                "id"
            ],

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

            5,

        ),


        round(

            float(
                place[
                    "longitude"
                ]
            ),

            5,

        ),

    )


# ==========================================
# 距離
# ==========================================

def distance_m(

    lat1,

    lon1,

    lat2,

    lon2,
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

        lat2 -
        lat1

    )


    delta_lon = math.radians(

        lon2 -
        lon1

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
            ),

        )

    )


if __name__ == "__main__":

    app.run(

        host=
            "0.0.0.0",

        port=int(

            os.getenv(

                "PORT",

                "5031",

            )

        ),

    )
