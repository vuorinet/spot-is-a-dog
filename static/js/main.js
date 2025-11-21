(() => {
    const d = document;
    const toast = d.getElementById('toast');
    const reloadBtn = d.getElementById('reloadNow');
    const countdownEl = d.getElementById('countdown');

    // Keep-awake via URL query parameter
    let wakeLock = null;
    async function requestWakeLock() {
        try {
            wakeLock = await navigator.wakeLock.request('screen');
            wakeLock.addEventListener('release', () => {
                console.log('Wake lock released');
            });
            console.log('Wake lock activated');
        } catch (e) {
            console.warn('Wake Lock not available', e);
        }
    }

    // Check URL parameter for keep-awake
    const urlParams = new URLSearchParams(window.location.search);
    const keepAwakeParam = urlParams.get('keepAwake') || urlParams.get('keep-awake');
    if (keepAwakeParam === 'true' || keepAwakeParam === '1') {
        console.log('Keep-awake enabled via URL parameter');
        requestWakeLock();
    }

    // Version auto-reload SSE fallback to polling
    const currentVersion = d.body.getAttribute('data-app-version');
    let notified = false;
    function scheduleReloadToast() {
        if (notified) return;
        notified = true;
        toast.classList.remove('hidden');
        let sec = 5;
        countdownEl.textContent = `(${sec})`;
        const timer = setInterval(() => {
            sec -= 1;
            countdownEl.textContent = `(${sec})`;
            if (sec <= 0) {
                clearInterval(timer);
                location.reload();
            }
        }, 1000);
    }
    reloadBtn?.addEventListener('click', () => location.reload());
    function beginVersionWatch() {
        if (!!window.EventSource) {
            const es = new EventSource('/events/version');
            es.onmessage = ev => {
                const event = JSON.parse(ev.data);

                // Handle version updates
                if (
                    event.type === 'version' &&
                    event.version &&
                    event.version !== currentVersion
                ) {
                    scheduleReloadToast();
                }

                // Handle cache updates (midnight rotation, new data arrivals)
                if (
                    event.type === 'cache_rotated' ||
                    event.type === 'today_updated' ||
                    event.type === 'tomorrow_updated'
                ) {
                    console.log(`Cache event received: ${event.type}`, event);

                    // Show notification for important price updates
                    if (event.type === 'tomorrow_updated') {
                        const reason = event.reason || 'new data available';
                        console.log(`Tomorrow's prices updated: ${reason}`);

                        // Show a brief notification for tomorrow price updates
                        const existingNotification = d.querySelector(
                            '.price-update-notification'
                        );
                        if (existingNotification) {
                            existingNotification.remove();
                        }

                        const notification = d.createElement('div');
                        notification.className = 'price-update-notification';
                        notification.style.cssText = `
              position: fixed;
              top: 20px;
              left: 50%;
              transform: translateX(-50%);
              background: #2ecc71;
              color: white;
              padding: 10px 16px;
              border-radius: 4px;
              font-size: 14px;
              z-index: 1001;
              opacity: 0.95;
              box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            `;
                        notification.textContent = `📈 Tomorrow's electricity prices updated!`;
                        d.body.appendChild(notification);

                        // Remove notification after 4 seconds
                        setTimeout(() => {
                            if (notification.parentNode) {
                                notification.remove();
                            }
                        }, 4000);
                    }

                    // Always refresh charts when server notifies us of new data
                    window.refreshCharts();
                }
            };
            es.onerror = () => {
                es.close();
                console.warn(
                    'SSE connection error, falling back to polling for version updates.'
                );
                setInterval(checkVersion, 60000);
            };
        } else {
            setInterval(checkVersion, 60000);
        }
    }
    async function checkVersion() {
        try {
            const r = await fetch('/version');
            const j = await r.json();
            if (j.version && j.version !== currentVersion) scheduleReloadToast();
        } catch {}
    }
    beginVersionWatch();

    // Function to update yellow line immediately
    window.updateNowLineImmediately = function () {
        const todayChartElement = d.querySelector('#todayChart [id*="googleChart"]');
        if (
            todayChartElement &&
            todayChartElement._chartInstance &&
            todayChartElement._validData &&
            window.addNowLine
        ) {
            console.log(
                'Updating yellow line immediately due to focus/visibility change'
            );
            // Remove existing line
            const svgElement = todayChartElement.querySelector('svg');
            if (svgElement) {
                const existingLines = svgElement.querySelectorAll('.now-line');
                existingLines.forEach(line => line.remove());
            }
            // Add new line at current time
            window.addNowLine(
                todayChartElement._chartInstance,
                todayChartElement,
                todayChartElement._validData
            );
        }
    };

    // Function to hide yellow line when window goes to background
    window.hideNowLine = function () {
        const todayChartElement = d.querySelector('#todayChart [id*="googleChart"]');
        if (todayChartElement) {
            console.log('Hiding yellow line - window in background');
            const svgElement = todayChartElement.querySelector('svg');
            if (svgElement) {
                const existingLines = svgElement.querySelectorAll('.now-line');
                existingLines.forEach(line => line.remove());
            }
        }
    };

    // Track the current date to detect midnight transitions
    let lastKnownDate = new Date().toDateString();

    // Keep a dedicated midnight timer so today/tomorrow swap happens exactly at local midnight
    let midnightTimerId = null;

    function scheduleMidnightRefresh() {
        if (midnightTimerId) {
            clearTimeout(midnightTimerId);
        }

        const now = new Date();
        const nextMidnight = new Date(now);
        // small buffer (5s) to ensure the clock has rolled over before we inspect the date string
        nextMidnight.setHours(24, 0, 5, 0);
        const waitMs = Math.max(1000, nextMidnight.getTime() - now.getTime());

        midnightTimerId = setTimeout(() => {
            console.log(
                'Local midnight reached - forcing today/tomorrow rotation'
            );
            window.handleMidnightTransition();
            scheduleMidnightRefresh();
        }, waitMs);
    }

    window.handleMidnightTransition = function () {
        const dateChanged = window.checkDateChange();
        if (!dateChanged) {
            // In rare cases something else already updated lastKnownDate; still ensure charts redraw now
            console.log(
                'Midnight handler forcing manual refresh (date already recorded)'
            );
            if (window.chartDataTimestamps) {
                window.chartDataTimestamps.today = null;
                window.chartDataTimestamps.tomorrow = null;
            }
            if (window.chartDataMetadata) {
                window.chartDataMetadata.today = { fetchedDate: null, fetchedHour: null };
                window.chartDataMetadata.tomorrow = { fetchedDate: null, fetchedHour: null };
            }
            window.refreshCharts();
        }
    };

    scheduleMidnightRefresh();

    // Track when we last checked for activity (to detect sleep/wake cycles)
    let lastActivityCheck = Date.now();

    // Add debouncing for refresh operations to prevent multiple simultaneous refreshes
    let refreshDebounceTimeout = null;
    let isRefreshInProgress = false;

    // Track when chart data was last fetched and key metadata
    window.chartDataTimestamps = {
        today: null,
        tomorrow: null,
    };

    // Track additional metadata for smart refresh decisions
    window.chartDataMetadata = {
        today: { fetchedDate: null, fetchedHour: null },
        tomorrow: { fetchedDate: null, fetchedHour: null },
    };

    // Function to check if date has changed and refresh if needed
    window.checkDateChange = function () {
        const currentDate = new Date().toDateString();
        if (currentDate !== lastKnownDate) {
            console.log('Date changed detected:', lastKnownDate, '->', currentDate);
            lastKnownDate = currentDate;

            // Clear chart data timestamps to force fresh data
            window.chartDataTimestamps = {
                today: null,
                tomorrow: null,
            };
            window.chartDataMetadata = {
                today: { fetchedDate: null, fetchedHour: null },
                tomorrow: { fetchedDate: null, fetchedHour: null },
            };

            // Date has changed - refresh charts to get new data
            console.log('Forcing complete chart refresh due to date change');
            window.refreshCharts();
            return true;
        }
        return false;
    };

    // Function to check if device likely woke up from sleep
    window.checkWakeFromSleep = function () {
        const now = Date.now();
        const timeSinceLastCheck = now - lastActivityCheck;
        lastActivityCheck = now;

        // Different thresholds for different scenarios
        const SHORT_SLEEP = 10 * 60 * 1000;  // 10 minutes - quick screen lock
        const MEDIUM_SLEEP = 30 * 60 * 1000; // 30 minutes - longer absence
        const LONG_SLEEP = 2 * 60 * 60 * 1000; // 2 hours - significant absence

        if (timeSinceLastCheck > LONG_SLEEP) {
            console.log(
                `Long sleep detected: ${Math.round(timeSinceLastCheck / 1000 / 60)} minutes inactive - data likely stale`
            );
            return { type: 'long', minutes: Math.round(timeSinceLastCheck / 1000 / 60) };
        } else if (timeSinceLastCheck > MEDIUM_SLEEP) {
            console.log(
                `Medium sleep detected: ${Math.round(timeSinceLastCheck / 1000 / 60)} minutes inactive - might need refresh`
            );
            return { type: 'medium', minutes: Math.round(timeSinceLastCheck / 1000 / 60) };
        } else if (timeSinceLastCheck > SHORT_SLEEP) {
            console.log(
                `Short sleep detected: ${Math.round(timeSinceLastCheck / 1000 / 60)} minutes inactive - probably just screen lock`
            );
            return { type: 'short', minutes: Math.round(timeSinceLastCheck / 1000 / 60) };
        }
        return false;
    };

    // Function to check if chart data is stale and needs refresh
    // Returns object with detailed staleness info for selective refresh
    window.checkStaleData = function () {
        const now = new Date();
        const nowTimestamp = now.getTime();
        const currentHour = now.getHours();
        const currentDate = now.toDateString();

        const STALE_THRESHOLD = 30 * 60 * 1000; // 30 minutes
        let todayStale = false;
        let tomorrowStale = false;
        let reasons = [];

        // Check today's data
        if (window.chartDataTimestamps.today && window.chartDataMetadata.today) {
            const todayAge = nowTimestamp - window.chartDataTimestamps.today;
            const fetchedDate = window.chartDataMetadata.today.fetchedDate;

            // Basic staleness check
            if (todayAge > STALE_THRESHOLD) {
                reasons.push(`today data ${Math.round(todayAge / 1000 / 60)} min old`);
                todayStale = true;
            }

            // Cross-day check: if data was fetched on a different date
            if (fetchedDate && fetchedDate !== currentDate) {
                reasons.push(`today data from previous day (${fetchedDate})`);
                todayStale = true;
            }
        }

        // Check tomorrow's data with smart 2 PM logic
        if (window.chartDataTimestamps.tomorrow && window.chartDataMetadata.tomorrow) {
            const tomorrowAge = nowTimestamp - window.chartDataTimestamps.tomorrow;
            const fetchedHour = window.chartDataMetadata.tomorrow.fetchedHour;
            const fetchedDate = window.chartDataMetadata.tomorrow.fetchedDate;

            // Basic staleness check
            if (tomorrowAge > STALE_THRESHOLD) {
                reasons.push(
                    `tomorrow data ${Math.round(tomorrowAge / 1000 / 60)} min old`
                );
                tomorrowStale = true;
            }

            // Cross-day check: if data was fetched on a different date
            if (fetchedDate && fetchedDate !== currentDate) {
                reasons.push(`tomorrow data from previous day (${fetchedDate})`);
                tomorrowStale = true;
            }

            // Smart 2 PM check: if data was fetched before 2 PM and it's now after 2 PM
            if (fetchedHour !== null && fetchedHour < 14 && currentHour >= 14) {
                reasons.push(
                    `tomorrow data fetched pre-2PM (${fetchedHour}:00), now post-2PM`
                );
                tomorrowStale = true;
            }

            // Extended afternoon check: during 2-6 PM, be more aggressive about refreshing
            // tomorrow's data (since prices can be published late or updated)
            if (currentHour >= 14 && currentHour <= 18) {
                const AFTERNOON_STALE_THRESHOLD = 20 * 60 * 1000; // 20 minutes during afternoon
                if (tomorrowAge > AFTERNOON_STALE_THRESHOLD) {
                    reasons.push(
                        `tomorrow data ${Math.round(
                            tomorrowAge / 1000 / 60
                        )} min old during afternoon hours`
                    );
                    tomorrowStale = true;
                }
            }
        }

        const hasStaleData = todayStale || tomorrowStale;
        if (hasStaleData && reasons.length > 0) {
            console.log(`Stale data detected: ${reasons.join(', ')}`);
        }

        return {
            hasStaleData,
            todayStale,
            tomorrowStale,
            reasons
        };
    };

    // Listen for window focus and visibility changes
    window.addEventListener('focus', () => {
        window.handleVisibilityChange('window-focus');
    });

    // Note: We don't hide on blur because the window might still be visible
    // (e.g., side-by-side windows on desktop). Only hide when actually not visible.

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            window.handleVisibilityChange('document-visibility');
        } else {
            console.log('Document became hidden - hiding yellow line');
            window.hideNowLine();
        }
    });

    // Listen for wake lock events (when screen is re-enabled)
    if ('wakeLock' in navigator) {
        // Unfortunately, there's no direct wake lock event, but we can detect when
        // the page regains focus after potential screen sleep
        let wasHidden = false;
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                wasHidden = true;
            } else if (wasHidden) {
                console.log('Screen likely re-enabled - using smart visibility handler');
                setTimeout(() => {
                    window.handleVisibilityChange('wake-lock-visibility');
                }, 100); // Small delay to ensure everything is ready
                wasHidden = false;
            }
        });
    }

    // Periodic checks as backup (every 5 minutes when page is visible)
    setInterval(() => {
        if (!document.hidden) {
            // Update activity timestamp to detect sleep/wake cycles
            lastActivityCheck = Date.now();

            // Check for date change (triggers refresh if needed)
            const dateChanged = window.checkDateChange();

            // If date didn't change, check for stale data
            if (!dateChanged) {
                const staleDataInfo = window.checkStaleData();
                if (staleDataInfo.hasStaleData) {
                    console.log('Periodic check detected stale data - refreshing charts selectively');
                    window.refreshChartsSelective(staleDataInfo.todayStale, staleDataInfo.tomorrowStale);
                }
            }
        }
    }, 5 * 60 * 1000); // 5 minutes

    // More frequent price update check (every 10 minutes during key hours)
    setInterval(() => {
        if (!document.hidden) {
            const now = new Date();
            const hour = now.getHours();

            // Check more frequently during key price update times:
            // - Morning hours (6-10 AM) when today's final prices might update
            // - Critical afternoon hours (2-6 PM) when tomorrow's prices are published/updated
            const isKeyHour = (hour >= 6 && hour <= 10) || (hour >= 14 && hour <= 18);

            if (isKeyHour) {
                console.log(`Key hour check (${hour}:00) - checking for stale data`);

                // Use the smart stale data logic that includes 2 PM awareness
                const staleDataInfo = window.checkStaleData();
                if (staleDataInfo.hasStaleData) {
                    console.log('Key hour check triggered selective refresh');
                    window.refreshChartsSelective(staleDataInfo.todayStale, staleDataInfo.tomorrowStale);
                }

                // Special case: during prime tomorrow publication time (2-4 PM),
                // check for tomorrow data that might be missing entirely
                if (hour >= 14 && hour <= 16 && !window.chartDataTimestamps.tomorrow) {
                    console.log(
                        'Critical time window: No tomorrow data cached, forcing tomorrow refresh only'
                    );
                    window.refreshChartsSelective(false, true); // Only refresh tomorrow chart
                }
            }
        }
    }, 10 * 60 * 1000); // 10 minutes

    // Add keyboard shortcut for manual chart refresh
    document.addEventListener('keydown', event => {
        // Ctrl+Shift+R for force refresh (avoid conflicts with browser refresh)
        if (event.ctrlKey && event.shiftKey && event.key === 'R') {
            event.preventDefault();
            console.log('Manual chart refresh triggered via Ctrl+Shift+R');
            window.refreshCharts();
        }
    });

    // Handle orientation changes only on mobile devices
    let currentOrientation = screen.orientation
        ? screen.orientation.angle
        : window.orientation;
    let orientationChangeTimeout;

    function handleOrientationChange() {
        clearTimeout(orientationChangeTimeout);
        orientationChangeTimeout = setTimeout(() => {
            const newOrientation = screen.orientation
                ? screen.orientation.angle
                : window.orientation;

            if (newOrientation !== currentOrientation) {
                console.log(
                    `Orientation changed: ${currentOrientation}° → ${newOrientation}°`
                );
                currentOrientation = newOrientation;

                // Only redraw charts on actual orientation change
                window.redrawCharts();
            }
        }, 300); // Wait for orientation change to complete
    }

    // Only listen for orientation changes, ignore resize events on mobile
    if (window.innerWidth <= 900) {
        console.log('Mobile device detected - listening for orientation changes only');
        window.addEventListener('orientationchange', handleOrientationChange);

        // Also listen to screen.orientation.onchange if available (more reliable)
        if (screen.orientation && screen.orientation.addEventListener) {
            screen.orientation.addEventListener('change', handleOrientationChange);
        }
    } else {
        // On desktop, only listen for actual window resize (not mobile scrolling issues)
        console.log('Desktop device detected - listening for window resize');
        let resizeTimeout;

        window.addEventListener('resize', () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                console.log('Desktop window resized - redrawing charts');
                window.redrawCharts();
            }, 300);
        });
    }

    // Add HTMX event listeners for debugging chart swaps
    d.body.addEventListener('htmx:afterSwap', function (evt) {
        console.log(
            'HTMX afterSwap:',
            evt.detail.target.id || evt.detail.target.className
        );
    });

    d.body.addEventListener('htmx:swapError', function (evt) {
        console.error('HTMX swap error:', evt.detail);
    });

    // Function to redraw existing charts (for responsive resize)
    window.redrawCharts = function () {
        console.log('Redrawing existing charts for responsive layout...');

        // Find existing chart instances and redraw them
        const todayChartElement = d.querySelector('#todayChart [id*="googleChart"]');
        const tomorrowChartElement = d.querySelector(
            '#tomorrowChart [id*="googleChart"]'
        );

        // Only redraw if charts actually exist (avoid double drawing on page load)
        if (
            todayChartElement &&
            todayChartElement._chartInstance &&
            window.createChart
        ) {
            console.log('Redrawing today chart with cached data');
            window.createChart('today');
        }

        if (
            tomorrowChartElement &&
            tomorrowChartElement._chartInstance &&
            window.createChart
        ) {
            console.log('Redrawing tomorrow chart with cached data');
            window.createChart('tomorrow');
        }
    };

    // Smart refresh function that handles visibility/focus events intelligently
    window.handleVisibilityChange = function(eventSource = 'unknown') {
        console.log(`Handle visibility change from: ${eventSource}`);

        // Clear any pending debounced refresh
        if (refreshDebounceTimeout) {
            clearTimeout(refreshDebounceTimeout);
            refreshDebounceTimeout = null;
        }

        // If a refresh is already in progress, just update the yellow line
        if (isRefreshInProgress) {
            console.log('Refresh in progress - only updating yellow line');
            window.updateNowLineImmediately();
            return;
        }

        // Debounce multiple rapid events (common during Android unlock)
        refreshDebounceTimeout = setTimeout(() => {
            console.log('Processing visibility change after debounce...');

            // Check if we likely woke from sleep (now returns detailed info)
            const sleepInfo = window.checkWakeFromSleep();

            // Check for date change first (most important)
            const dateChanged = window.checkDateChange();

            if (dateChanged) {
                // Date changed - must refresh both charts, exit early
                console.log('Date change detected - exiting handleVisibilityChange early (both charts refreshed)');
                return; // checkDateChange already called refreshCharts
            }

            // Check if data is actually stale (now returns detailed info)
            const staleDataInfo = window.checkStaleData();

            // Smart refresh logic based on sleep duration and data staleness
            if (staleDataInfo.hasStaleData) {
                console.log('Refreshing charts due to stale data');
                // Use selective refresh - only refresh charts that are actually stale
                window.refreshChartsSelective(staleDataInfo.todayStale, staleDataInfo.tomorrowStale);
            } else if (sleepInfo) {
                // Handle different sleep scenarios
                if (sleepInfo.type === 'long') {
                    // Long sleep (2+ hours) - check if data is actually stale before refreshing
                    // Even after long sleep, if data is fresh, just update the yellow line
                    console.log('Long sleep detected - but data is fresh, only updating yellow line');
                    window.updateNowLineImmediately();
                } else if (sleepInfo.type === 'medium') {
                    // Medium sleep (30min-2h) - only refresh if it's during key hours when data might have updated
                    const hour = new Date().getHours();
                    const isKeyHour = (hour >= 6 && hour <= 10) || (hour >= 14 && hour <= 18);

                    if (isKeyHour) {
                        console.log('Medium sleep during key hours - refreshing charts');
                        window.refreshCharts();
                    } else {
                        console.log('Medium sleep but not key hours - only updating yellow line');
                        window.updateNowLineImmediately();
                    }
                } else {
                    // Short sleep (10-30min) - just quick screen lock, only update yellow line
                    console.log('Short sleep (screen lock) - only updating yellow line');
                    window.updateNowLineImmediately();
                }
            } else {
                // No sleep detected, just update yellow line position
                console.log('No sleep detected - only updating yellow line');
                window.updateNowLineImmediately();
            }

            refreshDebounceTimeout = null;
        }, 150); // 150ms debounce to handle rapid multiple events
    };

    // Selective chart refresh function - only refreshes charts that need new data
    window.refreshChartsSelective = function (refreshToday = true, refreshTomorrow = true) {
        // Prevent multiple simultaneous refreshes
        if (isRefreshInProgress) {
            console.log('Refresh already in progress - skipping duplicate request');
            return;
        }

        if (!refreshToday && !refreshTomorrow) {
            console.log('No charts need refreshing - skipping');
            return;
        }

        isRefreshInProgress = true;
        const chartsToRefresh = [];
        if (refreshToday) chartsToRefresh.push('today');
        if (refreshTomorrow) chartsToRefresh.push('tomorrow');

        console.log(`Selectively refreshing charts: ${chartsToRefresh.join(', ')}`);

        // Show a brief visual indicator that data is being refreshed
        const existingIndicator = d.querySelector('.refresh-indicator');
        if (existingIndicator) {
            existingIndicator.remove();
        }

        const indicator = d.createElement('div');
        indicator.className = 'refresh-indicator';
        indicator.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      background: #333;
      color: #fff;
      padding: 8px 12px;
      border-radius: 4px;
      font-size: 12px;
      z-index: 1000;
      opacity: 0.9;
      pointer-events: none;
    `;
        indicator.textContent = `Updating ${chartsToRefresh.join(' & ')} prices...`;
        d.body.appendChild(indicator);

        // Remove indicator after 3 seconds
        setTimeout(() => {
            if (indicator.parentNode) {
                indicator.remove();
            }
        }, 3000);

        // Clean up cached data for charts being refreshed
        if (refreshToday) {
            const todayChartElement = d.querySelector('#todayChart [id*="googleChart"]');
            if (todayChartElement) {
                clearChartCache(todayChartElement);
                console.log('Cleared cached chart data for today');
            }
        }

        if (refreshTomorrow) {
            const tomorrowChartElement = d.querySelector('#tomorrowChart [id*="googleChart"]');
            if (tomorrowChartElement) {
                clearChartCache(tomorrowChartElement);
                console.log('Cleared cached chart data for tomorrow');
            }
        }

        // Verify target elements exist before refreshing
        const todayTarget = d.querySelector('#todayChart');
        const tomorrowTarget = d.querySelector('#tomorrowChart');

        if (refreshToday && !todayTarget) {
            console.error('Today chart target element not found');
            return;
        }
        if (refreshTomorrow && !tomorrowTarget) {
            console.error('Tomorrow chart target element not found');
            return;
        }

        // Trigger HTMX refresh for selected charts
        const margin = d.body.getAttribute('data-default-margin') || '0';

        try {
            if (refreshToday) {
                htmx.ajax('GET', `/partials/prices?date=today&margin=${margin}`, {
                    target: '#todayChart',
                    swap: 'outerHTML',
                });
            }
            if (refreshTomorrow) {
                htmx.ajax('GET', `/partials/prices?date=tomorrow&margin=${margin}`, {
                    target: '#tomorrowChart',
                    swap: 'outerHTML',
                });
            }
            console.log(`Selective chart refresh requests sent for: ${chartsToRefresh.join(', ')}`);
        } catch (error) {
            console.error('Error refreshing charts:', error);
        }

        // Reset refresh flag after HTMX requests complete (with timeout fallback)
        setTimeout(() => {
            isRefreshInProgress = false;
            console.log('Selective chart refresh completed');
        }, 2000); // 2 second timeout to ensure flag is reset
    };

    // Helper function to clear chart cache
    function clearChartCache(chartElement) {
        if (!chartElement) return;

        // Clear timers
        if (chartElement._nowLineTimer) {
            clearInterval(chartElement._nowLineTimer);
            clearTimeout(chartElement._nowLineTimer);
            delete chartElement._nowLineTimer;
        }

        // Clear event handlers
        if (chartElement._visibilityHandler) {
            document.removeEventListener('visibilitychange', chartElement._visibilityHandler);
            delete chartElement._visibilityHandler;
        }
        if (chartElement._focusHandler) {
            window.removeEventListener('focus', chartElement._focusHandler);
            delete chartElement._focusHandler;
        }
        if (chartElement._pageShowHandler) {
            window.removeEventListener('pageshow', chartElement._pageShowHandler);
            delete chartElement._pageShowHandler;
        }
        if (chartElement._pageHideHandler) {
            window.removeEventListener('pagehide', chartElement._pageHideHandler);
            delete chartElement._pageHideHandler;
        }

        // Clear cached chart data
        delete chartElement._chartInstance;
        delete chartElement._chartData;
        delete chartElement._validData;
        delete chartElement._granularity;
        delete chartElement._originalPriceRange;
    }

    // Global function to refresh charts (fetch new data from server) - backwards compatibility
    window.refreshCharts = function () {
        // Use selective refresh with both charts enabled for backwards compatibility
        window.refreshChartsSelective(true, true);
    };

    // Function to update current price display in header
    window.updateCurrentPrice = function() {
        console.log('updateCurrentPrice called');

        const priceEl = d.getElementById('current-price');
        console.log('Price element:', priceEl ? 'FOUND' : 'NOT FOUND');
        if (!priceEl) {
            console.warn('Current price element not found');
            return;
        }

        const todayChartElement = d.querySelector('#todayChart canvas[id*="chart_"]');
        console.log('Today chart element:', todayChartElement ? 'FOUND' : 'NOT FOUND');
        if (!todayChartElement) {
            console.warn('Today chart element not found');
            priceEl.textContent = '-- c/kWh';
            priceEl.style.color = '#4a9eff';
            return;
        }

        console.log('Chart _validData:', todayChartElement._validData ? 'EXISTS' : 'MISSING');
        if (!todayChartElement._validData) {
            console.warn('Valid data not available yet');
            priceEl.textContent = '-- c/kWh';
            priceEl.style.color = '#4a9eff';
            return;
        }

        // Get current time
        const now = new Date();
        const currentHour = now.getHours();
        const currentMinutes = now.getMinutes();

        // Determine which data point corresponds to the current time
        const granularity = todayChartElement._granularity || 'hour';
        let currentTimeStr;

        if (granularity === 'quarter_hour') {
            // For 15-minute data, find the current 15-minute interval
            const quarter = Math.floor(currentMinutes / 15);
            const intervalIndex = currentHour * 4 + quarter;
            currentTimeStr = intervalIndex.toString();
        } else {
            // For hourly data, use hour only
            currentTimeStr = currentHour.toString();
        }

        console.log('Looking for time:', currentTimeStr, 'granularity:', granularity);

        // Find the data row for current time
        const currentData = todayChartElement._validData.find(row => row[0] === currentTimeStr);

        if (!currentData) {
            console.warn('No data found for current time:', currentTimeStr);
            console.log('Available times:', todayChartElement._validData.map(r => r[0]).slice(0, 5));
            priceEl.textContent = '-- c/kWh';
            priceEl.style.color = '#4a9eff';
            return;
        }

        if (currentData.length < 5) {
            console.warn('Data row too short:', currentData.length);
            priceEl.textContent = '-- c/kWh';
            priceEl.style.color = '#4a9eff';
            return;
        }

        // Calculate spot price (low + medium + high, excluding margin)
        const lowPrice = parseFloat(currentData[1]) || 0;
        const mediumPrice = parseFloat(currentData[2]) || 0;
        const highPrice = parseFloat(currentData[3]) || 0;
        const marginPrice = parseFloat(currentData[4]) || 0;

        const spotPrice = lowPrice + mediumPrice + highPrice;
        const totalPrice = spotPrice + marginPrice;

        console.log('Current prices - Low:', lowPrice, 'Med:', mediumPrice, 'High:', highPrice, 'Margin:', marginPrice, 'Spot:', spotPrice, 'Total:', totalPrice);

        // Color code based on spot price tier (same as bar coloring)
        let color;
        if (spotPrice < 5) {
            color = '#2ecc71'; // Green
        } else if (spotPrice < 15) {
            color = '#f1c40f'; // Yellow
        } else {
            color = '#e74c3c'; // Red
        }

        // Display spot price without margin
        priceEl.textContent = spotPrice.toFixed(2) + ' c/kWh';
        priceEl.style.color = color;
        console.log('Price updated successfully:', spotPrice.toFixed(2), 'c/kWh (spot price, no margin), color:', color);
    };

    // Set up periodic updates for current price (every minute)
    setInterval(() => {
        if (!document.hidden) {
            window.updateCurrentPrice();
        }
    }, 60000); // Update every minute

    // Initial update
    setTimeout(() => {
        window.updateCurrentPrice();
    }, 1000); // Delay to allow charts to load

    // Additional aggressive fallback: check and update price every 15 seconds
    // This ensures the price updates even if the template calls don't execute
    setInterval(() => {
        console.log('[FALLBACK CHECK] Running at', new Date().toLocaleTimeString());
        if (!document.hidden) {
            const priceEl = d.getElementById('current-price');
            const todayChartElement = d.querySelector('#todayChart [id*="googleChart"]');
            console.log('[FALLBACK CHECK] Price element:', priceEl ? 'EXISTS' : 'MISSING', 'Chart element:', todayChartElement ? 'EXISTS' : 'MISSING', 'Has _validData:', todayChartElement && todayChartElement._validData ? 'YES' : 'NO', 'Text:', priceEl ? priceEl.textContent : 'N/A');
            if (priceEl && todayChartElement && todayChartElement._validData && priceEl.textContent === '-- c/kWh') {
                console.log('[FALLBACK] Conditions met! Calling updateCurrentPrice()');
                try {
                    window.updateCurrentPrice();
                } catch (e) {
                    console.error('[FALLBACK] Error calling updateCurrentPrice:', e);
                }
            } else {
                console.log('[FALLBACK] Conditions NOT met - price already updated or missing data');
            }
        } else {
            console.log('[FALLBACK CHECK] Document is hidden, skipping');
        }
    }, 15000); // Check every 15 seconds

    // Fullscreen toggle functionality
    const fullscreenBtn = d.getElementById('fullscreen-btn');
    const fullscreenIcon = d.getElementById('fullscreen-icon');
    
    // SVG paths for fullscreen and exit fullscreen icons
    const fullscreenSVG = '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"></path>';
    const exitFullscreenSVG = '<path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"></path>';
    
    function updateFullscreenIcon(isFullscreen) {
        if (fullscreenIcon) {
            fullscreenIcon.innerHTML = isFullscreen ? exitFullscreenSVG : fullscreenSVG;
        }
    }
    
    function toggleFullscreen() {
        if (!document.fullscreenElement && !document.webkitFullscreenElement && !document.mozFullScreenElement && !document.msFullscreenElement) {
            // Enter fullscreen
            const elem = document.documentElement;
            if (elem.requestFullscreen) {
                elem.requestFullscreen();
            } else if (elem.webkitRequestFullscreen) {
                elem.webkitRequestFullscreen();
            } else if (elem.mozRequestFullScreen) {
                elem.mozRequestFullScreen();
            } else if (elem.msRequestFullscreen) {
                elem.msRequestFullscreen();
            }
        } else {
            // Exit fullscreen
            if (document.exitFullscreen) {
                document.exitFullscreen();
            } else if (document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            } else if (document.mozCancelFullScreen) {
                document.mozCancelFullScreen();
            } else if (document.msExitFullscreen) {
                document.msExitFullscreen();
            }
        }
    }
    
    // Listen for fullscreen changes to update icon
    document.addEventListener('fullscreenchange', () => {
        updateFullscreenIcon(!!document.fullscreenElement);
    });
    document.addEventListener('webkitfullscreenchange', () => {
        updateFullscreenIcon(!!document.webkitFullscreenElement);
    });
    document.addEventListener('mozfullscreenchange', () => {
        updateFullscreenIcon(!!document.mozFullScreenElement);
    });
    document.addEventListener('MSFullscreenChange', () => {
        updateFullscreenIcon(!!document.msFullscreenElement);
    });
    
    // Initial icon state
    updateFullscreenIcon(false);
    
    // Button click handler
    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', toggleFullscreen);
    }
})();
