/**
 * Capture Statistics Charts
 * ECharts-based time series charts for capture performance metrics.
 * Auto-initializes from data attributes after HTMX swaps.
 */

'use strict';

window.CaptureStats = window.CaptureStats || {};

/**
 * Initialize a time series chart.
 * @param {HTMLElement} el - Chart container element
 * @param {Array} data - Array of {timestamp, value, cameras} or [timestamp, value] pairs
 * @param {Object} options - Chart options
 */
CaptureStats.initChart = function(el, data, options) {
    options = options || {};
    if (!el || !data || data.length === 0) {
        return;
    }

    // Dispose existing chart
    var existing = echarts.getInstanceByDom(el);
    if (existing) existing.dispose();

    var chart = echarts.init(el, null, { renderer: 'canvas' });

    // Store original data for tooltip camera breakdown
    var originalData = data;

    // Normalize data to [timestamp, value] pairs for chart
    var chartData = data.map(function(item) {
        if (Array.isArray(item)) {
            return [item[0], item[1]];
        }
        return [item.timestamp, item.value];
    });

    // Color configuration
    var primaryColor = options.color || 'rgb(34, 197, 94)';
    var rgbMatch = primaryColor.match(/\d+/g);
    var areaColor = rgbMatch ? 'rgba(' + rgbMatch.join(',') + ', 0.15)' : 'rgba(34, 197, 94, 0.15)';

    // Secondary series if provided
    var series = [{
        name: options.label || 'Value',
        type: 'line',
        data: chartData,
        smooth: 0.3,
        symbol: 'none',
        lineStyle: { width: 2, color: primaryColor },
        areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: areaColor },
                { offset: 1, color: 'rgba(255,255,255,0)' }
            ])
        }
    }];

    // Store secondary original data
    var originalData2 = null;

    // Add secondary data if provided
    if (options.data2 && options.data2.length > 0) {
        originalData2 = options.data2;
        var data2 = options.data2.map(function(item) {
            if (Array.isArray(item)) return [item[0], item[1]];
            return [item.timestamp, item.value];
        });
        var color2 = options.color2 || 'rgb(59, 130, 246)';
        series.push({
            name: options.label2 || 'Value 2',
            type: 'line',
            data: data2,
            smooth: 0.3,
            symbol: 'none',
            yAxisIndex: options.separateAxis ? 1 : 0,
            lineStyle: { width: 2, color: color2 }
        });
    }

    // Build yAxis configuration
    var yAxis = [{
        type: 'value',
        splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.1)' } },
        axisLine: { show: false },
        axisLabel: {
            color: '#94a3b8',
            fontSize: 11,
            formatter: options.yFormatter || '{value}'
        }
    }];

    // Add secondary Y axis if needed
    if (options.separateAxis && options.data2) {
        yAxis.push({
            type: 'value',
            position: 'right',
            splitLine: { show: false },
            axisLine: { show: false },
            axisLabel: {
                color: options.color2 || 'rgb(59, 130, 246)',
                fontSize: 11,
                formatter: options.y2Formatter || '{value}'
            }
        });
    }

    // Build tooltip formatter with camera breakdown
    var tooltipFormatter = options.tooltipFormatter || function(params) {
        if (!params || params.length === 0) return '';
        var ts = params[0].value[0];
        var date = new Date(ts);
        var timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        var dateStr = date.toLocaleDateString([], { month: 'short', day: 'numeric' });

        var html = '<div style="font-weight: 500; margin-bottom: 6px; color: #94a3b8;">' + dateStr + ' ' + timeStr + '</div>';

        params.forEach(function(p, idx) {
            var val = p.value[1];
            var unit = options.unit || '';
            if (idx === 1 && options.unit2) unit = options.unit2;
            var formattedVal = val !== null && val !== undefined ? val.toFixed(1) + unit : 'N/A';

            html += '<div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">';
            html += '<span style="width: 8px; height: 8px; border-radius: 50%; background: ' + p.color + ';"></span>';
            html += '<span style="color: #f1f5f9; font-weight: 500;">' + p.seriesName + ': ' + formattedVal + '</span>';
            html += '</div>';

            // Find matching data point for camera breakdown
            var dataSource = idx === 0 ? originalData : originalData2;
            if (dataSource && options.showCameraBreakdown !== false) {
                var dataPoint = dataSource.find(function(d) {
                    var dts = Array.isArray(d) ? d[0] : d.timestamp;
                    return dts === ts;
                });

                if (dataPoint && dataPoint.cameras && Object.keys(dataPoint.cameras).length > 0) {
                    var cameras = Object.entries(dataPoint.cameras).sort(function(a, b) {
                        return b[1] - a[1]; // Sort by value descending
                    });

                    // Show up to 6 cameras
                    var shown = cameras.slice(0, 6);
                    html += '<div style="margin-left: 14px; font-size: 11px; color: #94a3b8;">';
                    shown.forEach(function(cam) {
                        var camName = cam[0].replace(/_/g, ' ');
                        var camVal = cam[1] !== null && cam[1] !== undefined ? cam[1].toFixed(1) + unit : 'N/A';
                        html += '<div>' + camName + ': ' + camVal + '</div>';
                    });
                    if (cameras.length > 6) {
                        html += '<div style="color: #64748b;">+' + (cameras.length - 6) + ' more</div>';
                    }
                    html += '</div>';
                }
            }
        });
        return html;
    };

    chart.setOption({
        animation: true,
        animationDuration: 600,
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(15, 23, 42, 0.95)',
            borderColor: 'rgba(255, 255, 255, 0.1)',
            borderWidth: 1,
            padding: [10, 14],
            textStyle: { color: '#e2e8f0', fontSize: 12 },
            axisPointer: {
                type: 'line',
                lineStyle: { color: 'rgba(255, 255, 255, 0.2)', width: 1, type: 'dashed' }
            },
            formatter: tooltipFormatter
        },
        legend: options.showLegend ? {
            bottom: 0,
            textStyle: { color: '#94a3b8', fontSize: 11 },
            itemWidth: 12,
            itemHeight: 8
        } : { show: false },
        grid: {
            left: 50,
            right: options.separateAxis ? 50 : 20,
            top: 20,
            bottom: options.showLegend ? 40 : 20
        },
        xAxis: {
            type: 'time',
            splitLine: { show: false },
            axisLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.2)' } },
            axisLabel: {
                color: '#94a3b8',
                fontSize: 11,
                formatter: function(value) {
                    var date = new Date(value);
                    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                }
            }
        },
        yAxis: yAxis,
        series: series
    });

    // Resize handling
    var resizeTimeout;
    var ro = new ResizeObserver(function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(function() {
            chart.resize();
        }, 150);
    });
    ro.observe(el);

    // Cleanup on HTMX swap
    el.addEventListener('htmx:beforeSwap', function() {
        ro.disconnect();
        chart.dispose();
    }, { once: true });

    el._echartsInstance = chart;
    return chart;
};

