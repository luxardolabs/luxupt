/**
 * Core application JavaScript
 * Handles global functionality, utilities, and HTMX integration
 */

class App {
	static _errorState = { active: false, toastId: null, count: 0 };

	/**
	 * Initialize the application
	 */
	static init() {
		console.log('LuxUPT Web Interface initialized');

		// Initialize components
		Toast.init();
		this.setupHTMXEvents();
		this.setupGlobalKeyboardHandlers();
		this.processDataInitElements();

		// Auto-refresh status indicators
		this.startStatusRefresh();
	}

	/**
	 * Process elements with data-init attribute for deferred initialization
	 */
	static processDataInitElements() {
		document.querySelectorAll('[data-init]').forEach(el => {
			try {
				const initCode = el.dataset.init;
				if (initCode) {
					eval(initCode);
				}
			} catch (e) {
				console.error('Error processing data-init:', e);
			}
		});
	}

	/**
	 * Setup HTMX event handlers
	 */
	static setupHTMXEvents() {
		// Global request indicators
		document.addEventListener('htmx:beforeRequest', function(event) {
			const indicator = event.target.querySelector('.htmx-indicator');
			if (indicator) {
				indicator.style.opacity = '1';
			}
		});

		document.addEventListener('htmx:afterRequest', function(event) {
			const indicator = event.target.querySelector('.htmx-indicator');
			if (indicator) {
				indicator.style.opacity = '0';
			}

			const status = event.detail.xhr?.status;

			// Handle errors (skip 4xx since those show inline validation errors)
			if (status >= 500) {
				App._onServerError();
			} else if (status === 401) {
				Toast.show('Authentication required. Please login again.', 'error');
			} else if (status && status < 400 && App._errorState.active) {
				App._onServerRecovery();
			}
		});

		// Handle network-level failures (server completely unreachable)
		document.addEventListener('htmx:sendError', function() {
			App._onServerError();
		});

		// Handle authentication errors
		document.addEventListener('htmx:responseError', function(event) {
			if (event.detail.xhr.status === 401) {
				window.location.href = '/login';
			}
		});

		// Allow HTMX to swap content on 4xx responses (for validation errors)
		document.addEventListener('htmx:beforeSwap', function(event) {
			if (event.detail.xhr.status >= 400 && event.detail.xhr.status < 500) {
				event.detail.shouldSwap = true;
				event.detail.isError = false;
			}
		});

		// Auto-focus first input in modals (but NOT panels) and process data-init elements
		document.addEventListener('htmx:afterSettle', function(event) {
			// Skip auto-focus for panels - it causes scroll-to-bottom issues
			const isPanel = event.target.id === 'panel-container' || event.target.closest('#panel-container');
			if (!isPanel) {
				const firstInput = event.target.querySelector('input[type="text"], input[type="email"], select, textarea');
				if (firstInput && firstInput.offsetParent !== null) {
					firstInput.focus();
				}
			}

			// Process any data-init elements in the swapped content
			event.target.querySelectorAll('[data-init]').forEach(el => {
				try {
					const initCode = el.dataset.init;
					if (initCode) {
						eval(initCode);
					}
				} catch (e) {
					console.error('Error processing data-init after HTMX swap:', e);
				}
			});
		});
	}

	/**
	 * Setup global keyboard handlers
	 */
	static setupGlobalKeyboardHandlers() {
		document.addEventListener('keydown', function(event) {
			// Lightbox navigation (HTMX-based)
			if (Lightbox.isOpen()) {
				if (event.key === 'Escape') {
					event.preventDefault();
					Lightbox.close();
				} else if (event.key === 'ArrowLeft') {
					event.preventDefault();
					Lightbox.previous();
				} else if (event.key === 'ArrowRight') {
					event.preventDefault();
					Lightbox.next();
				}
				return;
			}

			// ESC to close panels
			if (event.key === 'Escape') {
				if (Panel.isOpen()) {
					event.preventDefault();
					Panel.close();
					return;
				}
				const openModal = document.querySelector('.modal.show');
				if (openModal && openModal.id === 'confirm-modal') {
					ConfirmModal.close();
				}
			}
		});
	}

