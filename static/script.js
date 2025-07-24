// frontend/static/script.js

const BACKEND_BASE_URL = '';
let currentCoords = null;
let currentAddress = null;
let allPlacesData = {}; // Stores all fetched places

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

    // New elements for manual location input
    const manualLocationInputDiv = document.getElementById('manualLocationInput');
    const addressInput = document.getElementById('addressInput');
    const geocodeAddressBtn = document.getElementById('geocodeAddressBtn');

    const searchRadiusSlider = document.getElementById('searchRadius');
    const radiusValueSpan = document.getElementById('radiusValue');

    if (updateWheelBtn) {
        updateWheelBtn.addEventListener('click', () => {
            const checkedIds = [...document.querySelectorAll('#placesList input[type="checkbox"]:checked')].map(cb => cb.id);
            const selectedNames = checkedIds.map(id => allPlacesData[id]?.name).filter(Boolean);
            if (typeof setupWheelWithPlaces === 'function') { // 不再檢查 selectedNames.length > 0
                setupWheelWithPlaces(selectedNames);
            }
            if (selectedNames.length === 0) {
                alert('請至少勾選一個地點才能轉盤！'); // 保持這個提示
            }
        });
    }

    // ✅ 勾選 checkbox 變動即時更新轉盤
    if (placesListDiv) {
        placesListDiv.addEventListener('change', () => {
            const checkedIds = [...placesListDiv.querySelectorAll('input[type="checkbox"]:checked')].map(cb => cb.id);
            const selectedNames = checkedIds.map(id => allPlacesData[id]?.name).filter(Boolean);
            if (typeof setupWheelWithPlaces === 'function') {
                setupWheelWithPlaces(selectedNames); // 不再檢查 selectedNames.length > 0
                console.log("🎯 轉盤已即時更新！", selectedNames);
            }
        });
    }

    if (getLocationBtn) {
        getLocationBtn.addEventListener('click', getGeoLocation);
    }

    if (searchNearbyBtn) {
        searchNearbyBtn.addEventListener('click', searchNearbyPlaces);
    }

    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
            const checkboxes = placesListDiv.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(checkbox => {
                checkbox.checked = true;
            });
            // 手動觸發 change 事件以更新轉盤
            if (placesListDiv) {
                placesListDiv.dispatchEvent(new Event('change'));
            }
        });
    }

    if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
            const checkboxes = placesListDiv.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(checkbox => {
                checkbox.checked = false;
            });
            // 手動觸發 change 事件以更新轉盤
            if (placesListDiv) {
                placesListDiv.dispatchEvent(new Event('change'));
            }
        });
    }

    // Event listener for manual address input button
    if (geocodeAddressBtn) {
        geocodeAddressBtn.addEventListener('click', geocodeAddress);
    }

    // Update radius value display
    if (searchRadiusSlider && radiusValueSpan) {
        radiusValueSpan.textContent = searchRadiusSlider.value;
        searchRadiusSlider.addEventListener('input', () => {
            radiusValueSpan.textContent = searchRadiusSlider.value;
        });
    }

    // Initial geolocation attempt
    getGeoLocation();

    function displayMessage(msg, type = 'info') {
        if (messageDiv) {
            messageDiv.textContent = msg;
            messageDiv.className = `message ${type}`;
            messageDiv.style.display = 'block'; // Ensure it's visible
        }
    }

    function hideMessage() {
        if (messageDiv) {
            messageDiv.style.display = 'none';
        }
    }

    function getGeoLocation() {
        displayMessage('正在獲取您的位置...', 'info');
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                position => {
                    currentCoords = {
                        latitude: position.coords.latitude,
                        longitude: position.coords.longitude
                    };
                    reverseGeocode(currentCoords.latitude, currentCoords.longitude);
                    if (manualLocationInputDiv) manualLocationInputDiv.classList.add('hidden-section');
                },
                error => {
                    console.error('獲取地理位置失敗:', error);
                    displayMessage('無法獲取您的位置，請手動輸入地址。', 'error');
                    if (currentLocationDisplay) currentLocationDisplay.textContent = '無法獲取位置。';
                    if (manualLocationInputDiv) manualLocationInputDiv.classList.remove('hidden-section'); // Show manual input
                    if (placeTypeSelection) placeTypeSelection.classList.add('hidden-section');
                    placesListDiv.innerHTML = '';
                    if (placesActionsDiv) placesActionsDiv.classList.add('hidden-section');
                    if (typeof setupWheelWithPlaces === 'function') {
                        setupWheelWithPlaces([]); // 清空轉盤
                    }
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
            );
        } else {
            displayMessage('您的瀏覽器不支援地理位置功能，請手動輸入地址。', 'error');
            if (currentLocationDisplay) currentLocationDisplay.textContent = '瀏覽器不支援地理位置。';
            if (manualLocationInputDiv) manualLocationInputDiv.classList.remove('hidden-section'); // Show manual input
            if (placeTypeSelection) placeTypeSelection.classList.add('hidden-section');
            placesListDiv.innerHTML = '';
            if (placesActionsDiv) placesActionsDiv.classList.add('hidden-section');
            if (typeof setupWheelWithPlaces === 'function') {
                setupWheelWithPlaces([]); // 清空轉盤
            }
        }
    }

    async function reverseGeocode(lat, lng) {
        try {
            const response = await fetch(`${BACKEND_BASE_URL}/reverse_geocode?lat=${lat}&lng=${lng}`);
            const data = await response.json();
            if (data.address) {
                currentAddress = data.address;
                if (currentLocationDisplay) currentLocationDisplay.textContent = `當前位置: ${currentAddress}`;
                displayMessage('位置已更新。', 'success');
                if (placeTypeSelection) placeTypeSelection.classList.remove('hidden-section');
            } else {
                currentAddress = '未知地址';
                if (currentLocationDisplay) currentLocationDisplay.textContent = '當前位置: 未知';
                displayMessage('無法解析當前位置地址。', 'warning');
                if (placeTypeSelection) placeTypeSelection.classList.remove('hidden-section');
            }
            // 每次位置更新後，確保轉盤是空的或根據現有勾選項目更新
            if (typeof setupWheelWithPlaces === 'function') {
                const checkedIds = [...placesListDiv.querySelectorAll('input[type="checkbox"]:checked')].map(cb => cb.id);
                const selectedNames = checkedIds.map(id => allPlacesData[id]?.name).filter(Boolean);
                setupWheelWithPlaces(selectedNames);
            }
        } catch (error) {
            console.error('反向地理編碼失敗:', error);
            displayMessage('反向地理編碼失敗。', 'error');
            currentAddress = '獲取地址失敗';
            if (currentLocationDisplay) currentLocationDisplay.textContent = '當前位置: 獲取地址失敗';
            if (placeTypeSelection) placeTypeSelection.classList.remove('hidden-section');
            if (typeof setupWheelWithPlaces === 'function') {
                setupWheelWithPlaces([]); // 清空轉盤
            }
        }
    }

    async function geocodeAddress() {
        const address = addressInput.value.trim();
        if (!address) {
            displayMessage('請輸入有效的地址。', 'error');
            return;
        }

        displayMessage('正在轉換地址...', 'info');
        try {
            const response = await fetch(`${BACKEND_BASE_URL}/geocode_address`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ address: address })
            });
            const data = await response.json();

            if (data.lat && data.lng) {
                currentCoords = {
                    latitude: data.lat,
                    longitude: data.lng
                };
                currentAddress = data.formatted_address || address;
                if (currentLocationDisplay) currentLocationDisplay.textContent = `當前位置: ${currentAddress}`;
                displayMessage('地址轉換成功！', 'success');
                if (placeTypeSelection) placeTypeSelection.classList.remove('hidden-section');
                if (manualLocationInputDiv) manualLocationInputDiv.classList.add('hidden-section');
            } else {
                displayMessage(`無法找到該地址的座標: ${data.error || '未知錯誤'}`, 'error');
                if (placeTypeSelection) placeTypeSelection.classList.add('hidden-section');
                currentCoords = null;
                currentAddress = null;
            }
            // 每次位置更新後，確保轉盤是空的或根據現有勾選項目更新
            if (typeof setupWheelWithPlaces === 'function') {
                const checkedIds = [...placesListDiv.querySelectorAll('input[type="checkbox"]:checked')].map(cb => cb.id);
                const selectedNames = checkedIds.map(id => allPlacesData[id]?.name).filter(Boolean);
                setupWheelWithPlaces(selectedNames);
            }
        } catch (error) {
            console.error('地址轉換失敗:', error);
            displayMessage('地址轉換失敗，請檢查網路連線或地址是否正確。', 'error');
            if (placeTypeSelection) placeTypeSelection.classList.add('hidden-section');
            currentCoords = null;
            currentAddress = null;
            if (typeof setupWheelWithPlaces === 'function') {
                setupWheelWithPlaces([]); // 清空轉盤
            }
        }
    }

    async function searchNearbyPlaces() {
        if (!currentCoords) {
            displayMessage('請先獲取或輸入您的位置。', 'warning');
            return;
        }

        const type = searchTypeSelect.value;
        const radius = searchRadiusSlider.value;
        displayMessage(`正在搜尋附近的 ${searchTypeSelect.options[searchTypeSelect.selectedIndex].text}...`, 'info');
        placesListDiv.innerHTML = '';
        if (placesActionsDiv) placesActionsDiv.classList.add('hidden-section');
        allPlacesData = {}; // Clear previous data

        try {
            const response = await fetch(`${BACKEND_BASE_URL}/nearby_search`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    lat: currentCoords.latitude,
                    lng: currentCoords.longitude,
                    type: type,
                    radius: parseInt(radius)
                })
            });

            const data = await response.json();

            if (data.places && data.places.length > 0) {
                data.places.forEach(place => {
                    const placeId = place.id || `osm-${place.osm_id}`;
                    allPlacesData[placeId] = place;

                    const placeItem = document.createElement('div');
                    placeItem.className = 'place-item';
                    placeItem.innerHTML = `
                        <input type="checkbox" id="${placeId}" checked>
                        <label for="${placeId}">
                            <h3>${place.name}</h3>
                            <p>${place.formatted_address || place.vicinity || '無地址資訊'}</p>
                            ${place.rating ? `<p>評分: ${place.rating} (${place.user_ratings_total} 則評論)</p>` : ''}
                            ${place.distance ? `<p>距離: ${place.distance.toFixed(2)} 公尺</p>` : ''}
                        </label>
                    `;
                    placesListDiv.appendChild(placeItem);
                });

                // ✅ 搜尋結束後，使用所有地點名稱建立轉盤（預設全選）
                const selectedNames = data.places.map(p => p.name).filter(Boolean);
                if (selectedNames.length > 0 && typeof setupWheelWithPlaces === 'function') {
                    setupWheelWithPlaces(selectedNames);
                }

                displayMessage(`找到 ${data.places.length} 個結果。`, 'success');
                if (placesActionsDiv) placesActionsDiv.classList.remove('hidden-section');
            } else {
                displayMessage('沒有找到符合條件的地點。', 'warning');
                if (placesActionsDiv) placesActionsDiv.classList.add('hidden-section');
                if (typeof setupWheelWithPlaces === 'function') {
                    setupWheelWithPlaces([]); // 清空轉盤
                }
            }
        } catch (error) {
            console.error('搜尋失敗:', error);
            displayMessage('搜尋地點失敗，請稍後再試。', 'error');
            if (placesActionsDiv) placesActionsDiv.classList.add('hidden-section');
            if (typeof setupWheelWithPlaces === 'function') {
                setupWheelWithPlaces([]); // 清空轉盤
            }
        }
    }
});