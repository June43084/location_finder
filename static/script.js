const BACKEND_BASE_URL = '';

let currentCoords = null;
let currentAddress = null;

let allPlaces = [];
let allPlacesData = {};
let selectedPlaceIds = new Set();

let currentPage = 1;

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

    const resultsSummary = document.getElementById('resultsSummary');

    const paginationDiv = document.getElementById('pagination');
    const prevPageBtn = document.getElementById('prevPageBtn');
    const nextPageBtn = document.getElementById('nextPageBtn');
    const pageInfo = document.getElementById('pageInfo');

    const jumpToWheelBtn = document.getElementById('jumpToWheelBtn');

    function displayMessage(msg, type = 'info') {
        if (!messageDiv) return;

        messageDiv.textContent = msg;
        messageDiv.className = `message ${type}`;
        messageDiv.style.display = 'block';
    }

    function getPageSize() {
        return window.matchMedia('(max-width: 768px)').matches ? 5 : 10;
    }

    function getTotalPages() {
        if (allPlaces.length === 0) return 1;

        return Math.ceil(allPlaces.length / getPageSize());
    }

    function clampCurrentPage() {
        currentPage = Math.max(
            1,
            Math.min(currentPage, getTotalPages())
        );
    }

    function getPlaceId(place, index) {
        if (place.id) return String(place.id);

        if (place.osm_id) {
            return `osm-${place.osm_id}`;
        }

        return [
            'fallback',
            index,
            place.name || 'unknown',
            place.latitude ?? '',
            place.longitude ?? ''
        ].join('-');
    }

    function rebuildPlaceMaps(places) {
        allPlacesData = {};
        window.placeUrlMap = {};

        places.forEach((place, index) => {
            const placeId = getPlaceId(place, index);

            place._frontendId = placeId;
            allPlacesData[placeId] = place;

            const placeName = place.name || '未命名地點';

            window.placeUrlMap[placeName] =
                place.map_url ||
                place.url ||
                '';
        });
    }

    function updateWheelFromSelection() {
        if (typeof window.setupWheelWithPlaces !== 'function') {
            return;
        }

        const selectedNames = allPlaces
            .filter(place => selectedPlaceIds.has(place._frontendId))
            .map(place => place.name)
            .filter(Boolean);

        window.setupWheelWithPlaces(selectedNames);

        if (jumpToWheelBtn) {
            jumpToWheelBtn.classList.toggle(
                'hidden-section',
                selectedNames.length === 0
            );
        }
    }

    function updateResultsSummary() {
        if (!resultsSummary) return;

        if (allPlaces.length === 0) {
            resultsSummary.textContent = '';
            return;
        }

        const pageSize = getPageSize();

        const start =
            (currentPage - 1) *
            pageSize +
            1;

        const end = Math.min(
            currentPage * pageSize,
            allPlaces.length
        );

        resultsSummary.textContent =
            `共 ${allPlaces.length} 家，目前顯示 ${start}–${end} 家，` +
            `已勾選 ${selectedPlaceIds.size} 家`;
    }

    function updatePagination() {
        if (
            !paginationDiv ||
            !prevPageBtn ||
            !nextPageBtn ||
            !pageInfo
        ) {
            return;
        }

        const totalPages = getTotalPages();

        if (allPlaces.length <= getPageSize()) {
            paginationDiv.classList.add('hidden-section');
            return;
        }

        paginationDiv.classList.remove('hidden-section');

        pageInfo.textContent =
            `第 ${currentPage} / ${totalPages} 頁`;

        prevPageBtn.disabled =
            currentPage <= 1;

        nextPageBtn.disabled =
            currentPage >= totalPages;
    }

    function createLinkParagraph(url, text) {
        const paragraph =
            document.createElement('p');

        paragraph.className =
            'place-link-row';

        const link =
            document.createElement('a');

        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = text;

        paragraph.appendChild(link);

        return paragraph;
    }

    function createPlaceCard(place) {
        const placeId =
            place._frontendId;

        const placeName =
            place.name ||
            '未命名地點';

        const placeItem =
            document.createElement('article');

        placeItem.className =
            'place-item';

        const headerRow =
            document.createElement('div');

        headerRow.className =
            'place-card-header';

        const checkbox =
            document.createElement('input');

        checkbox.type =
            'checkbox';

        checkbox.id =
            `place-${placeId}`;

        checkbox.className =
            'place-checkbox';

        checkbox.checked =
            selectedPlaceIds.has(placeId);

        checkbox.setAttribute(
            'aria-label',
            `選擇 ${placeName}`
        );

        checkbox.addEventListener(
            'change',
            () => {
                if (checkbox.checked) {
                    selectedPlaceIds.add(
                        placeId
                    );
                } else {
                    selectedPlaceIds.delete(
                        placeId
                    );
                }

                updateResultsSummary();
                updateWheelFromSelection();
            }
        );

        const title =
            document.createElement('label');

        title.className =
            'place-title';

        title.htmlFor =
            checkbox.id;

        title.textContent =
            placeName;

        headerRow.appendChild(
            checkbox
        );

        headerRow.appendChild(
            title
        );

        const image =
            document.createElement('img');

        image.src =
            place.photo_url ||
            '/static/placeholder.jpg';

        image.className =
            'place-img';

        image.alt =
            `${placeName} 圖片`;

        image.loading =
            'lazy';

        image.decoding =
            'async';

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

        const details =
            document.createElement('div');

        details.className =
            'place-details';

        const address =
            document.createElement('p');

        address.className =
            'place-address';

        address.textContent =
            place.formatted_address ||
            place.vicinity ||
            '無地址資訊';

        details.appendChild(
            address
        );

        if (place.map_url) {
            details.appendChild(
                createLinkParagraph(
                    place.map_url,
                    '📍 Google 地圖'
                )
            );
        }

        if (place.url) {
            details.appendChild(
                createLinkParagraph(
                    place.url,
                    '📍 OSM 地圖'
                )
            );
        }

        const googleSearchUrl =
            `https://www.google.com/search?q=${encodeURIComponent(placeName)}`;

        details.appendChild(
            createLinkParagraph(
                googleSearchUrl,
                '🔎 Google 搜尋'
            )
        );

        placeItem.appendChild(
            headerRow
        );

        placeItem.appendChild(
            image
        );

        placeItem.appendChild(
            details
        );

        return placeItem;
    }

    function renderCurrentPage({
        scrollToResults = false
    } = {}) {
        if (!placesListDiv) return;

        clampCurrentPage();

        placesListDiv.innerHTML = '';

        if (allPlaces.length === 0) {
            updateResultsSummary();
            updatePagination();
            return;
        }

        const pageSize =
            getPageSize();

        const startIndex =
            (currentPage - 1) *
            pageSize;

        const endIndex =
            Math.min(
                startIndex + pageSize,
                allPlaces.length
            );

        const pagePlaces =
            allPlaces.slice(
                startIndex,
                endIndex
            );

        const fragment =
            document.createDocumentFragment();

        pagePlaces.forEach(place => {
            fragment.appendChild(
                createPlaceCard(place)
            );
        });

        placesListDiv.appendChild(
            fragment
        );

        updateResultsSummary();
        updatePagination();

        if (scrollToResults) {
            const resultsContainer =
                document.getElementById(
                    'resultsContainer'
                );

            if (resultsContainer) {
                resultsContainer.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        }
    }

    function resetResults() {
        allPlaces = [];
        allPlacesData = {};

        selectedPlaceIds =
            new Set();

        currentPage = 1;

        window.placeUrlMap = {};

        if (placesListDiv) {
            placesListDiv.innerHTML = '';
        }

        if (placesActionsDiv) {
            placesActionsDiv.classList.add(
                'hidden-section'
            );
        }

        if (paginationDiv) {
            paginationDiv.classList.add(
                'hidden-section'
            );
        }

        if (jumpToWheelBtn) {
            jumpToWheelBtn.classList.add(
                'hidden-section'
            );
        }

        updateResultsSummary();

        if (
            typeof window.setupWheelWithPlaces ===
            'function'
        ) {
            window.setupWheelWithPlaces([]);
        }
    }

    if (updateWheelBtn) {
        updateWheelBtn.addEventListener(
            'click',
            () => {
                if (
                    selectedPlaceIds.size ===
                    0
                ) {
                    alert(
                        '請至少勾選一個地點才能轉盤！'
                    );

                    updateWheelFromSelection();

                    return;
                }

                updateWheelFromSelection();

                const wheelSection =
                    document.getElementById(
                        'wheelSection'
                    );

                if (wheelSection) {
                    wheelSection.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            }
        );
    }

    if (getLocationBtn) {
        getLocationBtn.addEventListener(
            'click',
            getGeoLocation
        );
    }

    if (searchNearbyBtn) {
        searchNearbyBtn.addEventListener(
            'click',
            searchNearbyPlaces
        );
    }

    if (selectAllBtn) {
        selectAllBtn.addEventListener(
            'click',
            () => {
                selectedPlaceIds =
                    new Set(
                        allPlaces.map(
                            place =>
                                place._frontendId
                        )
                    );

                renderCurrentPage();
                updateWheelFromSelection();
            }
        );
    }

    if (deselectAllBtn) {
        deselectAllBtn.addEventListener(
            'click',
            () => {
                selectedPlaceIds =
                    new Set();

                renderCurrentPage();
                updateWheelFromSelection();
            }
        );
    }

    if (prevPageBtn) {
        prevPageBtn.addEventListener(
            'click',
            () => {
                if (currentPage <= 1) {
                    return;
                }

                currentPage -= 1;

                renderCurrentPage({
                    scrollToResults: true
                });
            }
        );
    }

    if (nextPageBtn) {
        nextPageBtn.addEventListener(
            'click',
            () => {
                if (
                    currentPage >=
                    getTotalPages()
                ) {
                    return;
                }

                currentPage += 1;

                renderCurrentPage({
                    scrollToResults: true
                });
            }
        );
    }

    if (geocodeAddressBtn) {
        geocodeAddressBtn.addEventListener(
            'click',
            geocodeAddress
        );
    }

    if (addressInput) {
        addressInput.addEventListener(
            'keydown',
            event => {
                if (
                    event.key ===
                    'Enter'
                ) {
                    geocodeAddress();
                }
            }
        );
    }

    if (
        searchRadiusSlider &&
        radiusValueSpan
    ) {
        radiusValueSpan.textContent =
            searchRadiusSlider.value;

        searchRadiusSlider.addEventListener(
            'input',
            () => {
                radiusValueSpan.textContent =
                    searchRadiusSlider.value;
            }
        );
    }

    let lastWasMobile =
        window
            .matchMedia(
                '(max-width: 768px)'
            )
            .matches;

    window.addEventListener(
        'resize',
        () => {
            const isMobile =
                window
                    .matchMedia(
                        '(max-width: 768px)'
                    )
                    .matches;

            if (
                isMobile !==
                lastWasMobile
            ) {
                lastWasMobile =
                    isMobile;

                currentPage = 1;

                renderCurrentPage();
            }
        }
    );

    getGeoLocation();

    function getGeoLocation() {
        displayMessage(
            '正在獲取您的位置...',
            'info'
        );

        if (!navigator.geolocation) {
            displayMessage(
                '您的瀏覽器不支援地理位置功能，請手動輸入地址。',
                'error'
            );

            if (
                currentLocationDisplay
            ) {
                currentLocationDisplay.textContent =
                    '瀏覽器不支援地理位置。';
            }

            if (
                manualLocationInputDiv
            ) {
                manualLocationInputDiv.classList.remove(
                    'hidden-section'
                );
            }

            if (
                placeTypeSelection
            ) {
                placeTypeSelection.classList.add(
                    'hidden-section'
                );
            }

            resetResults();

            return;
        }

        navigator.geolocation.getCurrentPosition(
            position => {
                currentCoords = {
                    latitude:
                        position.coords.latitude,

                    longitude:
                        position.coords.longitude
                };

                reverseGeocode(
                    currentCoords.latitude,
                    currentCoords.longitude
                );

                if (
                    manualLocationInputDiv
                ) {
                    manualLocationInputDiv.classList.add(
                        'hidden-section'
                    );
                }
            },

            error => {
                console.error(
                    '獲取地理位置失敗:',
                    error
                );

                displayMessage(
                    '無法獲取您的位置，請手動輸入地址。',
                    'error'
                );

                if (
                    currentLocationDisplay
                ) {
                    currentLocationDisplay.textContent =
                        '無法獲取位置。';
                }

                if (
                    manualLocationInputDiv
                ) {
                    manualLocationInputDiv.classList.remove(
                        'hidden-section'
                    );
                }

                if (
                    placeTypeSelection
                ) {
                    placeTypeSelection.classList.add(
                        'hidden-section'
                    );
                }

                resetResults();
            },

            {
                enableHighAccuracy: true,
                timeout: 10000,
                maximumAge: 0
            }
        );
    }

    async function reverseGeocode(
        lat,
        lng
    ) {
        try {
            const response =
                await fetch(
                    `${BACKEND_BASE_URL}/reverse_geocode?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}`
                );

            const data =
                await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error ||
                    '反向地理編碼失敗'
                );
            }

            currentAddress =
                data.address ||
                '未知地址';

            if (
                currentLocationDisplay
            ) {
                currentLocationDisplay.textContent =
                    `當前位置: ${currentAddress}`;
            }

            displayMessage(
                data.address
                    ? '位置已更新。'
                    : '無法解析當前位置地址。',

                data.address
                    ? 'success'
                    : 'warning'
            );

            if (
                placeTypeSelection
            ) {
                placeTypeSelection.classList.remove(
                    'hidden-section'
                );
            }

        } catch (error) {
            console.error(
                '反向地理編碼失敗:',
                error
            );

            displayMessage(
                '反向地理編碼失敗。',
                'error'
            );

            currentAddress =
                '獲取地址失敗';

            if (
                currentLocationDisplay
            ) {
                currentLocationDisplay.textContent =
                    '當前位置: 獲取地址失敗';
            }

            if (
                placeTypeSelection
            ) {
                placeTypeSelection.classList.remove(
                    'hidden-section'
                );
            }
        }
    }

    async function geocodeAddress() {
        if (!addressInput) return;

        const address =
            addressInput.value.trim();

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
            const response =
                await fetch(
                    `${BACKEND_BASE_URL}/geocode_address`,
                    {
                        method: 'POST',

                        headers: {
                            'Content-Type':
                                'application/json'
                        },

                        body: JSON.stringify({
                            address
                        })
                    }
                );

            const data =
                await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error ||
                    '地址轉換失敗'
                );
            }

            if (
                data.lat ===
                undefined ||
                data.lng ===
                undefined
            ) {
                throw new Error(
                    '回傳資料沒有座標'
                );
            }

            currentCoords = {
                latitude:
                    data.lat,

                longitude:
                    data.lng
            };

            currentAddress =
                data.formatted_address ||
                address;

            if (
                currentLocationDisplay
            ) {
                currentLocationDisplay.textContent =
                    `當前位置: ${currentAddress}`;
            }

            displayMessage(
                '地址轉換成功！',
                'success'
            );

            if (
                placeTypeSelection
            ) {
                placeTypeSelection.classList.remove(
                    'hidden-section'
                );
            }

            if (
                manualLocationInputDiv
            ) {
                manualLocationInputDiv.classList.add(
                    'hidden-section'
                );
            }

            resetResults();

        } catch (error) {
            console.error(
                '地址轉換失敗:',
                error
            );

            displayMessage(
                `地址轉換失敗：${error.message}`,
                'error'
            );

            currentCoords = null;
            currentAddress = null;

            if (
                placeTypeSelection
            ) {
                placeTypeSelection.classList.add(
                    'hidden-section'
                );
            }

            resetResults();
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

        const type =
            searchTypeSelect.value;

        const radius =
            parseInt(
                searchRadiusSlider.value,
                10
            );

        const typeLabel =
            searchTypeSelect.options[
                searchTypeSelect.selectedIndex
            ].text;

        displayMessage(
            `正在搜尋附近的 ${typeLabel}...`,
            'info'
        );

        resetResults();

        try {
            const response =
                await fetch(
                    `${BACKEND_BASE_URL}/nearby_search`,
                    {
                        method: 'POST',

                        headers: {
                            'Content-Type':
                                'application/json'
                        },

                        body: JSON.stringify({
                            lat:
                                currentCoords.latitude,

                            lng:
                                currentCoords.longitude,

                            type,

                            radius
                        })
                    }
                );

            const data =
                await response.json();

            if (!response.ok) {
                if (
                    response.status ===
                    429
                ) {
                    throw new Error(
                        '搜尋太頻繁，請稍後再試'
                    );
                }

                throw new Error(
                    data.error ||
                    '搜尋失敗'
                );
            }

            if (
                !data.places ||
                data.places.length ===
                0
            ) {
                displayMessage(
                    '沒有找到符合條件的地點。',
                    'warning'
                );

                return;
            }

            allPlaces =
                data.places;

            rebuildPlaceMaps(
                allPlaces
            );

            selectedPlaceIds =
                new Set(
                    allPlaces.map(
                        place =>
                            place._frontendId
                    )
                );

            currentPage = 1;

            renderCurrentPage();

            updateWheelFromSelection();

            if (
                placesActionsDiv
            ) {
                placesActionsDiv.classList.remove(
                    'hidden-section'
                );
            }

            displayMessage(
                `找到 ${allPlaces.length} 個結果。`,
                'success'
            );

        } catch (error) {
            console.error(
                '搜尋失敗:',
                error
            );

            displayMessage(
                `搜尋地點失敗：${error.message}`,
                'error'
            );

            resetResults();
        }
    }
});