	/**
	 * Handle a server error — coalesce into one persistent toast
	 */
	static _onServerError() {
		const state = this._errorState;
		if (state.active) {
			state.count++;
			Toast.update(state.toastId, `Server unavailable (${state.count} errors suppressed)`);
		} else {
			state.active = true;
			state.count = 0;
			state.toastId = Toast.show('Server unavailable', 'error', 0);
		}
	}

	/**
	 * Handle recovery from server errors
	 */
	static _onServerRecovery() {
		const state = this._errorState;
		if (state.toastId) {
			Toast.remove(state.toastId);
		}
		state.active = false;
		state.toastId = null;
		state.count = 0;
		Toast.show('Reconnected', 'success', 3000);
	}

	/**
	 * Start automatic status refresh
	 */
	static startStatusRefresh() {
		// Refresh service status every 30 seconds
		setInterval(() => {
			const statusElement = document.getElementById('service-status');
			if (statusElement && document.visibilityState === 'visible') {
				htmx.trigger(statusElement, 'refresh');
			}
		}, 30000);

		// Refresh job status every 3 seconds when jobs are active
		setInterval(() => {
			const jobsElement = document.getElementById('active-jobs');
			if (jobsElement && document.visibilityState === 'visible') {
				htmx.trigger(jobsElement, 'refresh');
			}
		}, 3000);
	}

	/**
	 * Format file size in human readable format
	 */
	static formatFileSize(bytes) {
		const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
		if (bytes === 0) return '0 B';
		const i = Math.floor(Math.log(bytes) / Math.log(1024));
		return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
	}

	/**
	 * Format duration in human readable format
	 */
	static formatDuration(seconds) {
		const hours = Math.floor(seconds / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		const secs = Math.floor(seconds % 60);

		if (hours > 0) {
			return `${hours}h ${minutes}m ${secs}s`;
		} else if (minutes > 0) {
			return `${minutes}m ${secs}s`;
		} else {
			return `${secs}s`;
		}
	}

	/**
	 * Format timestamp to readable date/time
	 */
	static formatTimestamp(timestamp) {
		const date = new Date(timestamp * 1000);
		return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
	}

	/**
	 * Debounce function calls
	 */
	static debounce(func, wait) {
		let timeout;
		return function executedFunction(...args) {
			const later = () => {
				clearTimeout(timeout);
				func(...args);
			};
			clearTimeout(timeout);
			timeout = setTimeout(later, wait);
		};
	}

	/**
	 * Copy text to clipboard
	 */
	static async copyToClipboard(text) {
		try {
			await navigator.clipboard.writeText(text);
			Toast.show('Copied to clipboard', 'success');
		} catch (err) {
			console.error('Failed to copy text: ', err);
			Toast.show('Failed to copy to clipboard', 'error');
		}
	}
}

/**
 * Toast notification system
 */
class Toast {
	static container = null;
	static toasts = new Map();

	static init() {
		this.container = document.getElementById('toast-container');
		if (!this.container) {
			console.warn('Toast container not found');
		}
	}

	static show(message, type = 'info', duration = 5000) {
		if (!this.container) {
			console.warn('Toast container not initialized');
			return;
		}

		const id = Date.now().toString();
		const toast = this.createToast(id, message, type);

		this.container.appendChild(toast);
		this.toasts.set(id, toast);

		// Animate in
		requestAnimationFrame(() => {
			toast.style.opacity = '1';
			toast.style.transform = 'translateX(0)';
		});

		// Auto remove
		if (duration > 0) {
			setTimeout(() => this.remove(id), duration);
		}

		return id;
	}

