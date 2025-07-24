import os
from flask import Flask, request, jsonify, render_template
import requests
from dotenv import load_dotenv
from flask_cors import CORS #跨資源共用 公有或授權的外部API 中提取資料時需要
import logging #日誌 便於除錯
import math # 新增：用於地理距離計算

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 載入.env 檔案中的環境變數
load_dotenv()

app = Flask(__name__)
CORS(app) # 啟用 CORS，允許前端從不同來源發送請求

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    logging.error("GOOGLE_API_KEY 環境變數未設定。請檢查您的 .env 檔案或系統環境變數。")
    raise ValueError("GOOGLE_API_KEY 環境變數未設定。請檢查您的 .env 檔案或系統環境變數。")

# Google Geocoding API 的基礎 URL (用於反向地理編碼，將用戶的經緯度轉換為可讀的地址)
GOOGLE_GEOCODING_API_BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
# Google Places API (New) Nearby Search 的基礎 URL 用於搜尋特定位置附近的點
GOOGLE_PLACES_NEARBY_SEARCH_NEW_URL = "https://places.googleapis.com/v1/places:searchNearby"
# Overpass API 的基礎 URL 用於搜尋 OSM 資料
OVERPASS_API_BASE_URL = "https://overpass-api.de/api/interpreter"

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
        "language": "zh-TW" # 指定中文
    }
    try:
        response = requests.get(GOOGLE_GEOCODING_API_BASE_URL, params=params)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        data = response.json()

        if data['status'] == 'OK' and data['results']:
            formatted_address = data['results'][0]['formatted_address']
            return jsonify({"address": formatted_address})
        else:
            logging.warning(f"Google Geocoding API 查詢失敗或無結果: {data.get('status')}")
            return jsonify({"error": "無法解析地址", "details": data.get('status')}), 404

    except requests.exceptions.RequestException as e:
        logging.error(f"反向地理編碼請求失敗: {e}")
        return jsonify({"error": "反向地理編碼服務錯誤"}), 500

