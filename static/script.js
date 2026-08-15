const BACKEND_BASE_URL = '';
let currentCoords = null;
let currentAddress = null;
let allPlacesData = {};
window.placeUrlMap = {};

document.addEventListener('DOMContentLoaded', () => {
    const updateWheelBtn = document.getElementById('updateWheelBtn');
    const getLocationBtn = document.getElementById('getLocationBtn');
    const searchTypeSelect = document.getElementById('searchType');
    const searchNearbyBtn = document.getElementById('searchNearbyBtn');
    const messageDiv = document.getElementById('message');
    const placesListDiv = document.getElementById('placesList');
    const currentLocationDisplay = document.getElementById('currentLocationDisplay');
    const placeTypeSelection = document.getElementById('placeTypeSelection');
    const selectAllBtn = document.getElementById('selectAllBtn');
    const deselectAllBtn = document.getElementById('deselectAllBtn');
    const placesActionsDiv = document.getElementById('placesActions');
    const manualLocationInputDiv = document.getElementById('manualLocationInput');
    const addressInput = document.getElementById('addressInput');
    const geocodeAddressBtn = document.getElementById('geocodeAddressBtn');
    const searchRadiusSlider = document.getElementById('searchRadius');
    const radiusValueSpan = document.getElementById('radiusValue');

    function displayMessage(msg, type = 'info') {
        if (!messageDiv) return;
        messageDiv.textContent = msg;
        messageDiv.className = `message ${type}`;
        messageDiv.style.display = 'block';
    }

    function updateWheelFromCheckedPlaces() {
        if (!placesListDiv || typeof window.setupWheelWithPlaces !== 'function') return;

        const checkedIds = [
            ...placesListDiv.querySelectorAll('input[type="checkbox"]:checked')
        ].map(cb => cb.id);

        const selectedNames = checkedIds
            .map(id => allPlacesData[id]?.name)
            .filter(Boolean);

        window.setupWheelWithPlaces(selectedNames);
    }

    if (updateWheelBtn) {
        updateWheelBtn.addEventListener('click', () => {
            const checkedCount = placesListDiv
                ? placesListDiv.querySelectorAll('input[type="checkbox"]:checked').length
                : 0;

            if (checkedCount === 0) {
                if (typeof window.setupWheelWithPlaces === 'function') {
                    window.setupWheelWithPlaces([]);
                }

                alert('請至少勾選一個地點才能轉盤！');
                return;
            }

            updateWheelFromCheckedPlaces();
        });
    }

    if (placesListDiv) {
        placesListDiv.addEventListener('change', event => {
            if (event.target.matches('input[type="checkbox"]')) {
                updateWheelFromCheckedPlaces();
            }
        });
    }

    if (getLocationBtn) {
        getLocationBtn.addEventListener('click', getGeoLocation);
    }

    if (searchNearbyBtn) {
        searchNearbyBtn.addEventListener('click', searchNearbyPlaces);
    }

    if (selectAllBtn && placesListDiv) {
        selectAllBtn.addEventListener('click', () => {
            placesListDiv
                .querySelectorAll('input[type="checkbox"]')
                .forEach(checkbox => {
                    checkbox.checked = true;
                });

            updateWheelFromCheckedPlaces();
        });
    }

    if (deselectAllBtn && placesListDiv) {
        deselectAllBtn.addEventListener('click', () => {
            placesListDiv
                .querySelectorAll('input[type="checkbox"]')
                .forEach(checkbox => {
                    checkbox.checked = false;
                });

            updateWheelFromCheckedPlaces();
        });
    }

    if (geocodeAddressBtn) {
        geocodeAddressBtn.addEventListener('click', geocodeAddress);
    }

    if (addressInput) {
        addressInput.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                geocodeAddress();
            }
        });
    }

    if (searchRadiusSlider && radiusValueSpan) {
        radiusValueSpan.textContent = searchRadiusSlider.value;

        searchRadiusSlider.addEventListener('input', () => {
            radiusValueSpan.textContent = searchRadiusSlider.value;
        });
    }

    getGeoLocation();

    function getGeoLocation() {
        displayMessage('正在獲取您的位置...', 'info');

        if (!navigator.geolocation) {
            displayMessage(
                '您的瀏覽器不支援地理位置功能，請手動輸入地址。',
                'error'
            );

            if (currentLocationDisplay) {
                currentLocationDisplay.textContent = '瀏覽器不支援地理位置。';
            }

            if (manualLocationInputDiv) {
                manualLocationInputDiv.classList.remove('hidden-section');
            }

            if (placeTypeSelection) {
                placeTypeSelection.classList.add('hidden-section');
            }

            if (placesListDiv) {
                placesListDiv.innerHTML = '';
            }

            if (placesActionsDiv) {
                placesActionsDiv.classList.add('hidden-section');
            }

            if (typeof window.setupWheelWithPlaces === 'function') {
                window.setupWheelWithPlaces([]);
            }

            return;
        }

        navigator.geolocation.getCurrentPosition(
            position => {
                currentCoords = {
                    latitude: position.coords.latitude,
                    longitude: position.coords.longitude
                };

                reverseGeocode(
                    currentCoords.latitude,
                    currentCoords.longitude
                );

                if (manualLocationInputDiv) {
                    manualLocationInputDiv.classList.add('hidden-section');
                }
            },
            error => {
                console.error('獲取地理位置失敗:', error);

                displayMessage(
                    '無法獲取您的位置，請手動輸入地址。',
                    'error'
                );

                if (currentLocationDisplay) {
                    currentLocationDisplay.textContent = '無法獲取位置。';
                }

                if (manualLocationInputDiv) {
                    manualLocationInputDiv.classList.remove('hidden-section');
                }

                if (placeTypeSelection) {
                    placeTypeSelection.classList.add('hidden-section');
                }

                if (placesListDiv) {
                    placesListDiv.innerHTML = '';
                }

                if (placesActionsDiv) {
                    placesActionsDiv.classList.add('hidden-section');
                }

                if (typeof window.setupWheelWithPlaces === 'function') {
                    window.setupWheelWithPlaces([]);
                }
            },
            {
                enableHighAccuracy: true,
                timeout: 10000,
                maximumAge: 0
            }
        );
    }

    async function reverseGeocode(lat, lng) {
        try {
            const response = await fetch(
                `${BACKEND_BASE_URL}/reverse_geocode?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}`
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error || '反向地理編碼失敗'
                );
            }

            if (data.address) {
                currentAddress = data.address;

                if (currentLocationDisplay) {
                    currentLocationDisplay.textContent =
                        `當前位置: ${currentAddress}`;
                }

                displayMessage('位置已更新。', 'success');
            } else {
                currentAddress = '未知地址';

                if (currentLocationDisplay) {
                    currentLocationDisplay.textContent =
                        '當前位置: 未知';
                }

                displayMessage(
                    '無法解析當前位置地址。',
                    'warning'
                );
            }

            if (placeTypeSelection) {
                placeTypeSelection.classList.remove('hidden-section');
            }

            updateWheelFromCheckedPlaces();

        } catch (error) {
            console.error('反向地理編碼失敗:', error);

            displayMessage(
                '反向地理編碼失敗。',
                'error'
            );

            currentAddress = '獲取地址失敗';

            if (currentLocationDisplay) {
                currentLocationDisplay.textContent =
                    '當前位置: 獲取地址失敗';
            }

            if (placeTypeSelection) {
                placeTypeSelection.classList.remove('hidden-section');
            }

            if (typeof window.setupWheelWithPlaces === 'function') {
                window.setupWheelWithPlaces([]);
            }
        }
    }

    async function geocodeAddress() {
        if (!addressInput) return;

        const address = addressInput.value.trim();

        if (!address) {
            displayMessage(
                '請輸入有效的地址。',
                'error'
            );

            return;
        }

        displayMessage(
            '正在轉換地址...',
            'info'
        );

        try {
            const response = await fetch(
                `${BACKEND_BASE_URL}/geocode_address`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        address
                    })
                }
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error || '地址轉換失敗'
                );
            }

            if (
                data.lat !== undefined &&
                data.lng !== undefined
            ) {
                currentCoords = {
                    latitude: data.lat,
                    longitude: data.lng
                };

                currentAddress =
                    data.formatted_address || address;

                if (currentLocationDisplay) {
                    currentLocationDisplay.textContent =
                        `當前位置: ${currentAddress}`;
                }

                displayMessage(
                    '地址轉換成功！',
                    'success'
                );

                if (placeTypeSelection) {
                    placeTypeSelection.classList.remove('hidden-section');
                }

                if (manualLocationInputDiv) {
                    manualLocationInputDiv.classList.add('hidden-section');
                }

            } else {
                throw new Error(
                    '回傳資料沒有座標'
                );
            }

            updateWheelFromCheckedPlaces();

        } catch (error) {
            console.error('地址轉換失敗:', error);

            displayMessage(
                `地址轉換失敗：${error.message}`,
                'error'
            );

            if (placeTypeSelection) {
                placeTypeSelection.classList.add('hidden-section');
            }

            currentCoords = null;
            currentAddress = null;

            if (typeof window.setupWheelWithPlaces === 'function') {
                window.setupWheelWithPlaces([]);
            }
        }
    }

    async function searchNearbyPlaces() {
        if (!currentCoords) {
            displayMessage(
                '請先獲取或輸入您的位置。',
                'warning'
            );

            return;
        }

        if (
            !searchTypeSelect ||
            !searchRadiusSlider ||
            !placesListDiv
        ) {
            displayMessage(
                '頁面元件載入失敗，請重新整理後再試。',
                'error'
            );

            return;
        }

        const type = searchTypeSelect.value;
        const radius =
            parseInt(searchRadiusSlider.value, 10);

        displayMessage(
            `正在搜尋附近的 ${searchTypeSelect.options[searchTypeSelect.selectedIndex].text}...`,
            'info'
        );

        placesListDiv.innerHTML = '';

        allPlacesData = {};
        window.placeUrlMap = {};

        if (placesActionsDiv) {
            placesActionsDiv.classList.add('hidden-section');
        }

        try {
            const response = await fetch(
                `${BACKEND_BASE_URL}/nearby_search`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        lat: currentCoords.latitude,
                        lng: currentCoords.longitude,
                        type,
                        radius
                    })
                }
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error || '搜尋失敗'
                );
            }

            if (
                !data.places ||
                data.places.length === 0
            ) {
                displayMessage(
                    '沒有找到符合條件的地點。',
                    'warning'
                );

                if (
                    typeof window.setupWheelWithPlaces ===
                    'function'
                ) {
                    window.setupWheelWithPlaces([]);
                }

                return;
            }

            data.places.forEach(place => {
                const placeId =
                    place.id ||
                    `osm-${place.osm_id}`;

                const placeName =
                    place.name ||
                    '未命名地點';

                const photoUrl =
                    place.photo_url ||
                    '/static/placeholder.jpg';

                const mapUrl =
                    place.map_url ||
                    place.url ||
                    '';

                allPlacesData[placeId] = place;

                window.placeUrlMap[placeName] =
                    mapUrl;

                const placeItem =
                    document.createElement('div');

                placeItem.className =
                    'place-item';

                const checkbox =
                    document.createElement('input');

                checkbox.type =
                    'checkbox';

                checkbox.id =
                    placeId;

                checkbox.checked =
                    true;

                const label =
                    document.createElement('label');

                label.htmlFor =
                    placeId;

                const title =
                    document.createElement('h3');

                title.textContent =
                    placeName;

                const image =
                    document.createElement('img');

                image.src =
                    photoUrl;

                image.className =
                    'place-img';

                image.alt =
                    `${placeName} 圖片`;

                image.loading =
                    'lazy';

                image.onerror = () => {
                    if (
                        !image.src.endsWith(
                            '/static/placeholder.jpg'
                        )
                    ) {
                        image.src =
                            '/static/placeholder.jpg';
                    }
                };

                const address =
                    document.createElement('p');

                address.textContent =
                    place.formatted_address ||
                    place.vicinity ||
                    '無地址資訊';

                label.appendChild(title);
                label.appendChild(image);
                label.appendChild(address);

                if (place.map_url) {
                    label.appendChild(
                        createLinkParagraph(
                            place.map_url,
                            '📍 Google 地圖'
                        )
                    );
                }

                if (place.url) {
                    label.appendChild(
                        createLinkParagraph(
                            place.url,
                            '📍 OSM 地圖'
                        )
                    );
                }

                const googleSearchUrl =
                    `https://www.google.com/search?q=${encodeURIComponent(placeName)}`;

                label.appendChild(
                    createLinkParagraph(
                        googleSearchUrl,
                        '🔍 從 Google 搜尋這家店'
                    )
                );

                placeItem.appendChild(
                    checkbox
                );

                placeItem.appendChild(
                    label
                );

                placesListDiv.appendChild(
                    placeItem
                );
            });

            const selectedNames =
                data.places
                    .map(place => place.name)
                    .filter(Boolean);

            if (
                typeof window.setupWheelWithPlaces ===
                'function'
            ) {
                window.setupWheelWithPlaces(
                    selectedNames
                );
            }

            displayMessage(
                `找到 ${data.places.length} 個結果。`,
                'success'
            );

            if (placesActionsDiv) {
                placesActionsDiv.classList.remove(
                    'hidden-section'
                );
            }

        } catch (error) {
            console.error(
                '搜尋失敗:',
                error
            );

            displayMessage(
                `搜尋地點失敗：${error.message}`,
                'error'
            );

            if (placesActionsDiv) {
                placesActionsDiv.classList.add(
                    'hidden-section'
                );
            }

            if (
                typeof window.setupWheelWithPlaces ===
                'function'
            ) {
                window.setupWheelWithPlaces([]);
            }
        }
    }

    function createLinkParagraph(
        url,
        text
    ) {
        const paragraph =
            document.createElement('p');

        const link =
            document.createElement('a');

        link.href =
            url;

        link.target =
            '_blank';

        link.rel =
            'noopener noreferrer';

        link.textContent =
            text;

        paragraph.appendChild(
            link
        );

        return paragraph;
    }
});
