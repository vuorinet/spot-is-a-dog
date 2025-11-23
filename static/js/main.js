/**
 * Spot is a Dog - Frontend Logic
 *
 * Requirements:
 * 1. On page reload: Load both charts, draw "now" line
 * 2. At midnight: Update both charts with new dates
 * 3. At 14:00-14:30 Helsinki time: Poll for tomorrow's prices every minute
 * 4. Every 15 minutes: Update "now" line, check for missing data
 */

(function () {
    'use strict';

    const d = document;
    const HELSINKI_TZ = 'Europe/Helsinki';
    const FIFTEEN_MINUTES = 15 * 60 * 1000;
    const ONE_MINUTE = 60 * 1000;

    // ============================================================================
    // UTILITY FUNCTIONS
    // ============================================================================

    /**
     * Get Helsinki date in ISO format (YYYY-MM-DD)
     * @param {number} daysOffset - Days to add (0=today, 1=tomorrow)
     * @returns {string} ISO date string
     */
    window.getHelsinkiDate = function (daysOffset = 0) {
        const now = new Date();
        const helsinkiTime = new Date(now.toLocaleString('en-US', { timeZone: HELSINKI_TZ }));
        helsinkiTime.setDate(helsinkiTime.getDate() + daysOffset);
        const year = helsinkiTime.getFullYear();
        const month = String(helsinkiTime.getMonth() + 1).padStart(2, '0');
        const day = String(helsinkiTime.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    };

    /**
     * Get current Helsinki hour (0-23)
     */
    function getHelsinkiHour() {
        const now = new Date();
        const helsinkiTime = new Date(now.toLocaleString('en-US', { timeZone: HELSINKI_TZ }));
        return helsinkiTime.getHours();
    }

    /**
     * Refresh a chart by triggering its HTMX request
     * @param {string} role - 'today' or 'tomorrow'
     */
    function refreshChart(role) {
        const chartElement = d.getElementById(`${role}Chart`);
        if (!chartElement) return;

        const daysOffset = role === 'today' ? 0 : 1;
        const date = window.getHelsinkiDate(daysOffset);
        const margin = d.body.getAttribute('data-default-margin') || '0';

        console.log(`Refreshing ${role} chart with date ${date}`);

        htmx.ajax('GET', `/partials/prices?date=${date}&role=${role}&margin=${margin}`, {
            target: `#${role}Chart`,
            swap: 'outerHTML'
        });
    }

    /**
     * Check if a chart has data
     * @param {string} role - 'today' or 'tomorrow'
     * @returns {boolean}
     */
    function chartHasData(role) {
        const chartElement = d.querySelector(`#${role}Chart canvas`);
        return chartElement && chartElement._chartInstance && chartElement._validData && chartElement._validData.length > 0;
    }

    // ============================================================================
    // MIDNIGHT TRANSITION
    // ============================================================================

    let currentDate = window.getHelsinkiDate(0);

    function checkMidnightTransition() {
        const newDate = window.getHelsinkiDate(0);
        if (newDate !== currentDate) {
            console.log(`Midnight transition detected: ${currentDate} -> ${newDate}`);
            currentDate = newDate;

            // Refresh both charts with new dates
            refreshChart('today');
            refreshChart('tomorrow');
        }
    }

    // Check for midnight every minute
    setInterval(checkMidnightTransition, ONE_MINUTE);

    // ============================================================================
    // AFTERNOON POLLING (14:00-14:30 Helsinki time)
    // ============================================================================

    let afternoonPollingActive = false;
    let afternoonPollingTimer = null;

    function startAfternoonPolling() {
        if (afternoonPollingActive) return;

        console.log('Starting afternoon polling for tomorrow\'s prices');
        afternoonPollingActive = true;

        // Poll every minute
        afternoonPollingTimer = setInterval(() => {
            const hour = getHelsinkiHour();
            if (hour >= 14 && hour < 15) {
                console.log('Afternoon polling: checking for tomorrow\'s prices');
                refreshChart('tomorrow');
            } else {
                // Outside the window, stop polling
                stopAfternoonPolling();
            }
        }, ONE_MINUTE);
    }

    function stopAfternoonPolling() {
        if (!afternoonPollingActive) return;

        console.log('Stopping afternoon polling');
        afternoonPollingActive = false;

        if (afternoonPollingTimer) {
            clearInterval(afternoonPollingTimer);
            afternoonPollingTimer = null;
        }
    }

    function checkAfternoonWindow() {
        const hour = getHelsinkiHour();
        if (hour >= 14 && hour < 15) {
            startAfternoonPolling();
        } else {
            stopAfternoonPolling();
        }
    }

    // Check afternoon window every minute
    setInterval(checkAfternoonWindow, ONE_MINUTE);
    checkAfternoonWindow(); // Check immediately on load

    // ============================================================================
    // NOW LINE UPDATE
    // ============================================================================

    /**
     * Update the "now" line on today's chart
     */
    window.updateNowLine = function () {
        const todayChartCanvas = d.querySelector('#todayChart canvas');
        if (!todayChartCanvas || !todayChartCanvas._chartInstance) return;

        const chart = todayChartCanvas._chartInstance;
        const validData = todayChartCanvas._validData;
        const granularity = todayChartCanvas._granularity || 'quarter_hour';

        if (!validData || validData.length === 0) return;

        const now = new Date();
        const currentHour = now.getHours();
        const currentMinutes = now.getMinutes();
        let dataPointIndex = -1;

        if (granularity === 'quarter_hour') {
            const quarter = Math.floor(currentMinutes / 15);
            const intervalIndex = currentHour * 4 + quarter;
            dataPointIndex = validData.findIndex(row => row[0] === intervalIndex.toString());
        } else {
            dataPointIndex = validData.findIndex(row => row[0] === currentHour.toString());
        }

        if (dataPointIndex >= 0) {
            const annotations = chart.options.plugins.annotation.annotations;
            // Remove existing now line
            if (annotations.nowline) {
                delete annotations.nowline;
            }
            // Add new now line
            const priceRange = todayChartCanvas._originalPriceRange || { minPrice: 0, maxPrice: 10 };
            annotations.nowline = {
                type: 'line',
                xMin: dataPointIndex,
                xMax: dataPointIndex,
                yMin: priceRange.minPrice,
                yMax: priceRange.maxPrice,
                borderColor: 'rgba(255, 153, 0, 0.9)',
                borderWidth: 3,
                borderDash: [4, 3],
                drawTime: 'afterDatasetsDraw',
                xScaleID: 'x',
                yScaleID: 'y'
            };
            chart.update('none');
            console.log('Now line updated');
        }
    };

    /**
     * Update the current price display in the header
     */
    window.updateCurrentPrice = function () {
        const priceElement = d.getElementById('current-price');
        if (!priceElement) {
            console.log('Price element not found');
            return;
        }

        const todayChartCanvas = d.querySelector('#todayChart canvas');
        if (!todayChartCanvas || !todayChartCanvas._validData) {
            console.log('Today chart element or data not found');
            return;
        }

        const validData = todayChartCanvas._validData;
        const granularity = todayChartCanvas._granularity || 'quarter_hour';

        // Get current time interval
        const now = new Date();
        const currentHour = now.getHours();
        const currentMinutes = now.getMinutes();
        let dataPointIndex = -1;

        if (granularity === 'quarter_hour') {
            const quarter = Math.floor(currentMinutes / 15);
            const intervalIndex = currentHour * 4 + quarter;
            dataPointIndex = validData.findIndex(row => row[0] === intervalIndex.toString());
        } else {
            dataPointIndex = validData.findIndex(row => row[0] === currentHour.toString());
        }

        if (dataPointIndex >= 0 && dataPointIndex < validData.length) {
            const row = validData[dataPointIndex];
            const [timeStr, lowPrice, mediumPrice, highPrice, marginPrice] = row;

            // Calculate spot price (sum of low, med, high)
            const spotPrice = (lowPrice || 0) + (mediumPrice || 0) + (highPrice || 0);
            const totalPrice = spotPrice + (marginPrice || 0);

            // Determine which price to show (prefer spot if available, otherwise total)
            const displayPrice = spotPrice > 0 ? spotPrice : totalPrice;
            const priceText = displayPrice.toFixed(2) + ' c/kWh';

            // Update the element
            priceElement.textContent = priceText;

            // Set color based on price (green for low, yellow for medium, red for high)
            // Using a simple threshold: < 5 = green, 5-10 = yellow, > 10 = red
            if (displayPrice < 5) {
                priceElement.style.color = '#2ecc71'; // green
            } else if (displayPrice < 10) {
                priceElement.style.color = '#f39c12'; // yellow/orange
            } else {
                priceElement.style.color = '#e74c3c'; // red
            }

            console.log('Current price updated:', priceText);
        } else {
            console.log('Current time interval not found in data');
            priceElement.textContent = '-- c/kWh';
            priceElement.style.color = '';
        }
    };

    /**
     * Redraw existing charts (for window resize or screen re-enablement)
     */
    window.redrawCharts = function () {
        // Use requestAnimationFrame to ensure DOM is ready
        requestAnimationFrame(() => {
            const todayCanvas = d.querySelector('#todayChart canvas');
            const tomorrowCanvas = d.querySelector('#tomorrowChart canvas');

            if (todayCanvas && todayCanvas._chartInstance) {
                // Force Chart.js to recalculate size
                todayCanvas._chartInstance.resize();
                // Update without animation to refresh display
                todayCanvas._chartInstance.update('none');
            }

            if (tomorrowCanvas && tomorrowCanvas._chartInstance) {
                // Force Chart.js to recalculate size
                tomorrowCanvas._chartInstance.resize();
                // Update without animation to refresh display
                tomorrowCanvas._chartInstance.update('none');
            }
        });
    };

    // ============================================================================
    // 15-MINUTE HEALTH CHECK
    // ============================================================================

    function fifteenMinuteHealthCheck() {
        console.log('15-minute health check: updating now line and checking for missing data');

        // Update "now" line on today's chart
        window.updateNowLine();

        // Update current price display
        window.updateCurrentPrice();

        // Check if either chart is missing data and refresh if needed
        if (!chartHasData('today')) {
            console.log('Today\'s chart has no data, refreshing...');
            refreshChart('today');
        }

        if (!chartHasData('tomorrow')) {
            console.log('Tomorrow\'s chart has no data, refreshing...');
            refreshChart('tomorrow');
        }
    }

    // Run health check every 15 minutes
    setInterval(fifteenMinuteHealthCheck, FIFTEEN_MINUTES);

    // ============================================================================
    // SSE EVENT HANDLING
    // ============================================================================

    function setupSSE() {
        const eventSource = new EventSource('/events/version');

        eventSource.addEventListener('day_updated', function (event) {
            try {
                const data = JSON.parse(event.data);
                console.log('Day update event received:', data);

                const updatedDate = data.date;
                const todayDate = window.getHelsinkiDate(0);
                const tomorrowDate = window.getHelsinkiDate(1);

                // Refresh the appropriate chart
                if (updatedDate === todayDate) {
                    console.log('Today\'s prices updated');
                    refreshChart('today');
                } else if (updatedDate === tomorrowDate) {
                    console.log('Tomorrow\'s prices updated');
                    refreshChart('tomorrow');
                    // Stop afternoon polling since we got the data
                    stopAfternoonPolling();
                }
            } catch (e) {
                console.error('Error parsing day_updated event:', e);
            }
        });

        eventSource.addEventListener('version_update', function (event) {
            try {
                const data = JSON.parse(event.data);
                const currentVersion = d.body.getAttribute('data-app-version');
                if (data.version && data.version !== currentVersion) {
                    console.log('New version available:', data.version);
                    showUpdateToast();
                }
            } catch (e) {
                console.error('Error parsing version_update event:', e);
            }
        });

        eventSource.onerror = function () {
            console.log('SSE connection error, will reconnect automatically');
        };
    }

    setupSSE();

    // ============================================================================
    // UPDATE TOAST
    // ============================================================================

    function showUpdateToast() {
        const toast = d.getElementById('toast');
        if (!toast) return;

        toast.classList.remove('hidden');

        const reloadBtn = d.getElementById('reloadNow');
        if (reloadBtn) {
            reloadBtn.onclick = () => location.reload();
        }

        // Auto-reload after 30 seconds
        let countdown = 30;
        const countdownEl = d.getElementById('countdown');
        const countdownInterval = setInterval(() => {
            countdown--;
            if (countdownEl) {
                countdownEl.textContent = `Auto-reload in ${countdown}s`;
            }
            if (countdown <= 0) {
                clearInterval(countdownInterval);
                location.reload();
            }
        }, 1000);
    }

    // ============================================================================
    // FULLSCREEN TOGGLE
    // ============================================================================

    const fullscreenBtn = d.getElementById('fullscreen-btn');
    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', () => {
            if (!document.fullscreenElement) {
                d.documentElement.requestFullscreen().catch(err => {
                    console.log('Fullscreen request failed:', err);
                });
            } else {
                document.exitFullscreen();
            }
        });
    }

    // ============================================================================
    // WINDOW RESIZE HANDLER
    // ============================================================================

    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            // Redraw charts if they exist
            if (window.redrawCharts) {
                window.redrawCharts();
            }
        }, 300);
    });

    // ============================================================================
    // VISIBILITY CHANGE HANDLER (for screen re-enablement)
    // ============================================================================

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            // Screen was re-enabled, force chart resize
            console.log('Screen re-enabled, resizing charts...');
            setTimeout(() => {
                if (window.redrawCharts) {
                    window.redrawCharts();
                }
            }, 100); // Small delay to ensure DOM is ready
        }
    });

    // Handle page show event (when page is restored from back/forward cache or screen re-enabled)
    window.addEventListener('pageshow', (event) => {
        if (event.persisted) {
            // Page was restored from cache
            console.log('Page restored from cache, resizing charts...');
        } else {
            // Page was shown normally
            console.log('Page shown, resizing charts...');
        }
        setTimeout(() => {
            if (window.redrawCharts) {
                window.redrawCharts();
            }
        }, 100);
    });

    // Handle window focus (when screen is re-enabled, window regains focus)
    window.addEventListener('focus', () => {
        console.log('Window regained focus, resizing charts...');
        setTimeout(() => {
            if (window.redrawCharts) {
                window.redrawCharts();
            }
        }, 100);
    });

    // ============================================================================
    // KEYBOARD SHORTCUTS
    // ============================================================================

    d.addEventListener('keydown', event => {
        // Ctrl+Shift+R for force refresh
        if (event.ctrlKey && event.shiftKey && event.key === 'R') {
            event.preventDefault();
            console.log('Manual refresh triggered');
            refreshChart('today');
            refreshChart('tomorrow');
        }
    });

    console.log('Spot is a Dog - Frontend initialized');
})();