	static createToast(id, message, type) {
		const toast = document.createElement('div');
		toast.className = `toast toast-${type}`;
		toast.style.opacity = '0';
		toast.style.transform = 'translateX(100%)';
		toast.style.transition = 'all 0.3s ease-out';

		// Inline SVGs — no external icon requests needed
		const inlineSvgs = {
			error: '<svg fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" clip-rule="evenodd" d="M12 2.25C6.61522 2.25 2.25 6.61522 2.25 12C2.25 17.3848 6.61522 21.75 12 21.75C17.3848 21.75 21.75 17.3848 21.75 12C21.75 6.61522 17.3848 2.25 12 2.25ZM10.2803 9.21967C9.98744 8.92678 9.51256 8.92678 9.21967 9.21967C8.92678 9.51256 8.92678 9.98744 9.21967 10.2803L10.9393 12L9.21967 13.7197C8.92678 14.0126 8.92678 14.4874 9.21967 14.7803C9.51256 15.0732 9.98744 15.0732 10.2803 14.7803L12 13.0607L13.7197 14.7803C14.0126 15.0732 14.4874 15.0732 14.7803 14.7803C15.0732 14.4874 15.0732 14.0126 14.7803 13.7197L13.0607 12L14.7803 10.2803C15.0732 9.98744 15.0732 9.51256 14.7803 9.21967C14.4874 8.92678 14.0126 8.92678 13.7197 9.21967L12 10.9393L10.2803 9.21967Z" fill="currentColor"/></svg>',
			success: '<svg fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" clip-rule="evenodd" d="M2.25 12C2.25 6.61522 6.61522 2.25 12 2.25C17.3848 2.25 21.75 6.61522 21.75 12C21.75 17.3848 17.3848 21.75 12 21.75C6.61522 21.75 2.25 17.3848 2.25 12ZM15.6103 10.1859C15.8511 9.84887 15.773 9.38046 15.4359 9.1397C15.0989 8.89894 14.6305 8.97701 14.3897 9.31407L11.1543 13.8436L9.53033 12.2197C9.23744 11.9268 8.76256 11.9268 8.46967 12.2197C8.17678 12.5126 8.17678 12.9874 8.46967 13.2803L10.7197 15.5303C10.8756 15.6862 11.0921 15.7656 11.3119 15.7474C11.5316 15.7293 11.7322 15.6153 11.8603 15.4359L15.6103 10.1859Z" fill="currentColor"/></svg>',
			warning: '<svg fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" clip-rule="evenodd" d="M9.40123 3.0034C10.5557 1.00229 13.4439 1.00229 14.5983 3.0034L21.9527 15.7509C23.1065 17.7509 21.6631 20.2501 19.3541 20.2501H4.64546C2.33649 20.2501 0.893061 17.7509 2.04691 15.7509L9.40123 3.0034ZM12 8.25C12.4142 8.25 12.75 8.58579 12.75 9V12.75C12.75 13.1642 12.4142 13.5 12 13.5C11.5858 13.5 11.25 13.1642 11.25 12.75V9C11.25 8.58579 11.5858 8.25 12 8.25ZM12 16.5C12.4142 16.5 12.75 16.1642 12.75 15.75C12.75 15.3358 12.4142 15 12 15C11.5858 15 11.25 15.3358 11.25 15.75C11.25 16.1642 11.5858 16.5 12 16.5Z" fill="currentColor"/></svg>',
			info: '<svg fill="currentColor" viewBox="0 0 24 24"><path fill-rule="evenodd" clip-rule="evenodd" d="M2.25 12C2.25 6.61522 6.61522 2.25 12 2.25C17.3848 2.25 21.75 6.61522 21.75 12C21.75 17.3848 17.3848 21.75 12 21.75C6.61522 21.75 2.25 17.3848 2.25 12ZM10.9562 10.5584C12.1025 9.98533 13.3931 11.0206 13.0823 12.2639L12.3733 15.0999L12.4148 15.0792C12.7852 14.894 13.2357 15.0441 13.421 15.4146C13.6062 15.7851 13.4561 16.2356 13.0856 16.4208L13.0441 16.4416C11.8979 17.0147 10.6072 15.9794 10.9181 14.7361L11.6271 11.9001L11.5856 11.9208C11.2151 12.1061 10.7646 11.9559 10.5793 11.5854C10.3941 11.2149 10.5443 10.7644 10.9148 10.5792L10.9562 10.5584ZM12 9C12.4142 9 12.75 8.66421 12.75 8.25C12.75 7.83579 12.4142 7.5 12 7.5C11.5858 7.5 11.25 7.83579 11.25 8.25C11.25 8.66421 11.5858 9 12 9Z" fill="currentColor"/></svg>'
		};

		const iconColors = {
			success: 'text-green-400',
			error: 'text-red-400',
			warning: 'text-yellow-400',
			info: 'text-blue-400'
		};

		const iconHtml = inlineSvgs[type] || inlineSvgs.info;

		toast.innerHTML = `
            <div class="toast-content">
                <div class="toast-icon w-5 h-5 ${iconColors[type] || iconColors.info}">${iconHtml}</div>
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close" onclick="Toast.remove('${id}')">&times;</button>
        `;

		return toast;
	}

