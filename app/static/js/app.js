/**
 * app/static/js/app.js
 * --------------------
 * Shared utilities loaded on every page.
 * Page-specific logic lives inline in each template's {% block scripts %}.
 */

/**
 * Show a toast-style notification at the bottom of the screen.
 * @param {string} message
 * @param {'info'|'success'|'error'|'warning'} type
 * @param {number} duration  ms before auto-dismiss (0 = no auto-dismiss)
 */
function notify(message, type = 'info', duration = 3000) {
  const el = document.createElement('div');
  el.className = `alert alert-${type}`;
  el.style.cssText = `
    position:fixed; bottom:1rem; right:1rem;
    max-width:400px; z-index:9999;
    animation: fadeIn 0.2s ease;
  `;
  el.textContent = message;
  document.body.appendChild(el);
  if (duration > 0) {
    setTimeout(() => el.remove(), duration);
  }
  return el;
}

/**
 * Format a duration in seconds into a human-readable string.
 * e.g. 3723 -> "1h 02m 03s"
 */
function formatDuration(seconds) {
  if (!seconds) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${String(m).padStart(2,'0')}m`;
  return `${m}m ${String(s).padStart(2,'0')}s`;
}

/**
 * Format a file size in bytes to a human-readable string.
 */
function formatSize(bytes) {
  if (!bytes) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes/1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes/1048576).toFixed(1) + ' MB';
  return (bytes/1073741824).toFixed(2) + ' GB';
}
