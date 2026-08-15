import logging
import math
import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
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

GOOGLE_GEOCODING_API_BASE_URL = (
    "https://maps.googleapis.com/maps/api/geocode/json"
)
GOOGLE_PLACES_NEARBY_SEARCH_NEW_URL = (
    "https://places.googleapis.com/v1/places:searchNearby"
)
OVERPASS_API_BASE_URL = "https://overpass-api.de/api/interpreter"

REQUEST_TIMEOUT = 15
MAX_RESULTS = 20


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/reverse_geocode')
def reverse_geocode():
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)

    if lat is None or lng is None:
        return jsonify({"error": "Missing latitude or longitude"}), 400

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
            formatted_address = data['results'][0]['formatted_address']
            return jsonify({"address": formatted_address})

        logging.warning(
            "Google Geocoding API 查詢失敗或無結果: %s",
            data.get('status')
        )
        return jsonify({
            "error": "無法解析地址",
            "details": data.get('status')
        }), 404

    except requests.exceptions.RequestException as e:
        logging.error("反向地理編碼請求失敗: %s", e)
        return jsonify({"error": "反向地理編碼服務錯誤"}), 500


@app.route('/geocode_address', methods=['POST'])
def geocode_address():
    data = request.get_json(silent=True) or {}
    address = (data.get('address') or '').strip()

    if not address:
        return jsonify({"error": "Missing address"}), 400

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

        if result.get('status') == 'OK' and result.get('results'):
            location = result['results'][0]['geometry']['location']
            formatted_address = result['results'][0]['formatted_address']

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
        logging.error("地址轉換請求失敗: %s", e)
        return jsonify({"error": "地址轉換服務錯誤"}), 500


@app.route('/nearby_search', methods=['POST'])
def nearby_search():
    data = request.get_json(silent=True) or {}

    lat = data.get('lat')
    lng = data.get('lng')
    place_type = data.get('type')
    radius = data.get('radius', 5000)

    if lat is None or lng is None or not place_type:
        return jsonify({"error": "Missing lat, lng, or type"}), 400

    try:
        lat = float(lat)
        lng = float(lng)
        radius = int(radius)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid lat, lng, or radius"}), 400

    radius = max(100, min(radius, 10000))

    google_places = search_google_places(
        lat=lat,
        lng=lng,
        place_type=place_type,
        radius=radius
    )

    osm_places = search_osm_places(
        lat=lat,
        lng=lng,
        place_type=place_type,
        radius=radius
    )

    final_unique_places = {}

    for place in google_places:
        final_unique_places[make_unique_key(place)] = place

    for place in osm_places:
        unique_key = make_unique_key(place)
        if unique_key not in final_unique_places:
            final_unique_places[unique_key] = place

    final_places_list = list(final_unique_places.values())

    final_places_list.sort(
        key=lambda place: (
            place.get('rating') is not None,
            place.get('rating') or 0,
            -(place.get('distance') or float('inf')),
            place.get('name') or ''
        ),
        reverse=True
    )

    final_places_list = final_places_list[:MAX_RESULTS]

    return jsonify({"places": final_places_list})


def search_google_places(lat, lng, place_type, radius):
    google_places = []

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": (
            "places.displayName,"
            "places.formattedAddress,"
            "places.location,"
            "places.rating,"
            "places.userRatingCount,"
            "places.id,"
            "places.photos"
        )
    }

    payload = {
        "includedTypes": [place_type],
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": lat,
                    "longitude": lng
                },
                "radius": radius
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

        for place in google_data.get('places', []):
            location = place.get('location') or {}
            place_lat = location.get('latitude')
            place_lng = location.get('longitude')
            display_name = (place.get('displayName') or {}).get('text')

            if (
                not display_name or
                place_lat is None or
                place_lng is None
            ):
                continue

            photo_url = "/static/placeholder.jpg"
            photos = place.get("photos") or []

            if photos:
                photo_ref = photos[0].get("name")
                if photo_ref:
                    photo_url = (
                        f"https://places.googleapis.com/v1/"
                        f"{photo_ref}/media"
                        f"?key={GOOGLE_API_KEY}&maxWidthPx=400"
                    )

            place_id = place.get('id')

            google_places.append({
                "id": place_id,
                "name": display_name,
                "formatted_address": place.get('formattedAddress'),
                "vicinity": place.get('formattedAddress'),
                "latitude": place_lat,
                "longitude": place_lng,
                "rating": place.get('rating'),
                "user_ratings_total": place.get('userRatingCount'),
                "source": "Google",
                "distance": calculate_distance(
                    lat,
                    lng,
                    place_lat,
                    place_lng
                ),
                "photo_url": photo_url,
                "map_url": (
                    f"https://www.google.com/maps/place/"
                    f"?q=place_id:{place_id}"
                ) if place_id else (
                    f"https://www.google.com/maps/search/"
                    f"?api=1&query={place_lat},{place_lng}"
                ),
                "food_search_url": (
                    f"https://www.google.com/search?q={display_name}"
                )
            })

    except requests.exceptions.RequestException as e:
        logging.error("Google Places API 搜尋失敗: %s", e)
    except (KeyError, TypeError, ValueError) as e:
        logging.error("Google Places API 返回資料結構錯誤: %s", e)

    return google_places