	static update(id, message) {
		const toast = this.toasts.get(id);
		if (!toast) return;
		const msgEl = toast.querySelector('.toast-message');
		if (msgEl) {
			msgEl.textContent = message;
		}
	}

	static remove(id) {
		const toast = this.toasts.get(id);
		if (!toast) return;

		toast.style.opacity = '0';
		toast.style.transform = 'translateX(100%)';

		setTimeout(() => {
			if (toast.parentNode) {
				toast.parentNode.removeChild(toast);
			}
			this.toasts.delete(id);
		}, 300);
	}

	static clear() {
		for (const [id] of this.toasts) {
			this.remove(id);
		}
	}
}

/**
 * HTMX-based Lightbox for image viewing
 */
class Lightbox {
	/**
	 * Close the lightbox
	 */
	static close() {
		const container = document.getElementById('lightbox-container');
		if (container) {
			container.innerHTML = '';
		}
	}

	/**
	 * Navigate to previous image (swaps content only, not whole modal)
	 */
	static previous() {
		const navData = document.getElementById('lightbox-nav-data');
		if (navData && navData.dataset.prev) {
			htmx.ajax('GET', navData.dataset.prev, {target: '#lightbox-content', swap: 'morph:innerHTML'});
		}
	}

	/**
	 * Navigate to next image (swaps content only, not whole modal)
	 */
	static next() {
		const navData = document.getElementById('lightbox-nav-data');
		if (navData && navData.dataset.next) {
			htmx.ajax('GET', navData.dataset.next, {target: '#lightbox-content', swap: 'morph:innerHTML'});
		}
	}

	/**
	 * Check if lightbox is open
	 */
	static isOpen() {
		return document.getElementById('lightbox-backdrop') !== null;
	}
}

/**
 * Panel slide-out utility
 */
class Panel {
	/**
	 * Close the panel
	 */
	static close() {
		const container = document.getElementById('panel-container');
		if (container) {
			container.innerHTML = '';
		}
	}

	/**
	 * Check if a panel is open
	 */
	static isOpen() {
		return document.getElementById('panel-backdrop') !== null;
	}
}

/**
 * Confirmation modal utility
 */
class ConfirmModal {
	static show(title, message, onConfirm, confirmText = 'Confirm') {
		const modal = document.getElementById('confirm-modal');
		const titleEl = document.getElementById('confirm-modal-title');
		const messageEl = document.getElementById('confirm-modal-message');
		const actionBtn = document.getElementById('confirm-modal-action');

		if (!modal || !titleEl || !messageEl || !actionBtn) {
			console.error('Confirm modal elements not found');
			return;
		}

		titleEl.textContent = title;
		messageEl.textContent = message;
		actionBtn.textContent = confirmText;

		// Remove existing event listeners
		const newActionBtn = actionBtn.cloneNode(true);
		actionBtn.parentNode.replaceChild(newActionBtn, actionBtn);

		// Add new event listener
		newActionBtn.addEventListener('click', () => {
			onConfirm();
			this.close();
		});

		modal.classList.add('show');
	}