# 新增的地址轉換座標的 API 端點
@app.route('/geocode_address', methods=['POST'])
def geocode_address():
    data = request.get_json()
    address = data.get('address')

    if not address:
        return jsonify({"error": "Missing address"}), 400

    params = {
        "address": address,
        "key": GOOGLE_API_KEY,
        "language": "zh-TW" # 指定中文
    }
    try:
        response = requests.get(GOOGLE_GEOCODING_API_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        if data['status'] == 'OK' and data['results']:
            location = data['results'][0]['geometry']['location']
            formatted_address = data['results'][0]['formatted_address']
            return jsonify({
                "lat": location['lat'],
                "lng": location['lng'],
                "formatted_address": formatted_address
            })
        else:
            logging.warning(f"Google Geocoding API 地址轉換失敗或無結果: {data.get('status')}")
            return jsonify({"error": "無法找到該地址的座標", "details": data.get('status')}), 404

    except requests.exceptions.RequestException as e:
        logging.error(f"地址轉換請求失敗: {e}")
        return jsonify({"error": "地址轉換服務錯誤"}), 500


@app.route('/nearby_search', methods=['POST'])
def nearby_search():
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    place_type = data.get('type')
    radius = data.get('radius', 5000) # 預設半徑 5000 公尺

    if not all([lat, lng, place_type]):
        return jsonify({"error": "Missing lat, lng, or type"}), 400

    # --- 1. Google Places API (New) Nearby Search ---
    google_places = []
    google_headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.id,places.addressComponents,places.primaryType,places.types,places.regularOpeningHours.weekdayDescriptions,places.websiteUri,places.internationalPhoneNumber,places.priceLevel,places.editorialSummary,places.takeout,places.dineIn,places.delivery,places.servesBreakfast,places.servesLunch,places.servesDinner"
    }
    google_payload = {
        "includedTypes": [place_type],
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius
            }
        },
        "languageCode": "zh-TW" # 指定中文
    }

    try:
        google_response = requests.post(GOOGLE_PLACES_NEARBY_SEARCH_NEW_URL, headers=google_headers, json=google_payload)
        google_response.raise_for_status()
        google_data = google_response.json()
        
        if google_data.get('places'):
            for p in google_data['places']:
                place_info = {
                    "id": p.get('id'),
                    "name": p['displayName']['text'],
                    "formatted_address": p.get('formattedAddress'),
                    "vicinity": p.get('formattedAddress'), # For consistency with OSM
                    "latitude": p['location']['latitude'],
                    "longitude": p['location']['longitude'],
                    "rating": p.get('rating'),
                    "user_ratings_total": p.get('userRatingCount'),
                    "source": "Google",
                    "distance": calculate_distance(lat, lng, p['location']['latitude'], p['location']['longitude'])
                }
                google_places.append(place_info)
    except requests.exceptions.RequestException as e:
        logging.error(f"Google Places API 搜尋失敗: {e}")
    except KeyError as e:
        logging.error(f"Google Places API 返回資料結構錯誤: {e}")


    # --- 2. OpenStreetMap (OSM) Overpass API ---
    osm_places = []
    # 將 Google Places API 的類型映射到 OSM 的 key/value 標籤
    # 這裡只是一個簡單的映射，實際應用可能需要更複雜的邏輯
    osm_tags_map = {
        "restaurant": {"amenity": "restaurant"},
        "cafe": {"amenity": "cafe"},
        "bar": {"amenity": "bar"},
        "bakery": {"shop": "bakery"},
        "meal_delivery": {"amenity": "food_court"}, # OSM 沒有直接對應
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
    if osm_tag:
        overpass_query = f"""
            [out:json];
            (
              node["{list(osm_tag.keys())[0]}"="{list(osm_tag.values())[0]}"](around:{radius},{lat},{lng});
              way["{list(osm_tag.keys())[0]}"="{list(osm_tag.values())[0]}"](around:{radius},{lat},{lng});
              relation["{list(osm_tag.keys())[0]}"="{list(osm_tag.values())[0]}"](around:{radius},{lat},{lng});
            );
            out center;
        """
        try:
            osm_response = requests.post(OVERPASS_API_BASE_URL, data=overpass_query)
            osm_response.raise_for_status()
            osm_data = osm_response.json()

            for element in osm_data.get('elements', []):
                if 'tags' in element and 'lat' in element and 'lon' in element:
                    name = element['tags'].get('name', 'N/A')
                    address = element['tags'].get('addr:full') or \
                              element['tags'].get('addr:street', '') + (", " + element['tags']['addr:housenumber'] if element['tags'].get('addr:housenumber') else '') or \
                              element['tags'].get('addr:housename', '無地址資訊')
                    
                    # 檢查並跳過沒有名稱的項目，除非是公園這類可能沒有具體名稱但有類型標籤的項目
                    if name == 'N/A' and place_type not in ["park", "tourist_attraction"]:
                        continue

                    osm_places.append({
                        "id": f"osm-{element['id']}",
                        "osm_id": element['id'], # 儲存 OSM ID
                        "name": name,
                        "formatted_address": address.strip(),
                        "vicinity": address.strip(),
                        "latitude": element['lat'],
                        "longitude": element['lon'],
                        "rating": None, # OSM 通常不包含評分
                        "user_ratings_total": None,
                        "source": "OSM",
                        "distance": calculate_distance(lat, lng, element['lat'], element['lon'])
                    })

        except requests.exceptions.RequestException as e:
            logging.error(f"OSM Overpass API 搜尋失敗: {e}")

    # --- 3. 合併結果並去除重複 ---
    # 使用一個字典來儲存唯一的地點，鍵可以是 (name, address, lat, lng) 的組合
    # 優先保留 Google 的結果
    final_unique_places = {}

    for p in google_places:
        name = p['name']
        address = p.get('formatted_address', p.get('vicinity', ''))
        lat = p['latitude']
        lng = p['longitude']
        unique_key = (name.lower().strip(), address.lower().strip(), round(lat, 5), round(lng, 5)) # 使用 round 處理浮點數精度問題
        final_unique_places[unique_key] = p

    for p in osm_places:
        name = p['name']
        address = p.get('formatted_address', p.get('vicinity', ''))
        lat = p['latitude']
        lng = p['longitude']
        unique_key = (name.lower().strip(), address.lower().strip(), round(lat, 5), round(lng, 5))
        
        if unique_key not in final_unique_places:
            final_unique_places[unique_key] = p
        else:
            logging.debug(f"跳過重複的 OSM 地點: {name} ({address})。")

    # --- 4. 合併並回傳結果 (Google 結果優先) ---
    final_places_list = list(final_unique_places.values())

    # 排序：優先顯示有評分（來自 Google）的地點，然後按評分高低排序，最後按地點名稱字母順序排序
    # 新增：將距離作為第三個排序條件，距離越近越優先
    final_places_list.sort(key=lambda x: (
        x['rating'] is not None,  # 有評分的優先
        x['rating'] or 0,         # 評分高的優先
        -(x.get('distance', float('inf')) or float('inf')), # 距離近的優先 (負數實現升序)
        x['name']                 # 最後按名稱排序
    ), reverse=True) # reverse=True 是因為前面兩個是越高越好，但距離是越低越好，所以需要調整 key 的符號

    # 截斷結果列表至 max_results 數量
    # if max_results and len(final_places_list) > max_results:
    #     logging.info(f"將結果從 {len(final_places_list)} 截斷至 {max_results}。")
    #     final_places_list = final_places_list[:max_results]

    return jsonify({"places": final_places_list})

# 輔助函數：計算兩個經緯度之間的距離（公尺）
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000 # 地球半徑，單位公尺
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance

if __name__ == '__main__':
    app.run( port=5031)