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
    // GLOBAL STATE AND DATA MANAGEMENT
    // ============================================================================

    /**
     * Global chart data store - single source of truth for chart data
     * This prevents stale data from being used by timers or closures
     */
    window.chartDataStore = {
        today: {
            date: null,          // ISO date string (YYYY-MM-DD)
            data: null,          // Array of [timeStr, low, med, high, margin]
            fetchedAt: null,     // Timestamp when data was fetched
            granularity: 'quarter_hour',
            priceRange: { minPrice: 0, maxPrice: 15 }
        },
        tomorrow: {
            date: null,
            data: null,
            fetchedAt: null,
            granularity: 'quarter_hour',
            priceRange: { minPrice: 0, maxPrice: 15 }
        },

        /**
         * Update chart data for a role
         */
        setData: function (role, date, data, granularity, priceRange) {
            if (!this[role]) return;
            this[role] = {
                date: date,
                data: data,
                fetchedAt: Date.now(),
                granularity: granularity || 'quarter_hour',
                priceRange: priceRange || { minPrice: 0, maxPrice: 15 }
            };
            console.log(`Chart data store updated for ${role}: date=${date}, rows=${data ? data.length : 0}`);
        },

        /**
         * Get data for a role, with date validation
         */
        getData: function (role, expectedDate) {
            if (!this[role] || !this[role].data) return null;
            
            // If expected date is provided, validate
            if (expectedDate && this[role].date !== expectedDate) {
                console.warn(`Chart data store: date mismatch for ${role}. Expected ${expectedDate}, got ${this[role].date}`);
                return null;
            }
            
            return this[role];
        },

        /**
         * Check if data is stale (older than 2 hours)
         */
        isStale: function (role) {
            if (!this[role] || !this[role].fetchedAt) return true;
            const age = Date.now() - this[role].fetchedAt;
            return age > 2 * 60 * 60 * 1000; // 2 hours
        },

        /**
         * Clear data for a role
         */
        clear: function (role) {
            if (this[role]) {
                this[role] = {
                    date: null,
                    data: null,
                    fetchedAt: null,
                    granularity: 'quarter_hour',
                    priceRange: { minPrice: 0, maxPrice: 15 }
                };
            }
        },

        /**
         * Clear all data
         */
        clearAll: function () {
            this.clear('today');
            this.clear('tomorrow');
        }
    };

    /**
     * Refresh lock to prevent concurrent refreshes
     */
    window.refreshLock = {
        today: false,
        tomorrow: false,
        lastRefresh: {
            today: 0,
            tomorrow: 0
        },

        /**
         * Acquire lock for a role
         */
        acquire: function (role) {
            if (this[role]) {
                console.log(`Refresh already in progress for ${role}, skipping`);
                return false;
            }
            // Also prevent rapid refreshes (within 2 seconds)
            const now = Date.now();
            if (now - this.lastRefresh[role] < 2000) {
                console.log(`Refresh for ${role} too soon, skipping`);
                return false;
            }
            this[role] = true;
            return true;
        },

        /**
         * Release lock for a role
         */
        release: function (role) {
            this[role] = false;
            this.lastRefresh[role] = Date.now();
        }
    };

    // ============================================================================
    // GLOBAL CHART REGISTRY AND CLEANUP MANAGEMENT
    // ============================================================================

    /**
     * Global chart instance registry for tracking and cleanup
     */
    window.chartRegistry = {
        instances: new Map(), // Map<canvasElement, {instance, role, date, registeredAt}>
        timers: new Map(), // Map<timerId, {role, purpose, createdAt}>
        eventListeners: new Map(), // Map<element, Array<{event, handler, options}>>
        sseConnections: new Set(), // Set of EventSource instances
        cleanupCallbacks: new Set(), // Set of cleanup functions

        /**
         * Register a chart instance with date metadata
         */
        registerChart: function (canvasElement, chartInstance, role, date) {
            if (!canvasElement || !chartInstance) return;
            // Clean up any existing chart on this canvas
            if (this.instances.has(canvasElement)) {
                this.unregisterChart(canvasElement);
            }
            this.instances.set(canvasElement, {
                instance: chartInstance,
                role: role || canvasElement.closest('[data-role]')?.getAttribute('data-role') || 'unknown',
                date: date,
                registeredAt: Date.now()
            });
            console.log(`Chart registered: role=${role}, date=${date}`);
        },

        /**
         * Unregister and destroy a chart instance
         */
        unregisterChart: function (canvasElement) {
            if (!canvasElement) return;
            const entry = this.instances.get(canvasElement);
            if (entry && entry.instance) {
                console.log(`Unregistering chart: role=${entry.role}, date=${entry.date}`);
                try {
                    entry.instance.destroy();
                } catch (e) {
                    console.warn('Error destroying chart instance:', e);
                }
            }
            this.instances.delete(canvasElement);
        },

        /**
         * Get chart info by role
         */
        getChartByRole: function (role) {
            for (const [canvas, entry] of this.instances) {
                if (entry.role === role && d.body.contains(canvas)) {
                    return { canvas, ...entry };
                }
            }
            return null;
        },

        /**
         * Track a timer for cleanup with metadata
         */
        trackTimer: function (timerId, role, purpose) {
            if (timerId) {
                this.timers.set(timerId, {
                    role: role || 'global',
                    purpose: purpose || 'unknown',
                    createdAt: Date.now()
                });
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
         * Clear all timers for a role
         */
        clearTimersForRole: function (role) {
            const toClear = [];
            this.timers.forEach((meta, timerId) => {
                if (meta.role === role) {
                    toClear.push(timerId);
                }
            });
            toClear.forEach(timerId => this.clearTimer(timerId));
            console.log(`Cleared ${toClear.length} timers for role ${role}`);
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
            this.timers.forEach((meta, timerId) => {
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

            // Clear data store
            window.chartDataStore.clearAll();

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
         * Periodic cleanup of orphaned chart instances and stale timers
         */
        cleanupOrphaned: function () {
            let cleanedCharts = 0;
            let cleanedTimers = 0;

            // Clean up orphaned charts
            const orphanedCharts = [];
            this.instances.forEach((entry, canvasElement) => {
                if (!d.body.contains(canvasElement)) {
                    orphanedCharts.push(canvasElement);
                } else {
                    try {
                        if (!entry.instance || !entry.instance.canvas) {
                            orphanedCharts.push(canvasElement);
                        }
                    } catch (e) {
                        orphanedCharts.push(canvasElement);
                    }
                }
            });
            orphanedCharts.forEach(canvasElement => {
                this.unregisterChart(canvasElement);
                cleanedCharts++;
            });

            // Clean up stale timers (older than 1 hour with no matching chart)
            const staleTimers = [];
            const now = Date.now();
            this.timers.forEach((meta, timerId) => {
                if (meta.role !== 'global' && now - meta.createdAt > 60 * 60 * 1000) {
                    const hasChart = this.getChartByRole(meta.role);
                    if (!hasChart) {
                        staleTimers.push(timerId);
                    }
                }
            });
            staleTimers.forEach(timerId => {
                this.clearTimer(timerId);
                cleanedTimers++;
            });

            if (cleanedCharts > 0 || cleanedTimers > 0) {
                console.log(`Cleanup: ${cleanedCharts} orphaned charts, ${cleanedTimers} stale timers`);
            }

            return { cleanedCharts, cleanedTimers };
        }
    };

    // Run periodic cleanup every 5 minutes
    window.chartRegistry.trackTimer(setInterval(() => {
        window.chartRegistry.cleanupOrphaned();
    }, 5 * 60 * 1000), 'global', 'periodic-cleanup');

    // ============================================================================
    // HTMX INTEGRATION - Critical for preventing orphaned Chart.js instances
    // ============================================================================

    /**
     * Destroy Chart.js instances BEFORE HTMX swaps elements
     * This prevents orphaned charts with stale event listeners
     */
    d.body.addEventListener('htmx:beforeSwap', function (evt) {
        const target = evt.detail.target;
        if (!target) return;

        // Find all canvases in the element being swapped
        const canvases = target.querySelectorAll ? target.querySelectorAll('canvas') : [];
        canvases.forEach(canvas => {
            if (canvas._chartInstance) {
                console.log('HTMX beforeSwap: destroying chart on canvas', canvas.id);
                try {
                    // Unregister from our registry (which also destroys)
                    window.chartRegistry.unregisterChart(canvas);
                } catch (e) {
                    console.warn('Error destroying chart before HTMX swap:', e);
                }
                canvas._chartInstance = null;
            }
            // Also clear any timers associated with this chart's role
            const role = canvas._chartRole;
            if (role) {
                window.chartRegistry.clearTimersForRole(role);
            }
        });

        // Also check if the target itself is a canvas
        if (target.tagName === 'CANVAS' && target._chartInstance) {
            console.log('HTMX beforeSwap: destroying chart on target canvas');
            try {
                window.chartRegistry.unregisterChart(target);
            } catch (e) {
                console.warn('Error destroying chart before HTMX swap:', e);
            }
            target._chartInstance = null;
        }
    });

    /**
     * Clean up after HTMX swap completes
     */
    d.body.addEventListener('htmx:afterSwap', function (evt) {
        // Run orphan cleanup after swap
        setTimeout(() => {
            window.chartRegistry.cleanupOrphaned();
        }, 100);
    });

    /**
     * Handle HTMX errors - release locks
     */
    d.body.addEventListener('htmx:responseError', function (evt) {
        console.error('HTMX response error:', evt.detail);
        // Release any locks that might be held
        window.refreshLock.release('today');
        window.refreshLock.release('tomorrow');
    });

    d.body.addEventListener('htmx:sendError', function (evt) {
        console.error('HTMX send error:', evt.detail);
        window.refreshLock.release('today');
        window.refreshLock.release('tomorrow');
    });

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
     */
    window.validateChartData = function (data, role = 'unknown') {
        const errors = [];

        if (!Array.isArray(data)) {
            errors.push('Data is not an array');
            return { isValid: false, errors, validRows: 0, totalRows: 0 };
        }

        if (data.length === 0) {
            errors.push('Data array is empty');
            return { isValid: false, errors, validRows: 0, totalRows: 0 };
        }

        const expectedLength = 5;
        let validRows = 0;
        let invalidRows = 0;

        data.forEach((row, index) => {
            if (!Array.isArray(row)) {
                invalidRows++;
                return;
            }
            if (row.length !== expectedLength) {
                invalidRows++;
                return;
            }
            const [timeStr, lowPrice, mediumPrice, highPrice, marginPrice] = row;
            if (typeof timeStr !== 'string' && typeof timeStr !== 'number') {
                invalidRows++;
                return;
            }
            validRows++;
        });

        if (invalidRows > 0) {
            console.warn(`Chart data validation for ${role}: ${invalidRows} invalid rows out of ${data.length}`);
        }

        const isValid = validRows >= Math.ceil(data.length * 0.8);
        return { isValid, validRows, invalidRows, totalRows: data.length, errors };
    };

    /**
     * Validate DOM element is still in the document
     */
    function isElementValid(element) {
        if (!element) return false;
        if (!(element instanceof Element)) return false;
        return d.body.contains(element);
    }

    /**
     * Get expected date for a chart role
     */
    function getExpectedDate(role) {
        return role === 'today' ? window.getHelsinkiDate(0) : window.getHelsinkiDate(1);
    }

    /**
     * Check if chart data matches current expected date
     */
    function isChartDataCurrent(role) {
        const expectedDate = getExpectedDate(role);
        const storeData = window.chartDataStore.getData(role, expectedDate);
        if (!storeData || !storeData.data || storeData.data.length === 0) {
            return false;
        }
        
        // Also check the DOM element's data-date attribute
        const section = d.getElementById(`${role}Chart`);
        if (section) {
            const domDate = section.getAttribute('data-date');
            if (domDate && domDate !== expectedDate) {
                console.warn(`Chart ${role}: DOM date ${domDate} != expected ${expectedDate}`);
                return false;
            }
        }
        
        return true;
    }

    /**
     * Refresh a chart by triggering its HTMX request
     */
    function refreshChart(role) {
        // Acquire lock
        if (!window.refreshLock.acquire(role)) {
            return;
        }

        const chartElement = d.getElementById(`${role}Chart`);
        if (!isElementValid(chartElement)) {
            console.warn(`Cannot refresh ${role} chart: element not found or invalid`);
            window.refreshLock.release(role);
            return;
        }

        const daysOffset = role === 'today' ? 0 : 1;
        const date = window.getHelsinkiDate(daysOffset);
        const margin = d.body.getAttribute('data-default-margin') || '0';

        // Clear stale data from store
        window.chartDataStore.clear(role);
        
        // Clear timers for this role (they will be recreated)
        window.chartRegistry.clearTimersForRole(role);

        console.log(`Refreshing ${role} chart with date ${date}`);

        htmx.ajax('GET', `/partials/prices?date=${date}&role=${role}&margin=${margin}`, {
            target: `#${role}Chart`,
            swap: 'outerHTML'
        }).then(() => {
            window.refreshLock.release(role);
        }).catch((e) => {
            console.error(`Error refreshing ${role} chart:`, e);
            window.refreshLock.release(role);
        });
    }

    /**
     * Check if a chart has valid, current data
     */
    function chartHasData(role) {
        const chartElement = d.querySelector(`#${role}Chart canvas`);
        if (!isElementValid(chartElement)) return false;
        if (!chartElement._chartInstance) return false;
        
        // Check data store for current data
        if (!isChartDataCurrent(role)) return false;
        
        return true;
    }

    // ============================================================================
    // MIDNIGHT TRANSITION AND DATE VALIDATION
    // ============================================================================

    let currentDate = window.getHelsinkiDate(0);
    let midnightCheckTimer = null;

    function checkMidnightTransition() {
        const newDate = window.getHelsinkiDate(0);
        if (newDate !== currentDate) {
            console.log(`Midnight transition detected: ${currentDate} -> ${newDate}`);
            currentDate = newDate;

            // Clear ALL stale data
            window.chartDataStore.clearAll();

            // Refresh both charts with new dates
            refreshChart('today');
            
            // Small delay for tomorrow to avoid race conditions
            setTimeout(() => {
                refreshChart('tomorrow');
            }, 500);
        }
    }

    // Check for midnight every minute
    midnightCheckTimer = window.chartRegistry.trackTimer(
        setInterval(checkMidnightTransition, ONE_MINUTE),
        'global', 'midnight-check'
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

        afternoonPollingTimer = window.chartRegistry.trackTimer(
            setInterval(() => {
                const hour = getHelsinkiHour();
                if (hour >= 14 && hour < 15) {
                    console.log('Afternoon polling: checking for tomorrow\'s prices');
                    refreshChart('tomorrow');
                } else {
                    stopAfternoonPolling();
                }
            }, ONE_MINUTE),
            'global', 'afternoon-polling'
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

    afternoonWindowCheckTimer = window.chartRegistry.trackTimer(
        setInterval(checkAfternoonWindow, ONE_MINUTE),
        'global', 'afternoon-window-check'
    );
    checkAfternoonWindow();

    // ============================================================================
    // NOW LINE UPDATE (uses data store, not canvas properties)
    // ============================================================================

    /**
     * Update the "now" line on today's chart
     * Uses data from the global store, not canvas properties
     */
    window.updateNowLine = function () {
        // First, verify data is current
        if (!isChartDataCurrent('today')) {
            console.log('Skipping now line update: data not current');
            return;
        }

        const todayChartCanvas = d.querySelector('#todayChart canvas');
        if (!isElementValid(todayChartCanvas)) return;
        if (!todayChartCanvas._chartInstance) return;
        
        // Verify canvas's chart date matches expected date
        const expectedDate = getExpectedDate('today');
        if (todayChartCanvas._chartDate && todayChartCanvas._chartDate !== expectedDate) {
            console.log(`Skipping now line update: canvas date ${todayChartCanvas._chartDate} != expected ${expectedDate}`);
            return;
        }

        const chart = todayChartCanvas._chartInstance;
        
        // Get data from the global store (single source of truth)
        const storeData = window.chartDataStore.getData('today', getExpectedDate('today'));
        if (!storeData || !storeData.data || storeData.data.length === 0) {
            console.log('Skipping now line update: no valid data in store');
            return;
        }

        const validData = storeData.data;
        const granularity = storeData.granularity || 'quarter_hour';
        const priceRange = storeData.priceRange || { minPrice: 0, maxPrice: 15 };

        // Validate chart instance
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
                if (annotations.nowline) {
                    delete annotations.nowline;
                }
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
                console.log('Now line updated to index', dataPointIndex);
            } catch (e) {
                console.warn('Error updating now line:', e);
            }
        }
    };

    /**
     * Update the current price display in the header
     * Uses data from the global store
     */
    window.updateCurrentPrice = function () {
        const priceElement = d.getElementById('current-price');
        if (!isElementValid(priceElement)) return;

        // Verify data is current
        if (!isChartDataCurrent('today')) {
            priceElement.textContent = '-- c/kWh';
            priceElement.style.color = '';
            return;
        }

        const storeData = window.chartDataStore.getData('today', getExpectedDate('today'));
        if (!storeData || !storeData.data || storeData.data.length === 0) {
            priceElement.textContent = '-- c/kWh';
            priceElement.style.color = '';
            return;
        }

        const validData = storeData.data;
        const granularity = storeData.granularity || 'quarter_hour';

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
                priceElement.textContent = '-- c/kWh';
                priceElement.style.color = '';
                return;
            }

            const [, lowPrice, mediumPrice, highPrice, marginPrice] = row;
            const safeLow = typeof lowPrice === 'number' && !isNaN(lowPrice) ? lowPrice : 0;
            const safeMed = typeof mediumPrice === 'number' && !isNaN(mediumPrice) ? mediumPrice : 0;
            const safeHigh = typeof highPrice === 'number' && !isNaN(highPrice) ? highPrice : 0;
            const safeMargin = typeof marginPrice === 'number' && !isNaN(marginPrice) ? marginPrice : 0;

            const spotPrice = safeLow + safeMed + safeHigh;
            // Always show spot price (without margin), even if it's zero or negative
            const displayPrice = spotPrice;
            const priceText = displayPrice.toFixed(2) + ' c/kWh';

            priceElement.textContent = priceText;

            if (displayPrice < 5.0) {
                priceElement.style.color = '#2ecc71';
            } else if (displayPrice < 15.0) {
                priceElement.style.color = '#f1c40f';
            } else {
                priceElement.style.color = '#e74c3c';
            }
        } else {
            priceElement.textContent = '-- c/kWh';
            priceElement.style.color = '';
        }
    };

    /**
     * Redraw existing charts (for window resize or screen re-enablement)
     */
    window.redrawCharts = function () {
        requestAnimationFrame(() => {
            ['today', 'tomorrow'].forEach(role => {
                const canvas = d.querySelector(`#${role}Chart canvas`);
                if (isElementValid(canvas) && canvas._chartInstance) {
                    // Verify data is current before redrawing
                    if (!isChartDataCurrent(role)) {
                        console.log(`Skipping redraw for ${role}: data not current, triggering refresh`);
                        refreshChart(role);
                        return;
                    }
                    try {
                        canvas._chartInstance.resize();
                        canvas._chartInstance.update('none');
                    } catch (e) {
                        console.warn(`Error redrawing ${role} chart:`, e);
                    }
                }
            });
        });
    };

    // ============================================================================
    // 15-MINUTE HEALTH CHECK WITH DATE VALIDATION
    // ============================================================================

    let healthCheckTimer = null;

    function fifteenMinuteHealthCheck() {
        console.log('15-minute health check');

        // First, check if dates are still correct
        const todayExpected = window.getHelsinkiDate(0);
        const tomorrowExpected = window.getHelsinkiDate(1);

        // Check today's data
        const todayData = window.chartDataStore.getData('today');
        if (!todayData || todayData.date !== todayExpected || window.chartDataStore.isStale('today')) {
            console.log(`Today's data invalid or stale (have: ${todayData?.date}, expected: ${todayExpected}), refreshing...`);
            refreshChart('today');
        } else {
            window.updateNowLine();
            window.updateCurrentPrice();
        }

        // Check tomorrow's data
        const tomorrowData = window.chartDataStore.getData('tomorrow');
        if (!tomorrowData || tomorrowData.date !== tomorrowExpected || window.chartDataStore.isStale('tomorrow')) {
            console.log(`Tomorrow's data invalid or stale (have: ${tomorrowData?.date}, expected: ${tomorrowExpected}), refreshing...`);
            refreshChart('tomorrow');
        }

        // Cleanup orphaned resources
        window.chartRegistry.cleanupOrphaned();
    }

    healthCheckTimer = window.chartRegistry.trackTimer(
        setInterval(fifteenMinuteHealthCheck, FIFTEEN_MINUTES),
        'global', 'health-check'
    );

    // ============================================================================
    // VISIBILITY CHANGE - WITH DATE VALIDATION
    // ============================================================================

    let lastVisibilityChange = 0;

    const visibilityChangeHandler = () => {
        if (!document.hidden) {
            const now = Date.now();
            // Debounce - ignore if within 1 second of last change
            if (now - lastVisibilityChange < 1000) return;
            lastVisibilityChange = now;

            console.log('Screen re-enabled, validating chart data...');
            
            // Check if dates are still valid
            const todayExpected = window.getHelsinkiDate(0);
            const tomorrowExpected = window.getHelsinkiDate(1);
            
            // Update current date tracking
            if (currentDate !== todayExpected) {
                console.log(`Date changed while hidden: ${currentDate} -> ${todayExpected}`);
                currentDate = todayExpected;
                
                // Clear all data and increment generations to invalidate old timers
                window.chartDataStore.clearAll();
                window.chartRegistry.clearTimersForRole('today');
                window.chartRegistry.clearTimersForRole('tomorrow');
                
                refreshChart('today');
                setTimeout(() => refreshChart('tomorrow'), 500);
                return;
            }

            // Validate and refresh if needed
            let needsRefresh = false;
            
            ['today', 'tomorrow'].forEach((role, index) => {
                const expectedDate = index === 0 ? todayExpected : tomorrowExpected;
                const storeData = window.chartDataStore.getData(role);
                
                // Also check if chart's date attribute matches
                const section = d.getElementById(`${role}Chart`);
                const domDate = section ? section.getAttribute('data-date') : null;
                const domMismatch = domDate && domDate !== expectedDate;
                
                if (!storeData || storeData.date !== expectedDate || window.chartDataStore.isStale(role) || domMismatch) {
                    console.log(`${role} chart needs refresh: storeDate=${storeData?.date}, domDate=${domDate}, expected=${expectedDate}, stale=${window.chartDataStore.isStale(role)}`);
                    // Clear timers for this role before refresh
                    window.chartRegistry.clearTimersForRole(role);
                    setTimeout(() => refreshChart(role), index * 500);
                    needsRefresh = true;
                }
            });

            if (!needsRefresh) {
                // Data is valid, verify chart canvas generations match store dates
                const todayCanvas = d.querySelector('#todayChart canvas');
                const tomorrowCanvas = d.querySelector('#tomorrowChart canvas');
                
                // Check if canvases exist and have valid chart instances
                const todayValid = todayCanvas && todayCanvas._chartInstance && todayCanvas._chartDate === todayExpected;
                const tomorrowValid = tomorrowCanvas && tomorrowCanvas._chartInstance && tomorrowCanvas._chartDate === tomorrowExpected;
                
                if (!todayValid) {
                    console.log('Today canvas/chart invalid, refreshing...');
                    window.chartRegistry.clearTimersForRole('today');
                    refreshChart('today');
                } else if (!tomorrowValid) {
                    console.log('Tomorrow canvas/chart invalid, refreshing...');
                    window.chartRegistry.clearTimersForRole('tomorrow');
                    refreshChart('tomorrow');
                } else {
                    // Everything valid, just redraw
                    setTimeout(() => {
                        window.redrawCharts();
                        window.updateNowLine();
                        window.updateCurrentPrice();
                    }, 100);
                }
            }
        }
    };

    window.chartRegistry.trackEventListener(document, 'visibilitychange', visibilityChangeHandler);

    // ============================================================================
    // VERSION CHECKING AND AUTO-RELOAD
    // ============================================================================

    let sseEventSource = null;
    let sseReconnectAttempts = 0;
    let sseReconnectTimer = null;
    let versionCheckTimer = null;
    let lastVersionCheck = 0;
    const VERSION_CHECK_INTERVAL = 60000; // Check every 60 seconds as fallback
    const SSE_MAX_RECONNECT_DELAY = 30000; // Max 30 seconds between SSE reconnect attempts

    /**
     * Check version via fetch API (more reliable than SSE on mobile)
     */
    async function checkVersionViaFetch() {
        const now = Date.now();
        // Debounce: don't check more than once every 5 seconds
        if (now - lastVersionCheck < 5000) {
            return;
        }
        lastVersionCheck = now;

        try {
            const response = await fetch('/version', {
                cache: 'no-store',
                headers: { 'Cache-Control': 'no-cache' }
            });
            if (!response.ok) {
                console.warn('Version check failed:', response.status);
                return;
            }
            const data = await response.json();
            const currentVersion = d.body.getAttribute('data-app-version');
            console.log('Version check:', data.version, '(current:', currentVersion + ')');

            if (data.version && data.version !== currentVersion) {
                console.log('New version detected via fetch:', data.version);
                showUpdateToast();
            }
        } catch (e) {
            console.warn('Version check error:', e.message);
        }
    }

    /**
     * Handle version update from SSE or fetch
     */
    function handleVersionUpdate(serverVersion) {
        const currentVersion = d.body.getAttribute('data-app-version');
        console.log('Version update:', serverVersion, '(current:', currentVersion + ')');

        if (serverVersion && serverVersion !== currentVersion) {
            console.log('New version available:', serverVersion);
            showUpdateToast();
        }
    }

    /**
     * Setup SSE connection with robust reconnection logic
     */
    function setupSSE() {
        // Clean up existing connection
        if (sseEventSource) {
            try {
                sseEventSource.close();
            } catch (e) { /* ignore */ }
            window.chartRegistry.closeSSE(sseEventSource);
            sseEventSource = null;
        }

        // Clear any pending reconnect timer
        if (sseReconnectTimer) {
            clearTimeout(sseReconnectTimer);
            sseReconnectTimer = null;
        }

        console.log('Establishing SSE connection...');

        try {
            sseEventSource = new EventSource('/events/version');
            window.chartRegistry.trackSSE(sseEventSource);

            sseEventSource.onopen = function () {
                console.log('SSE connection opened');
                sseReconnectAttempts = 0; // Reset on successful connection
            };

            sseEventSource.addEventListener('day_updated', function (event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Day update event received:', data);

                    const updatedDate = data.date;
                    const todayDate = window.getHelsinkiDate(0);
                    const tomorrowDate = window.getHelsinkiDate(1);

                    if (updatedDate === todayDate) {
                        window.chartDataStore.clear('today');
                        refreshChart('today');
                    } else if (updatedDate === tomorrowDate) {
                        window.chartDataStore.clear('tomorrow');
                        refreshChart('tomorrow');
                        stopAfternoonPolling();
                    }
                } catch (e) {
                    console.error('Error parsing day_updated event:', e);
                }
            });

            sseEventSource.addEventListener('version_update', function (event) {
                try {
                    const data = JSON.parse(event.data);
                    handleVersionUpdate(data.version);
                } catch (e) {
                    console.error('Error parsing version_update event:', e);
                }
            });

            sseEventSource.onerror = function (event) {
                console.log('SSE connection error, readyState:', sseEventSource.readyState);

                // EventSource.CLOSED = 2, means connection is closed and won't retry
                if (sseEventSource.readyState === EventSource.CLOSED) {
                    console.log('SSE connection closed, scheduling reconnect...');
                    scheduleSSEReconnect();
                }
                // EventSource.CONNECTING = 0, means it's trying to reconnect automatically
                // We'll let it try, but also schedule our own reconnect as backup
                else if (sseEventSource.readyState === EventSource.CONNECTING) {
                    // Give EventSource 10 seconds to reconnect, then take over
                    if (!sseReconnectTimer) {
                        sseReconnectTimer = setTimeout(() => {
                            if (sseEventSource && sseEventSource.readyState !== EventSource.OPEN) {
                                console.log('EventSource auto-reconnect taking too long, forcing reconnect');
                                setupSSE();
                            }
                        }, 10000);
                    }
                }
            };
        } catch (e) {
            console.error('Failed to create EventSource:', e);
            scheduleSSEReconnect();
        }
    }

    /**
     * Schedule SSE reconnection with exponential backoff
     */
    function scheduleSSEReconnect() {
        if (sseReconnectTimer) {
            return; // Already scheduled
        }

        sseReconnectAttempts++;
        // Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
        const delay = Math.min(1000 * Math.pow(2, sseReconnectAttempts - 1), SSE_MAX_RECONNECT_DELAY);

        console.log(`Scheduling SSE reconnect in ${delay}ms (attempt ${sseReconnectAttempts})`);

        sseReconnectTimer = setTimeout(() => {
            sseReconnectTimer = null;
            // Check version via fetch before reconnecting (in case server restarted)
            checkVersionViaFetch().then(() => {
                setupSSE();
            });
        }, delay);
    }

    /**
     * Start periodic version checking as fallback
     */
    function startVersionPolling() {
        if (versionCheckTimer) {
            clearInterval(versionCheckTimer);
        }

        // Check version periodically as fallback
        versionCheckTimer = setInterval(() => {
            // Only poll if SSE is not connected
            if (!sseEventSource || sseEventSource.readyState !== EventSource.OPEN) {
                console.log('SSE not connected, checking version via polling');
                checkVersionViaFetch();
            }
        }, VERSION_CHECK_INTERVAL);

        window.chartRegistry.trackTimer(versionCheckTimer);
    }

    // Initialize SSE and polling
    setupSSE();
    startVersionPolling();

    // Immediately check version on page load (in case SSE is slow to connect)
    // Use a small delay to let the page render first
    setTimeout(() => {
        console.log('Initial version check on page load');
        checkVersionViaFetch();
    }, 1000);

    // Check version when page becomes visible (critical for mobile!)
    window.chartRegistry.trackEventListener(document, 'visibilitychange', function () {
        if (!document.hidden) {
            console.log('Page became visible, checking version...');
            checkVersionViaFetch();

            // Also ensure SSE is connected
            if (!sseEventSource || sseEventSource.readyState !== EventSource.OPEN) {
                console.log('SSE not connected after visibility change, reconnecting...');
                setupSSE();
            }
        }
    });

    // Check version when window regains focus
    window.chartRegistry.trackEventListener(window, 'focus', function () {
        console.log('Window focused, checking version...');
        checkVersionViaFetch();
    });

    // Check version when coming back from sleep/background (pageshow event)
    window.chartRegistry.trackEventListener(window, 'pageshow', function (event) {
        if (event.persisted) {
            console.log('Page restored from bfcache, checking version...');
            checkVersionViaFetch();
            setupSSE(); // Reconnect SSE
        }
    });

    // Check version when network comes back online
    window.chartRegistry.trackEventListener(window, 'online', function () {
        console.log('Network online, checking version and reconnecting SSE...');
        checkVersionViaFetch();
        setupSSE();
    });

    // ============================================================================
    // UPDATE TOAST
    // ============================================================================

    let toastCountdownTimer = null;

    function showUpdateToast() {
        const toast = d.getElementById('toast');
        if (!isElementValid(toast)) return;

        // If toast is already visible and countdown is running, don't reset it
        if (!toast.classList.contains('hidden') && toastCountdownTimer) {
            console.log('Toast already showing, not resetting countdown');
            return;
        }

        toast.classList.remove('hidden');

        const reloadBtn = d.getElementById('reloadNow');
        if (reloadBtn) {
            reloadBtn.onclick = () => location.reload();
        }

        // Clear any existing timer before starting a new one
        if (toastCountdownTimer) {
            window.chartRegistry.clearTimer(toastCountdownTimer);
            toastCountdownTimer = null;
        }

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
                    console.log('Countdown reached 0, reloading page...');
                    location.reload();
                }
            }, 1000),
            'global', 'toast-countdown'
        );
    }

    // ============================================================================
    // OTHER EVENT HANDLERS
    // ============================================================================

    const fullscreenBtn = d.getElementById('fullscreen-btn');
    if (fullscreenBtn) {
        window.chartRegistry.trackEventListener(fullscreenBtn, 'click', () => {
            if (!document.fullscreenElement) {
                d.documentElement.requestFullscreen().catch(err => {
                    console.log('Fullscreen request failed:', err);
                });
            } else {
                document.exitFullscreen();
            }
        });
    }

    let resizeTimeout = null;
    window.chartRegistry.trackEventListener(window, 'resize', () => {
        if (resizeTimeout) clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            window.redrawCharts();
            resizeTimeout = null;
        }, 300);
    });

    window.chartRegistry.trackEventListener(window, 'pageshow', (event) => {
        if (event.persisted) {
            console.log('Page restored from cache, validating data...');
            // Trigger visibility change logic
            visibilityChangeHandler();
        }
    });

    window.chartRegistry.trackEventListener(window, 'focus', () => {
        // Just redraw, visibility handler will validate data
        setTimeout(() => window.redrawCharts(), 100);
    });

    window.chartRegistry.trackEventListener(d, 'keydown', (event) => {
        if (event.ctrlKey && event.shiftKey && event.key === 'R') {
            event.preventDefault();
            console.log('Manual refresh triggered');
            window.chartDataStore.clearAll();
            refreshChart('today');
            setTimeout(() => refreshChart('tomorrow'), 500);
        }
    });

    window.chartRegistry.trackEventListener(window, 'beforeunload', () => {
        window.chartRegistry.cleanup();
    });

    window.chartRegistry.trackEventListener(window, 'pagehide', () => {
        window.chartRegistry.cleanup();
    });

    // ============================================================================
    // DOG ICON CLICK - PLAY FART SOUND
    // ============================================================================

    const dogIcon = d.querySelector('.header-icon');
    const dogIconWrapper = d.querySelector('.dog-icon-wrapper');
    if (dogIcon && dogIconWrapper) {
        // Create audio element for fart sound
        const fartSound = new Audio('/static/fart.mp3');
        fartSound.preload = 'auto';

        window.chartRegistry.trackEventListener(dogIcon, 'click', () => {
            // Reset audio to beginning and play
            fartSound.currentTime = 0;
            fartSound.play().catch(err => {
                console.warn('Error playing fart sound:', err);
            });
            
            // Trigger fart animation
            dogIconWrapper.classList.remove('fart-animate');
            // Force reflow to restart animation
            void dogIconWrapper.offsetWidth;
            dogIconWrapper.classList.add('fart-animate');
        });
    }

    console.log('Spot is a Dog - Frontend initialized');
})();