	static close() {
		const modal = document.getElementById('confirm-modal');
		if (modal) {
			modal.classList.remove('show');
		}
	}
}

// Initialize app when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
	App.init();
});

/**
 * Update URL from filter form values (for bookmarkable filter state)
 */
function updateUrlFromFilters(form) {
	const formData = new FormData(form);
	const params = new URLSearchParams();

	for (const [key, value] of formData.entries()) {
		if (value) {
			params.set(key, value);
		}
	}

	const newUrl = params.toString()
		? `${window.location.pathname}?${params.toString()}`
		: window.location.pathname;

	history.pushState({}, '', newUrl);
}

/**
 * Form utilities for dynamic UI elements
 */
class FormUtils {
	/**
	 * Toggle all checkboxes in a container
	 */
	static toggleAllCheckboxes(containerId, checked) {
		const container = document.getElementById(containerId);
		if (container) {
			const checkboxes = container.querySelectorAll('input[type="checkbox"]');
			checkboxes.forEach(cb => { cb.checked = checked; });
		}
	}

	/**
	 * Toggle all interval checkboxes (alias for camera settings)
	 */
	static toggleAllIntervals(checked) {
		this.toggleAllCheckboxes('interval-checkboxes', checked);
	}

	/**
	 * Add a new interval row to fetch settings
	 */
	static addInterval() {
		const container = document.getElementById('intervals-container');
		if (!container) return;

		const row = document.createElement('div');
		row.className = 'interval-row flex items-center gap-1 bg-surface-700/50 rounded-lg px-2 py-1';
		row.innerHTML = `
			<input type="number" name="intervals" value="60" min="5" max="3600"
				   class="w-16 rounded border-0 bg-transparent px-1 py-0.5 text-sm text-white text-center focus:ring-1 focus:ring-primary-500">
			<span class="text-xs text-surface-200">sec</span>
			<button type="button" onclick="FormUtils.removeInterval(this)"
					class="p-0.5 text-surface-200 hover:text-danger-400 transition-colors">
				<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
				</svg>
			</button>
		`;
		container.appendChild(row);
	}

	/**
	 * Remove an interval row from fetch settings
	 */
	static removeInterval(btn) {
		const container = document.getElementById('intervals-container');
		if (container && container.querySelectorAll('.interval-row').length > 1) {
			btn.closest('.interval-row').remove();
		}
	}
}

/**
 * Capture statistics panel utilities
 */
class CaptureStats {
	/**
	 * Update filters and refresh charts
	 */
	static updateFilters() {
		const camera = document.getElementById('stats-camera-filter')?.value || '';
		const interval = document.getElementById('stats-interval-filter')?.value || '';
		const period = document.getElementById('stats-period-filter')?.value || '';

		const params = new URLSearchParams();
		if (camera) params.set('camera', camera);
		if (interval) params.set('interval', interval);
		if (period) params.set('period', period);
		params.set('offset', '0');  // Always reset to current when changing filters

		const url = '/cameras/capture-stats/charts?' + params.toString();
		htmx.ajax('GET', url, {target: '#capture-stats-content', swap: 'innerHTML'});
	}
}

/**
 * Timelapse creator utilities
 */
class TimelapseCreator {
	/**
	 * Initialize the timelapse creator panel
	 */
	static init() {
		// Load timelapse options
		htmx.ajax('GET', '/api/timelapses/options', {
			target: '#create_date',
			swap: 'innerHTML'
		});

		// Check deletion configuration
		fetch('/api/config/deletion-enabled')
			.then(response => response.json())
			.then(data => {
				if (data.deletion_enabled) {
					this.showDeletionWarning();
				}
			})
			.catch(error => {
				console.error('Error checking deletion config:', error);
			});
	}