def search_osm_places(lat, lng, place_type, radius):
    osm_places = []

    osm_tags_map = {
        "restaurant": {"amenity": "restaurant"},
        "cafe": {"amenity": "cafe"},
        "bar": {"amenity": "bar"},
        "bakery": {"shop": "bakery"},
        "meal_delivery": {"amenity": "food_court"},
        "meal_takeaway": {"amenity": "fast_food"},
        "amusement_park": {"leisure": "amusement_park"},
        "park": {"leisure": "park"},
        "museum": {"tourism": "museum"},
        "movie_theater": {"amenity": "cinema"},
        "bowling_alley": {"leisure": "bowling_alley"},
        "shopping_mall": {"shop": "mall"},
        "spa": {"amenity": "spa"},
        "beauty_salon": {"shop": "beauty"},
        "gym": {"leisure": "fitness_centre"},
        "zoo": {"tourism": "zoo"},
        "tourist_attraction": {"tourism": "attraction"},
        "night_club": {"amenity": "nightclub"},
        "aquarium": {"tourism": "aquarium"},
        "art_gallery": {"tourism": "art_gallery"},
        "casino": {"amenity": "casino"}
    }

    osm_tag = osm_tags_map.get(place_type)
    if not osm_tag:
        return osm_places

    osm_key = next(iter(osm_tag))
    osm_value = osm_tag[osm_key]

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

        for element in osm_data.get('elements', []):
            tags = element.get('tags') or {}
            center = element.get('center') or {}

            element_lat = element.get('lat')
            element_lng = element.get('lon')

            if element_lat is None:
                element_lat = center.get('lat')
            if element_lng is None:
                element_lng = center.get('lon')

            if element_lat is None or element_lng is None:
                continue

            name = tags.get('name', 'N/A')

            if (
                name == 'N/A' and
                place_type not in ["park", "tourist_attraction"]
            ):
                continue

            address = build_osm_address(tags)

            osm_places.append({
                "id": f"osm-{element['id']}",
                "osm_id": element['id'],
                "name": name,
                "formatted_address": address,
                "vicinity": address,
                "latitude": element_lat,
                "longitude": element_lng,
                "rating": None,
                "user_ratings_total": None,
                "source": "OSM",
                "distance": calculate_distance(
                    lat,
                    lng,
                    element_lat,
                    element_lng
                ),
                "photo_url": "/static/placeholder.jpg",
                "url": (
                    "https://www.openstreetmap.org/"
                    f"?mlat={element_lat}"
                    f"&mlon={element_lng}"
                    "&zoom=18"
                ),
                "food_search_url": (
                    f"https://www.google.com/search?q={name}"
                )
            })

    except requests.exceptions.RequestException as e:
        logging.error("OSM Overpass API 搜尋失敗: %s", e)
    except (KeyError, TypeError, ValueError) as e:
        logging.error("OSM 資料處理失敗: %s", e)

    return osm_places


def build_osm_address(tags):
    if tags.get('addr:full'):
        return tags['addr:full'].strip()

    parts = []

    if tags.get('addr:city'):
        parts.append(tags['addr:city'])

    if tags.get('addr:district'):
        parts.append(tags['addr:district'])

    street = tags.get('addr:street')
    house_number = tags.get('addr:housenumber')

    if street:
        if house_number:
            parts.append(f"{street} {house_number}")
        else:
            parts.append(street)

    if not parts and tags.get('addr:housename'):
        parts.append(tags['addr:housename'])

    return ", ".join(parts) if parts else "無地址資訊"


def make_unique_key(place):
    name = (place.get('name') or '').lower().strip()
    address = (
        place.get('formatted_address') or
        place.get('vicinity') or
        ''
    ).lower().strip()

    lat = float(place.get('latitude'))
    lng = float(place.get('longitude'))

    return (
        name,
        address,
        round(lat, 5),
        round(lng, 5)
    )


def calculate_distance(lat1, lon1, lat2, lon2):
    radius = 6371000

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2 +
        math.cos(phi1) *
        math.cos(phi2) *
        math.sin(delta_lambda / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return radius * c


if __name__ == '__main__':
    app.run(port=5031)
