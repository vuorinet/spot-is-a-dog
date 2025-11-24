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
    // GLOBAL CHART REGISTRY AND CLEANUP MANAGEMENT
    // ============================================================================

    /**
     * Global chart instance registry for tracking and cleanup
     */
    window.chartRegistry = {
        instances: new Map(), // Map<canvasElement, chartInstance>
        timers: new Set(), // Set of all timer IDs
        eventListeners: new Map(), // Map<element, Array<{event, handler, options}>>
        sseConnections: new Set(), // Set of EventSource instances
        cleanupCallbacks: new Set(), // Set of cleanup functions

        /**
         * Register a chart instance
         */
        registerChart: function (canvasElement, chartInstance) {
            if (!canvasElement || !chartInstance) return;
            // Clean up any existing chart on this canvas
            if (this.instances.has(canvasElement)) {
                this.unregisterChart(canvasElement);
            }
            this.instances.set(canvasElement, {
                instance: chartInstance,
                role: canvasElement.closest('[data-role]')?.getAttribute('data-role') || 'unknown',
                registeredAt: Date.now()
            });
        },

        /**
         * Unregister and destroy a chart instance
         */
        unregisterChart: function (canvasElement) {
            if (!canvasElement) return;
            const entry = this.instances.get(canvasElement);
            if (entry && entry.instance) {
                try {
                    entry.instance.destroy();
                } catch (e) {
                    console.warn('Error destroying chart instance:', e);
                }
            }
            this.instances.delete(canvasElement);
        },

        /**
         * Track a timer for cleanup
         */
        trackTimer: function (timerId) {
            if (timerId) {
                this.timers.add(timerId);
            }
            return timerId;
        },

        /**
         * Clear a tracked timer
         */
        clearTimer: function (timerId) {
            if (timerId) {
                clearInterval(timerId);
                clearTimeout(timerId);
                this.timers.delete(timerId);
            }
        },

        /**
         * Track an event listener for cleanup
         */
        trackEventListener: function (element, event, handler, options) {
            if (!element || !event || !handler) return;
            if (!this.eventListeners.has(element)) {
                this.eventListeners.set(element, []);
            }
            this.eventListeners.get(element).push({ event, handler, options });
            element.addEventListener(event, handler, options);
        },

        /**
         * Remove tracked event listeners for an element
         */
        removeEventListeners: function (element) {
            if (!element) return;
            const listeners = this.eventListeners.get(element);
            if (listeners) {
                listeners.forEach(({ event, handler, options }) => {
                    try {
                        element.removeEventListener(event, handler, options);
                    } catch (e) {
                        console.warn('Error removing event listener:', e);
                    }
                });
                this.eventListeners.delete(element);
            }
        },

        /**
         * Track an SSE connection
         */
        trackSSE: function (eventSource) {
            if (eventSource) {
                this.sseConnections.add(eventSource);
            }
        },

        /**
         * Close an SSE connection
         */
        closeSSE: function (eventSource) {
            if (eventSource) {
                try {
                    eventSource.close();
                } catch (e) {
                    console.warn('Error closing SSE connection:', e);
                }
                this.sseConnections.delete(eventSource);
            }
        },

        /**
         * Register a cleanup callback
         */
        registerCleanup: function (callback) {
            if (typeof callback === 'function') {
                this.cleanupCallbacks.add(callback);
            }
        },

        /**
         * Comprehensive cleanup of all resources
         */
        cleanup: function () {
            console.log('Performing comprehensive cleanup...');

            // Clean up all chart instances
            this.instances.forEach((entry, canvasElement) => {
                this.unregisterChart(canvasElement);
            });

            // Clear all timers
            this.timers.forEach(timerId => {
                clearInterval(timerId);
                clearTimeout(timerId);
            });
            this.timers.clear();

            // Remove all event listeners
            this.eventListeners.forEach((listeners, element) => {
                this.removeEventListeners(element);
            });

            // Close all SSE connections
            this.sseConnections.forEach(eventSource => {
                this.closeSSE(eventSource);
            });

            // Run cleanup callbacks
            this.cleanupCallbacks.forEach(callback => {
                try {
                    callback();
                } catch (e) {
                    console.warn('Error in cleanup callback:', e);
                }
            });
            this.cleanupCallbacks.clear();

            console.log('Cleanup complete');
        },

        /**
         * Periodic cleanup of orphaned chart instances
         */
        cleanupOrphanedCharts: function () {
            const orphaned = [];
            this.instances.forEach((entry, canvasElement) => {
                // Check if canvas element is still in DOM
                if (!d.body.contains(canvasElement)) {
                    orphaned.push(canvasElement);
                } else {
                    // Validate chart instance is still valid
                    try {
                        if (!entry.instance || !entry.instance.canvas) {
                            orphaned.push(canvasElement);
                        }
                    } catch (e) {
                        orphaned.push(canvasElement);
                    }
                }
            });

            orphaned.forEach(canvasElement => {
                console.log('Cleaning up orphaned chart instance');
                this.unregisterChart(canvasElement);
            });

            return orphaned.length;
        }
    };

    // Run periodic cleanup every 5 minutes
    window.chartRegistry.trackTimer(setInterval(() => {
        const cleaned = window.chartRegistry.cleanupOrphanedCharts();
        if (cleaned > 0) {
            console.log(`Cleaned up ${cleaned} orphaned chart instance(s)`);
        }
    }, 5 * 60 * 1000));

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
     * Validate chart data integrity
     * @param {Array} data - Chart data array
     * @param {string} role - Chart role for logging
     * @returns {Object} Validation result with isValid and errors
     */
    window.validateChartData = function (data, role = 'unknown') {
        const errors = [];

        if (!Array.isArray(data)) {
            errors.push('Data is not an array');
            return { isValid: false, errors };
        }

        if (data.length === 0) {
            errors.push('Data array is empty');
            return { isValid: false, errors };
        }

        // Check for expected data structure
        const expectedLength = 5; // [timeStr, lowPrice, mediumPrice, highPrice, marginPrice]
        let validRows = 0;
        let invalidRows = 0;

        data.forEach((row, index) => {
            if (!Array.isArray(row)) {
                invalidRows++;
                errors.push(`Row ${index} is not an array`);
                return;
            }

            if (row.length !== expectedLength) {
                invalidRows++;
                errors.push(`Row ${index} has incorrect length: expected ${expectedLength}, got ${row.length}`);
                return;
            }

            // Validate data types
            const [timeStr, lowPrice, mediumPrice, highPrice, marginPrice] = row;

            if (typeof timeStr !== 'string' && typeof timeStr !== 'number') {
                invalidRows++;
                errors.push(`Row ${index}: timeStr must be string or number, got ${typeof timeStr}`);
                return;
            }

            // Prices should be numbers (or null/undefined)
            const prices = [lowPrice, mediumPrice, highPrice, marginPrice];
            prices.forEach((price, priceIndex) => {
                if (price !== null && price !== undefined && (typeof price !== 'number' || isNaN(price))) {
                    invalidRows++;
                    errors.push(`Row ${index}, price[${priceIndex}]: must be number, null, or undefined, got ${typeof price}`);
                }
            });

            validRows++;
        });

        if (invalidRows > 0) {
            console.warn(`Chart data validation for ${role}: ${invalidRows} invalid rows out of ${data.length}`);
        }

        // Consider data valid if at least 80% of rows are valid
        const isValid = validRows >= Math.ceil(data.length * 0.8);

        return {
            isValid,
            validRows,
            invalidRows,
            totalRows: data.length,
            errors: errors.slice(0, 10) // Limit error messages
        };
    }

    /**
     * Validate DOM element is still in the document
     * @param {Element} element - DOM element to validate
     * @returns {boolean}
     */
    function isElementValid(element) {
        if (!element) return false;
        if (!(element instanceof Element)) return false;
        return d.body.contains(element);
    }

    /**
     * Refresh a chart by triggering its HTMX request
     * @param {string} role - 'today' or 'tomorrow'
     */
    function refreshChart(role) {
        const chartElement = d.getElementById(`${role}Chart`);
        if (!isElementValid(chartElement)) {
            console.warn(`Cannot refresh ${role} chart: element not found or invalid`);
            return;
        }

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
     * Check if a chart has valid data
     * @param {string} role - 'today' or 'tomorrow'
     * @returns {boolean}
     */
    function chartHasData(role) {
        const chartElement = d.querySelector(`#${role}Chart canvas`);
        if (!isElementValid(chartElement)) return false;
        if (!chartElement._chartInstance) return false;
        if (!chartElement._validData || !Array.isArray(chartElement._validData)) return false;
        if (chartElement._validData.length === 0) return false;

        // Additional validation: check if chart instance is still valid
        try {
            if (!chartElement._chartInstance.canvas) return false;
        } catch (e) {
            return false;
        }

        return true;
    }

    // ============================================================================
    // MIDNIGHT TRANSITION
    // ============================================================================

    let currentDate = window.getHelsinkiDate(0);
    let midnightCheckTimer = null;

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
    midnightCheckTimer = window.chartRegistry.trackTimer(
        setInterval(checkMidnightTransition, ONE_MINUTE)
    );

    // ============================================================================
    // AFTERNOON POLLING (14:00-14:30 Helsinki time)
    // ============================================================================

    let afternoonPollingActive = false;
    let afternoonPollingTimer = null;
    let afternoonWindowCheckTimer = null;

    function startAfternoonPolling() {
        if (afternoonPollingActive) return;

        console.log('Starting afternoon polling for tomorrow\'s prices');
        afternoonPollingActive = true;

        // Poll every minute
        afternoonPollingTimer = window.chartRegistry.trackTimer(
            setInterval(() => {
                const hour = getHelsinkiHour();
                if (hour >= 14 && hour < 15) {
                    console.log('Afternoon polling: checking for tomorrow\'s prices');
                    refreshChart('tomorrow');
                } else {
                    // Outside the window, stop polling
                    stopAfternoonPolling();
                }
            }, ONE_MINUTE)
        );
    }

    function stopAfternoonPolling() {
        if (!afternoonPollingActive) return;

        console.log('Stopping afternoon polling');
        afternoonPollingActive = false;

        if (afternoonPollingTimer) {
            window.chartRegistry.clearTimer(afternoonPollingTimer);
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
    afternoonWindowCheckTimer = window.chartRegistry.trackTimer(
        setInterval(checkAfternoonWindow, ONE_MINUTE)
    );
    checkAfternoonWindow(); // Check immediately on load

    // ============================================================================
    // NOW LINE UPDATE
    // ============================================================================

    /**
     * Update the "now" line on today's chart
     */
    window.updateNowLine = function () {
        const todayChartCanvas = d.querySelector('#todayChart canvas');
        if (!isElementValid(todayChartCanvas)) return;
        if (!todayChartCanvas._chartInstance) return;

        const chart = todayChartCanvas._chartInstance;
        const validData = todayChartCanvas._validData;
        const granularity = todayChartCanvas._granularity || 'quarter_hour';

        if (!validData || !Array.isArray(validData) || validData.length === 0) return;

        // Validate chart instance is still valid
        try {
            if (!chart.canvas || !chart.options || !chart.options.plugins) return;
            if (!chart.options.plugins.annotation || !chart.options.plugins.annotation.annotations) return;
        } catch (e) {
            console.warn('Chart instance invalid, skipping now line update:', e);
            return;
        }

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
            try {
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
            } catch (e) {
                console.warn('Error updating now line:', e);
            }
        }
    };

    /**
     * Update the current price display in the header
     */
    window.updateCurrentPrice = function () {
        const priceElement = d.getElementById('current-price');
        if (!isElementValid(priceElement)) {
            console.log('Price element not found or invalid');
            return;
        }

        const todayChartCanvas = d.querySelector('#todayChart canvas');
        if (!isElementValid(todayChartCanvas) || !todayChartCanvas._validData) {
            console.log('Today chart element or data not found');
            return;
        }

        const validData = todayChartCanvas._validData;
        if (!Array.isArray(validData) || validData.length === 0) {
            console.log('Today chart data is invalid or empty');
            return;
        }

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
            if (!Array.isArray(row) || row.length < 5) {
                console.warn('Invalid row data at index', dataPointIndex);
                priceElement.textContent = '-- c/kWh';
                priceElement.style.color = '';
                return;
            }

            const [timeStr, lowPrice, mediumPrice, highPrice, marginPrice] = row;

            // Validate prices are numbers
            const safeLow = typeof lowPrice === 'number' && !isNaN(lowPrice) ? lowPrice : 0;
            const safeMed = typeof mediumPrice === 'number' && !isNaN(mediumPrice) ? mediumPrice : 0;
            const safeHigh = typeof highPrice === 'number' && !isNaN(highPrice) ? highPrice : 0;
            const safeMargin = typeof marginPrice === 'number' && !isNaN(marginPrice) ? marginPrice : 0;

            // Calculate spot price (sum of low, med, high)
            const spotPrice = safeLow + safeMed + safeHigh;
            const totalPrice = spotPrice + safeMargin;

            // Determine which price to show (prefer spot if available, otherwise total)
            const displayPrice = spotPrice > 0 ? spotPrice : totalPrice;
            const priceText = displayPrice.toFixed(2) + ' c/kWh';

            // Update the element
            priceElement.textContent = priceText;

            // Set color based on price (green for low, yellow for medium, red for high)
            // Using same thresholds and colors as backend: < 5.0 = green, 5.0-15.0 = yellow, >= 15.0 = red
            if (displayPrice < 5.0) {
                priceElement.style.color = '#2ecc71'; // green
            } else if (displayPrice < 15.0) {
                priceElement.style.color = '#f1c40f'; // yellow (same as backend bar coloring)
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

            if (isElementValid(todayCanvas) && todayCanvas._chartInstance) {
                try {
                    // Force Chart.js to recalculate size
                    todayCanvas._chartInstance.resize();
                    // Update without animation to refresh display
                    todayCanvas._chartInstance.update('none');
                } catch (e) {
                    console.warn('Error redrawing today chart:', e);
                }
            }

            if (isElementValid(tomorrowCanvas) && tomorrowCanvas._chartInstance) {
                try {
                    // Force Chart.js to recalculate size
                    tomorrowCanvas._chartInstance.resize();
                    // Update without animation to refresh display
                    tomorrowCanvas._chartInstance.update('none');
                } catch (e) {
                    console.warn('Error redrawing tomorrow chart:', e);
                }
            }
        });
    };

    // ============================================================================
    // 15-MINUTE HEALTH CHECK
    // ============================================================================

    let healthCheckTimer = null;

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

        // Clean up orphaned charts
        window.chartRegistry.cleanupOrphanedCharts();
    }

    // Run health check every 15 minutes
    healthCheckTimer = window.chartRegistry.trackTimer(
        setInterval(fifteenMinuteHealthCheck, FIFTEEN_MINUTES)
    );

    // ============================================================================
    // SSE EVENT HANDLING
    // ============================================================================

    let sseEventSource = null;

    function setupSSE() {
        // Close existing connection if any
        if (sseEventSource) {
            window.chartRegistry.closeSSE(sseEventSource);
        }

        sseEventSource = new EventSource('/events/version');
        window.chartRegistry.trackSSE(sseEventSource);

        const dayUpdatedHandler = function (event) {
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
        };

        const versionUpdateHandler = function (event) {
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
        };

        const errorHandler = function () {
            console.log('SSE connection error, will reconnect automatically');
        };

        sseEventSource.addEventListener('day_updated', dayUpdatedHandler);
        sseEventSource.addEventListener('version_update', versionUpdateHandler);
        sseEventSource.onerror = errorHandler;

        // Store handlers for cleanup
        sseEventSource._dayUpdatedHandler = dayUpdatedHandler;
        sseEventSource._versionUpdateHandler = versionUpdateHandler;
        sseEventSource._errorHandler = errorHandler;
    }

    setupSSE();

    // ============================================================================
    // UPDATE TOAST
    // ============================================================================

    let toastCountdownTimer = null;

    function showUpdateToast() {
        const toast = d.getElementById('toast');
        if (!isElementValid(toast)) return;

        toast.classList.remove('hidden');

        const reloadBtn = d.getElementById('reloadNow');
        if (reloadBtn) {
            reloadBtn.onclick = () => location.reload();
        }

        // Clear existing countdown timer if any
        if (toastCountdownTimer) {
            window.chartRegistry.clearTimer(toastCountdownTimer);
        }

        // Auto-reload after 30 seconds
        let countdown = 30;
        const countdownEl = d.getElementById('countdown');
        toastCountdownTimer = window.chartRegistry.trackTimer(
            setInterval(() => {
                countdown--;
                if (isElementValid(countdownEl)) {
                    countdownEl.textContent = `Auto-reload in ${countdown}s`;
                }
                if (countdown <= 0) {
                    window.chartRegistry.clearTimer(toastCountdownTimer);
                    toastCountdownTimer = null;
                    location.reload();
                }
            }, 1000)
        );
    }

    // ============================================================================
    // FULLSCREEN TOGGLE
    // ============================================================================

    const fullscreenBtn = d.getElementById('fullscreen-btn');
    if (fullscreenBtn) {
        const fullscreenHandler = () => {
            if (!document.fullscreenElement) {
                d.documentElement.requestFullscreen().catch(err => {
                    console.log('Fullscreen request failed:', err);
                });
            } else {
                document.exitFullscreen();
            }
        };
        window.chartRegistry.trackEventListener(fullscreenBtn, 'click', fullscreenHandler);
    }

    // ============================================================================
    // WINDOW RESIZE HANDLER
    // ============================================================================

    let resizeTimeout = null;

    const resizeHandler = () => {
        if (resizeTimeout) {
            clearTimeout(resizeTimeout);
        }
        resizeTimeout = window.chartRegistry.trackTimer(
            setTimeout(() => {
                // Redraw charts if they exist
                if (window.redrawCharts) {
                    window.redrawCharts();
                }
                resizeTimeout = null;
            }, 300)
        );
    };

    window.chartRegistry.trackEventListener(window, 'resize', resizeHandler);

    // ============================================================================
    // VISIBILITY CHANGE HANDLER (for screen re-enablement)
    // ============================================================================

    const visibilityChangeHandler = () => {
        if (!document.hidden) {
            // Screen was re-enabled, force chart resize
            console.log('Screen re-enabled, resizing charts...');
            setTimeout(() => {
                if (window.redrawCharts) {
                    window.redrawCharts();
                }
            }, 100); // Small delay to ensure DOM is ready
        }
    };

    window.chartRegistry.trackEventListener(document, 'visibilitychange', visibilityChangeHandler);

    // Handle page show event (when page is restored from back/forward cache or screen re-enabled)
    const pageShowHandler = (event) => {
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
    };

    window.chartRegistry.trackEventListener(window, 'pageshow', pageShowHandler);

    // Handle window focus (when screen is re-enabled, window regains focus)
    const focusHandler = () => {
        console.log('Window regained focus, resizing charts...');
        setTimeout(() => {
            if (window.redrawCharts) {
                window.redrawCharts();
            }
        }, 100);
    };

    window.chartRegistry.trackEventListener(window, 'focus', focusHandler);

    // ============================================================================
    // KEYBOARD SHORTCUTS
    // ============================================================================

    const keydownHandler = (event) => {
        // Ctrl+Shift+R for force refresh
        if (event.ctrlKey && event.shiftKey && event.key === 'R') {
            event.preventDefault();
            console.log('Manual refresh triggered');
            refreshChart('today');
            refreshChart('tomorrow');
        }
    };

    window.chartRegistry.trackEventListener(d, 'keydown', keydownHandler);

    // ============================================================================
    // BEFOREUNLOAD CLEANUP HANDLER
    // ============================================================================

    const beforeUnloadHandler = () => {
        console.log('Page unloading, performing cleanup...');
        window.chartRegistry.cleanup();
    };

    window.chartRegistry.trackEventListener(window, 'beforeunload', beforeUnloadHandler);
    window.chartRegistry.trackEventListener(window, 'pagehide', beforeUnloadHandler);

    // Also register cleanup for visibility change to hidden (when tab is backgrounded)
    const visibilityCleanupHandler = () => {
        if (document.hidden) {
            // Optional: perform light cleanup when tab is hidden
            // Full cleanup will happen on beforeunload
        }
    };

    window.chartRegistry.trackEventListener(document, 'visibilitychange', visibilityCleanupHandler);

    console.log('Spot is a Dog - Frontend initialized');
})();