	/**
	 * Show deletion warning and update UI
	 */
	static showDeletionWarning() {
		const warningDiv = document.getElementById('config-warning');
		const createButton = document.getElementById('create-button');
		const overrideCheckbox = document.getElementById('override-deletion');
		const spinnerHtml = '<span id="create-spinner" class="htmx-indicator"><img src="/static/icons/spinner.svg" class="w-5 h-5 animate-spin" alt=""></span>';

		if (!warningDiv || !createButton) return;

		// Show warning
		warningDiv.style.display = 'block';

		// Update button
		createButton.innerHTML = spinnerHtml + ' Create Timelapse (Images will be deleted!)';
		createButton.classList.remove('btn-primary');
		createButton.classList.add('btn-warning');

		// Handle checkbox changes
		if (overrideCheckbox) {
			overrideCheckbox.addEventListener('change', function() {
				if (this.checked) {
					createButton.innerHTML = spinnerHtml + ' Create Timelapse (Images will be kept)';
					createButton.classList.remove('btn-warning');
					createButton.classList.add('btn-primary');
				} else {
					createButton.innerHTML = spinnerHtml + ' Create Timelapse (Images will be deleted!)';
					createButton.classList.remove('btn-primary');
					createButton.classList.add('btn-warning');
				}
			});
		}
	}
}

/**
 * Image filters utilities
 */
class ImageFilters {
	/**
	 * Initialize image filter dropdowns
	 */
	static init() {
		// Populate dates
		fetch('/api/filters/dates')
			.then(response => response.text())
			.then(options => {
				const el = document.getElementById('date');
				if (el) el.innerHTML = options;
			})
			.catch(error => console.error('Error loading dates:', error));

		// Populate cameras
		fetch('/api/filters/cameras')
			.then(response => response.text())
			.then(options => {
				const el = document.getElementById('camera');
				if (el) el.innerHTML = options;
			})
			.catch(error => console.error('Error loading cameras:', error));

		// Populate intervals
		fetch('/api/filters/intervals')
			.then(response => response.text())
			.then(options => {
				const el = document.getElementById('interval');
				if (el) el.innerHTML = options;
			})
			.catch(error => console.error('Error loading intervals:', error));
	}

	/**
	 * Reset all filter fields to defaults
	 */
	static reset() {
		const form = document.querySelector('.image-filters');
		if (!form) return;

		const dateSelect = form.querySelector('select[name="date"]');
		const cameraSelect = form.querySelector('select[name="camera"]');
		const intervalSelect = form.querySelector('select[name="interval"]');
		const timeRangeSelect = form.querySelector('select[name="time_range"]');
		const perPageSelect = form.querySelector('select[name="per_page"]');
		const sortSelect = form.querySelector('select[name="sort"]');
		const pageInput = form.querySelector('input[name="page"]');

		if (dateSelect) dateSelect.value = '';
		if (cameraSelect) cameraSelect.value = '';
		if (intervalSelect) intervalSelect.value = '';
		if (timeRangeSelect) timeRangeSelect.value = '';
		if (perPageSelect) perPageSelect.value = '100';
		if (sortSelect) sortSelect.value = 'newest';
		if (pageInput) pageInput.value = '1';

		// Trigger form submission to refresh results
		form.dispatchEvent(new Event('submit'));
	}
}

/**
 * Panel result utilities for close/reload behavior
 */
class PanelResult {
	/**
	 * Close panel and reload page after delay
	 */
	static closeAndReload(delay = 1500) {
		setTimeout(() => {
			Panel.close();
			window.location.reload();
		}, delay);
	}

	/**
	 * Reload page after delay (for detection results)
	 */
	static reloadAfterDelay(delay = 2000) {
		setTimeout(() => {
			window.location.reload();
		}, delay);
	}
}

// Global utilities
window.App = App;
window.Toast = Toast;
window.Lightbox = Lightbox;
window.Panel = Panel;
window.ConfirmModal = ConfirmModal;
window.updateUrlFromFilters = updateUrlFromFilters;
window.FormUtils = FormUtils;
window.CaptureStats = CaptureStats;
window.TimelapseCreator = TimelapseCreator;
window.ImageFilters = ImageFilters;
window.PanelResult = PanelResult;