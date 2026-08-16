const BACKEND_BASE_URL = '';


let currentCoords =
  null;


let allPlaces =
  [];


let selectedIds =
  new Set();


let currentPage =
  1;


window.placeUrlMap =
  {};


document.addEventListener(
  'DOMContentLoaded',
  () => {


    const $ =
      id =>
        document.getElementById(
          id
        );


    const message =
      $('message');


    const locationText =
      $('currentLocationDisplay');


    const manualBox =
      $('manualLocationInput');


    const typeSection =
      $('placeTypeSelection');


    const addressInput =
      $('addressInput');


    const searchType =
      $('searchType');


    const radius =
      $('searchRadius');


    const radiusValue =
      $('radiusValue');


    const placesList =
      $('placesList');


    const actions =
      $('placesActions');


    const summary =
      $('resultsSummary');


    const pagination =
      $('pagination');


    const prevBtn =
      $('prevPageBtn');


    const nextBtn =
      $('nextPageBtn');


    const pageInfo =
      $('pageInfo');


    const jumpBtn =
      $('jumpToWheelBtn');


    function show(
      text,
      type = 'info'
    ) {

      message.textContent =
        text;


      message.className =
        `message ${type}`;


      message.style.display =
        'block';

    }


    /*
     * 手機：
     * 5 家 / 頁
     *
     * 電腦：
     * 10 家 / 頁
     * =
     * 5 欄 × 2 列
     */

    function pageSize() {

      return matchMedia(
        '(max-width: 768px)'
      ).matches

        ?

        5

        :

        10;

    }


    function totalPages() {

      return Math.max(

        1,

        Math.ceil(

          allPlaces.length

          /

          pageSize()

        )

      );

    }


    function idFor(
      place,
      index
    ) {

      return String(

        place.id

        ||

        place.osm_id

        ||

        (
          `fallback-${index}-`
          +
          `${place.name}-`
          +
          `${place.latitude}-`
          +
          `${place.longitude}`
        )

      );

    }


    function preparePlaces() {

      window.placeUrlMap =
        {};


      allPlaces.forEach(
        (
          place,
          index
        ) => {


          place._id =
            idFor(
              place,
              index
            );


          window.placeUrlMap[
            place.name
          ] =

            place.map_url

            ||

            place.url

            ||

            '';

        }
      );

    }


    function selectedNames() {

      return allPlaces

        .filter(

          place =>
            selectedIds.has(
              place._id
            )

        )

        .map(

          place =>
            place.name

        )

        .filter(
          Boolean
        );

    }


    function updateWheel() {

      const names =
        selectedNames();


      window
        .setupWheelWithPlaces
        ?.
        (
          names
        );


      jumpBtn
        ?.
        classList
        .toggle(

          'hidden-section',

          names.length === 0

        );

    }


    function updateSummary() {

      if (
        !allPlaces.length
      ) {

        summary.textContent =
          '';

        return;

      }


      const size =
        pageSize();


      const start =

        (
          currentPage -
          1
        )

        *

        size

        +

        1;


      const end =
        Math.min(

          currentPage *
          size,

          allPlaces.length

        );


      summary.textContent =

        `共 ${allPlaces.length} 家，`

        +

        `目前顯示 ${start}–${end} 家，`

        +

        `已勾選 ${selectedIds.size} 家`;

    }


    function updatePager() {

      currentPage =
        Math.max(

          1,

          Math.min(

            currentPage,

            totalPages()

          )

        );


      if (
        allPlaces.length
        <=
        pageSize()
      ) {

        pagination
          .classList
          .add(
            'hidden-section'
          );

        return;

      }


      pagination
        .classList
        .remove(
          'hidden-section'
        );


      pageInfo.textContent =
        `第 ${currentPage} / ${totalPages()} 頁`;


      prevBtn.disabled =
        currentPage === 1;


      nextBtn.disabled =
        currentPage ===
        totalPages();

    }


    function linkRow(
      url,
      text
    ) {

      const paragraph =
        document.createElement(
          'p'
        );


      paragraph.className =
        'place-link-row';


      const link =
        document.createElement(
          'a'
        );


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


    function card(
      place
    ) {

      const article =
        document.createElement(
          'article'
        );


      article.className =
        'place-item';


      const header =
        document.createElement(
          'div'
        );


      header.className =
        'place-card-header';


      const checkbox =
        document.createElement(
          'input'
        );


      checkbox.type =
        'checkbox';


      checkbox.className =
        'place-checkbox';


      checkbox.id =
        `place-${place._id}`;


      checkbox.checked =
        selectedIds.has(
          place._id
        );


      const label =
        document.createElement(
          'label'
        );


      label.className =
        'place-title';


      label.htmlFor =
        checkbox.id;


      label.textContent =
        place.name
        ||
        '未命名地點';


      checkbox.onchange =
        () => {


          if (
            checkbox.checked
          ) {

            selectedIds.add(
              place._id
            );

          } else {

            selectedIds.delete(
              place._id
            );

          }


          updateSummary();

          updateWheel();

        };


      header.append(
        checkbox,
        label
      );


      /*
       * 只有目前頁面的卡片
       * 才會建立 img 元件。
       *
       * 所以：
       *
       * 手機最多一次載 5 張
       *
       * 電腦最多一次載 10 張
       */

      const image =
        document.createElement(
          'img'
        );


      image.className =
        'place-img';


      image.src =
        place.photo_url
        ||
        '/static/placeholder.jpg';


      image.alt =
        `${place.name || '地點'} 圖片`;


      image.loading =
        'lazy';


      image.decoding =
        'async';


      image.onerror =
        () => {


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
        document.createElement(
          'div'
        );


      details.className =
        'place-details';


      const address =
        document.createElement(
          'p'
        );


      address.className =
        'place-address';


      address.textContent =

        place.formatted_address

        ||

        place.vicinity

        ||

        '無地址資訊';


      details.appendChild(
        address
      );


      if (
        place.map_url
      ) {

        details.appendChild(

          linkRow(
            place.map_url,
            '📍 Google 地圖'
          )

        );

      }


      if (
        place.url
      ) {

        details.appendChild(

          linkRow(
            place.url,
            '📍 OSM 地圖'
          )

        );

      }


      const googleSearchUrl =

        'https://www.google.com/search?q='

        +

        encodeURIComponent(
          place.name
          ||
          ''
        );


      details.appendChild(

        linkRow(
          googleSearchUrl,
          '🔎 Google 搜尋'
        )

      );


      article.append(

        header,

        image,

        details

      );


      return article;

    }


    function render(
      scroll = false
    ) {


      currentPage =
        Math.max(

          1,

          Math.min(

            currentPage,

            totalPages()

          )

        );


      placesList.innerHTML =
        '';


      const size =
        pageSize();


      const start =

        (
          currentPage -
          1
        )

        *

        size;


      allPlaces

        .slice(
          start,
          start + size
        )

        .forEach(
          place => {


            placesList.appendChild(

              card(
                place
              )

            );

          }
        );


      updateSummary();

      updatePager();


      if (
        scroll
      ) {

        $('resultsContainer')
          .scrollIntoView({

            behavior:
              'smooth',

            block:
              'start'

          });

      }

    }


    function reset() {

      allPlaces =
        [];


      selectedIds =
        new Set();


      currentPage =
        1;


      placesList.innerHTML =
        '';


      actions
        .classList
        .add(
          'hidden-section'
        );


      pagination
        .classList
        .add(
          'hidden-section'
        );


      jumpBtn
        .classList
        .add(
          'hidden-section'
        );


      summary.textContent =
        '';


      window.placeUrlMap =
        {};


      window
        .setupWheelWithPlaces
        ?.
        (
          []
        );

    }


    $('selectAllBtn').onclick =
      () => {


        selectedIds =
          new Set(

            allPlaces.map(
              place =>
                place._id
            )

          );


        render();

        updateWheel();

      };


    $('deselectAllBtn').onclick =
      () => {


        selectedIds =
          new Set();


        render();

        updateWheel();

      };


    $('updateWheelBtn').onclick =
      () => {


        if (
          !selectedIds.size
        ) {

          alert(
            '請至少勾選一個地點才能轉盤！'
          );

          return;

        }


        updateWheel();


        $('wheelSection')
          .scrollIntoView({

            behavior:
              'smooth'

          });

      };


    prevBtn.onclick =
      () => {


        if (
          currentPage > 1
        ) {

          currentPage--;

          render(
            true
          );

        }

      };


    nextBtn.onclick =
      () => {


        if (
          currentPage
          <
          totalPages()
        ) {

          currentPage++;

          render(
            true
          );

        }

      };


    radiusValue.textContent =
      radius.value;


    radius.oninput =
      () => {

        radiusValue.textContent =
          radius.value;

      };


    /*
     * 如果使用者旋轉手機
     * 或 resize browser，
     * 手機 / 桌機模式切換時
     * 回到第一頁。
     */

    let mobile =
      matchMedia(
        '(max-width: 768px)'
      ).matches;


    window.addEventListener(
      'resize',
      () => {


        const nowMobile =
          matchMedia(
            '(max-width: 768px)'
          ).matches;


        if (
          nowMobile !==
          mobile
        ) {

          mobile =
            nowMobile;


          currentPage =
            1;


          render();

        }

      }
    );


    $('getLocationBtn').onclick =
      getLocation;


    $('geocodeAddressBtn').onclick =
      geocodeAddress;


    $('searchNearbyBtn').onclick =
      searchNearby;


    addressInput.onkeydown =
      event => {


        if (
          event.key ===
          'Enter'
        ) {

          geocodeAddress();

        }

      };


    async function reverseGeocode(
      lat,
      lng
    ) {

      try {


        const response =
          await fetch(

            `${BACKEND_BASE_URL}/reverse_geocode`

            +

            `?lat=${encodeURIComponent(lat)}`

            +

            `&lng=${encodeURIComponent(lng)}`

          );


        const data =
          await response.json();


        if (
          !response.ok
        ) {

          throw new Error(

            data.error

            ||

            '地址解析失敗'

          );

        }


        locationText.textContent =
          `當前位置: ${data.address}`;


        show(
          '位置已更新。',
          'success'
        );


        typeSection
          .classList
          .remove(
            'hidden-section'
          );


      } catch (
        error
      ) {


        /*
         * Geocoding 額度即使滿了，
         * GPS 座標還是已經取得。
         *
         * 所以不要阻止 Nearby Search。
         */

        locationText.textContent =
          '已取得座標，但地址名稱暫時無法取得。';


        show(
          error.message,
          'warning'
        );


        if (
          currentCoords
        ) {

          typeSection
            .classList
            .remove(
              'hidden-section'
            );

        }

      }

    }


    function getLocation() {


      show(
        '正在獲取您的位置...',
        'info'
      );


      if (
        !navigator.geolocation
      ) {


        show(
          '瀏覽器不支援定位，請手動輸入地址。',
          'error'
        );


        manualBox
          .classList
          .remove(
            'hidden-section'
          );


        return;

      }


      navigator.geolocation
        .getCurrentPosition(


          position => {


            currentCoords = {

              latitude:
                position
                  .coords
                  .latitude,

              longitude:
                position
                  .coords
                  .longitude

            };


            manualBox
              .classList
              .add(
                'hidden-section'
              );


            reverseGeocode(

              currentCoords.latitude,

              currentCoords.longitude

            );

          },


          () => {


            currentCoords =
              null;


            locationText.textContent =
              '無法獲取位置。';


            manualBox
              .classList
              .remove(
                'hidden-section'
              );


            typeSection
              .classList
              .add(
                'hidden-section'
              );


            show(
              '無法獲取位置，請手動輸入地址。',
              'error'
            );


            reset();

          },


          {

            enableHighAccuracy:
              true,

            timeout:
              10000,

            maximumAge:
              0

          }

        );

    }


    async function geocodeAddress() {


      const address =
        addressInput
          .value
          .trim();


      if (
        !address
      ) {

        show(
          '請輸入地址。',
          'error'
        );

        return;

      }


      show(
        '正在轉換地址...',
        'info'
      );


      try {


        const response =
          await fetch(

            `${BACKEND_BASE_URL}/geocode_address`,

            {

              method:
                'POST',

              headers: {

                'Content-Type':
                  'application/json'

              },

              body:
                JSON.stringify({

                  address:
                    address

                })

            }

          );


        const data =
          await response.json();


        if (
          !response.ok
        ) {

          throw new Error(

            data.error

            ||

            '地址轉換失敗'

          );

        }


        currentCoords = {

          latitude:
            data.lat,

          longitude:
            data.lng

        };


        locationText.textContent =

          `當前位置: `

          +

          (
            data.formatted_address

            ||

            address
          );


        manualBox
          .classList
          .add(
            'hidden-section'
          );


        typeSection
          .classList
          .remove(
            'hidden-section'
          );


        show(
          '地址轉換成功！',
          'success'
        );


        reset();


      } catch (
        error
      ) {


        show(
          error.message,
          'error'
        );

      }

    }


    async function searchNearby() {


      if (
        !currentCoords
      ) {

        show(
          '請先取得位置。',
          'warning'
        );

        return;

      }


      const type =
        searchType.value;


      const meters =
        parseInt(
          radius.value,
          10
        );


      const label =
        searchType
          .options[
            searchType
              .selectedIndex
          ]
          .text;


      show(
        `正在搜尋附近的 ${label}...`,
        'info'
      );


      reset();


      try {


        const response =
          await fetch(

            `${BACKEND_BASE_URL}/nearby_search`,

            {

              method:
                'POST',

              headers: {

                'Content-Type':
                  'application/json'

              },

              body:
                JSON.stringify({

                  lat:
                    currentCoords
                      .latitude,

                  lng:
                    currentCoords
                      .longitude,

                  type:
                    type,

                  radius:
                    meters

                })

            }

          );


        const data =
          await response.json();


        if (
          !response.ok
        ) {

          throw new Error(

            data.error

            ||

            '搜尋失敗'

          );

        }


        if (
          !data.places
          ?.
          length
        ) {

          show(
            '沒有找到符合條件的地點。',
            'warning'
          );

          return;

        }


        allPlaces =
          data.places;


        preparePlaces();


        /*
         * 搜尋後預設全部勾選。
         *
         * 即使店家跨不同分頁，
         * selectedIds 仍然完整保存。
         */

        selectedIds =
          new Set(

            allPlaces.map(
              place =>
                place._id
            )

          );


        currentPage =
          1;


        render();


        updateWheel();


        actions
          .classList
          .remove(
            'hidden-section'
          );


        show(
          `找到 ${allPlaces.length} 個結果。`,
          'success'
        );


      } catch (
        error
      ) {


        reset();


        show(
          error.message,
          'error'
        );

      }

    }


    getLocation();

  }
);