/**
 * Auto-initialize charts from data attributes.
 * Usage: <div data-capture-chart='[...]' data-chart-color="rgb(34, 197, 94)"></div>
 */
CaptureStats.initAllCharts = function(root) {
    root = root || document;

    // Check if echarts is loaded
    if (typeof echarts === 'undefined') {
        console.warn('ECharts not loaded yet');
        return;
    }

    var elements = root.querySelectorAll('[data-capture-chart]');

    elements.forEach(function(el) {
        if (el._echartsInstance) return;

        // Wait for browser to paint the element so it has dimensions
        requestAnimationFrame(function() {
            requestAnimationFrame(function() {
                CaptureStats.initSingleChart(el);
            });
        });
    });
};

CaptureStats.initSingleChart = function(el, retryCount) {
    if (el._echartsInstance) return;
    retryCount = retryCount || 0;

    // Check if element has dimensions - if not, retry
    var rect = el.getBoundingClientRect();
    if (rect.height === 0 && retryCount < 10) {
        requestAnimationFrame(function() {
            CaptureStats.initSingleChart(el, retryCount + 1);
        });
        return;
    }

    try {
        var data = JSON.parse(el.getAttribute('data-capture-chart'));
        var options = {
            color: el.getAttribute('data-chart-color') || 'rgb(34, 197, 94)',
            label: el.getAttribute('data-chart-label') || 'Value',
            showLegend: el.hasAttribute('data-chart-legend'),
            unit: el.getAttribute('data-chart-unit') || ''
        };

        // Secondary data
        if (el.hasAttribute('data-chart-data2')) {
            options.data2 = JSON.parse(el.getAttribute('data-chart-data2'));
            options.color2 = el.getAttribute('data-chart-color2') || 'rgb(59, 130, 246)';
            options.label2 = el.getAttribute('data-chart-label2') || 'Value 2';
            options.unit2 = el.getAttribute('data-chart-unit2') || options.unit;
            options.separateAxis = el.hasAttribute('data-chart-separate-axis');
        }

        // Disable camera breakdown for simple charts (success/failure)
        if (el.hasAttribute('data-chart-no-breakdown')) {
            options.showCameraBreakdown = false;
        }

        CaptureStats.initChart(el, data, options);
    } catch (e) {
        console.warn('Failed to initialize capture chart:', e);
    }
};

// Auto-initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    CaptureStats.initAllCharts();
});

// Re-initialize after HTMX swaps
document.addEventListener('htmx:afterSettle', function(_event) {
    CaptureStats.initAllCharts(document);
});
