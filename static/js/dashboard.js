/**
 * YouTube AI Autopilot Dashboard - Core Frontend JavaScript
 * Handles Dark/Light Mode, SocketIO telemetry, AJAX control actions,
 * interactive CSS charts, mobile sidebar navigation, toast notifications,
 * and periodic data refreshes.
 */

(function () {
  'use strict';

  // Global State & Configuration
  const APP_CONFIG = {
    themeStorageKey: 'yt_autopilot_theme',
    autoRefreshIntervalMs: 60000,
    socketEndpoint: window.location.origin,
  };

  /**
   * Safe CSRF Token Extractor from Meta Tag or DOM
   */
  function getCsrfToken() {
    const metaTag = document.querySelector('meta[name="csrf-token"]');
    if (metaTag && metaTag.content) {
      return metaTag.content;
    }
    const inputField = document.querySelector('input[name="csrf_token"]');
    if (inputField && inputField.value) {
      return inputField.value;
    }
    return '';
  }

  /**
   * Theme Manager (Dark / Light Mode)
   */
  const ThemeManager = {
    init: function () {
      const savedTheme = localStorage.getItem(APP_CONFIG.themeStorageKey) || 'light';
      this.applyTheme(savedTheme);

      const toggleBtn = document.getElementById('themeToggleBtn');
      if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
          const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
          const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
          this.applyTheme(nextTheme);
        });
      }
    },

    applyTheme: function (theme) {
      document.documentElement.setAttribute('data-theme', theme);
      localStorage.setItem(APP_CONFIG.themeStorageKey, theme);

      const toggleBtn = document.getElementById('themeToggleBtn');
      if (toggleBtn) {
        toggleBtn.innerHTML = theme === 'dark' ? '☀️' : '🌙';
        toggleBtn.setAttribute('title', theme === 'dark' ? 'التبديل إلى الوضع الفاتح' : 'التبديل إلى الوضع الليلي');
      }
    },
  };

  /**
   * Toast Notification Manager
   */
  const ToastManager = {
    show: function (message, type = 'info', duration = 4500) {
      let container = document.getElementById('toastContainer');
      if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container';
        document.body.appendChild(container);
      }

      const toast = document.createElement('div');
      toast.className = `toast ${type}`;

      let icon = 'ℹ️';
      if (type === 'success') icon = '✅';
      if (type === 'danger') icon = '⚠️';
      if (type === 'warning') icon = '🔔';

      toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
      container.appendChild(toast);

      setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
      }, duration);
    },
  };

  /**
   * Mobile Sidebar Navigation Toggle
   */
  const NavigationManager = {
    init: function () {
      const hamburgerBtn = document.getElementById('hamburgerBtn');
      const sidebar = document.getElementById('sidebar');

      if (hamburgerBtn && sidebar) {
        hamburgerBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          sidebar.classList.toggle('open');
        });

        document.addEventListener('click', (e) => {
          if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== hamburgerBtn) {
            sidebar.classList.remove('open');
          }
        });
      }
    },
  };

  /**
   * Real-Time Telemetry & WebSocket Connection
   */
  const SocketManager = {
    socket: null,

    init: function () {
      if (typeof io === 'undefined') {
        console.warn('Socket.IO client library not loaded. Falling back to HTTP polling.');
        return;
      }

      try {
        this.socket = io(APP_CONFIG.socketEndpoint, {
          transports: ['websocket', 'polling'],
          reconnectionAttempts: 5,
          timeout: 10000,
        });

        this.bindEvents();
      } catch (err) {
        console.error('Failed to initialize Socket.IO:', err);
      }
    },

    bindEvents: function () {
      const badge = document.getElementById('connectionStatusBadge');

      this.socket.on('connect', () => {
        if (badge) {
          badge.className = 'status-badge';
          badge.innerHTML = '<span class="status-indicator-dot"></span> متصل بالنظام السحابي';
        }
      });

      this.socket.on('disconnect', () => {
        if (badge) {
          badge.className = 'status-badge disconnected';
          badge.innerHTML = '<span class="status-indicator-dot"></span> انقطع الاتصال بالخادم';
        }
      });

      this.socket.on('system_state_update', (data) => {
        this.handleStateUpdate(data);
      });

      this.socket.on('emergency_stop_triggered', () => {
        ToastManager.show('تحذير طارئ: تم إيقاف النظام بشكل كامل من قبل الخادم!', 'danger', 7000);
        setTimeout(() => window.location.reload(), 2000);
      });
    },

    handleStateUpdate: function (data) {
      const pauseBtn = document.getElementById('btnPausePublishing');
      const resumeBtn = document.getElementById('btnResumePublishing');

      if (data.is_paused) {
        if (pauseBtn) pauseBtn.style.display = 'none';
        if (resumeBtn) resumeBtn.style.display = 'inline-flex';
      } else {
        if (pauseBtn) pauseBtn.style.display = 'inline-flex';
        if (resumeBtn) resumeBtn.style.display = 'none';
      }
    },
  };

  /**
   * Action Controls (Pause / Resume / Emergency Stop / Error Retry)
   */
  const ActionManager = {
    init: function () {
      // Emergency Stop Button
      const emergencyBtn = document.getElementById('btnEmergencyStop');
      if (emergencyBtn) {
        emergencyBtn.addEventListener('click', () => this.handleEmergencyStop());
      }

      // Pause Button
      const pauseBtn = document.getElementById('btnPausePublishing');
      if (pauseBtn) {
        pauseBtn.addEventListener('click', () => this.handlePause());
      }

      // Resume Button
      const resumeBtn = document.getElementById('btnResumePublishing');
      if (resumeBtn) {
        resumeBtn.addEventListener('click', () => this.handleResume());
      }

      // Retry Error Buttons
      document.querySelectorAll('.btn-retry-error').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          const errorId = btn.getAttribute('data-error-id');
          if (errorId) {
            this.handleRetryError(errorId, btn);
          }
        });
      });
    },

    handleEmergencyStop: function () {
      const confirmed = window.confirm(
        '⚠️ تحذير أمني خطير:\n\nهل أنت متأكد تماماً من رغبتك في تفعيل إيقاف الطوارئ؟\nسيؤدي هذا إلى تجميد كافة عمليات الرفع، والتصيير السحابي، وخط إنتاج الفيديو فوراً!'
      );

      if (!confirmed) return;

      fetch('/api/emergency-stop', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.status === 'success') {
            ToastManager.show(data.message, 'danger', 6000);
            setTimeout(() => window.location.reload(), 1500);
          } else {
            ToastManager.show('فشل في إرسال أمر الطوارئ!', 'danger');
          }
        })
        .catch((err) => {
          console.error('Emergency stop error:', err);
          ToastManager.show('حدث خطأ في الاتصال بالخادم.', 'danger');
        });
    },

    handlePause: function () {
      fetch('/api/pause', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.status === 'success') {
            ToastManager.show(data.message, 'warning');
            const pauseBtn = document.getElementById('btnPausePublishing');
            const resumeBtn = document.getElementById('btnResumePublishing');
            if (pauseBtn) pauseBtn.style.display = 'none';
            if (resumeBtn) resumeBtn.style.display = 'inline-flex';
          }
        })
        .catch((err) => {
          console.error('Pause error:', err);
          ToastManager.show('تعذر إيقاف النظام مؤقتاً.', 'danger');
        });
    },

    handleResume: function () {
      fetch('/api/resume', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.status === 'success') {
            ToastManager.show(data.message, 'success');
            const pauseBtn = document.getElementById('btnPausePublishing');
            const resumeBtn = document.getElementById('btnResumePublishing');
            if (pauseBtn) pauseBtn.style.display = 'inline-flex';
            if (resumeBtn) resumeBtn.style.display = 'none';
          }
        })
        .catch((err) => {
          console.error('Resume error:', err);
          ToastManager.show('تعذر استئناف عمليات النظام.', 'danger');
        });
    },

    handleRetryError: function (errorId, buttonEl) {
      buttonEl.disabled = true;
      buttonEl.innerText = 'جارٍ الإعادة...';

      fetch(`/api/errors/retry/${errorId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.status === 'success') {
            ToastManager.show(data.message, 'success');
            buttonEl.innerText = 'تمت الإعادة';
            buttonEl.className = 'btn btn-sm btn-secondary';
          } else {
            ToastManager.show(data.message || 'فشلت عملية إعادة المحاولة.', 'danger');
            buttonEl.disabled = false;
            buttonEl.innerText = 'إعادة المحاولة';
          }
        })
        .catch((err) => {
          console.error('Retry error:', err);
          ToastManager.show('حدث خطأ أثناء التواصل مع الخادم.', 'danger');
          buttonEl.disabled = false;
          buttonEl.innerText = 'إعادة المحاولة';
        });
    },
  };

  /**
   * CSS-based Interactive Chart Renderer (Zero External Dependency)
   */
  const ChartManager = {
    render: function () {
      const chartContainers = document.querySelectorAll('.css-chart-container');
      chartContainers.forEach((container) => {
        const bars = container.querySelectorAll('.bar-fill');
        bars.forEach((bar) => {
          const targetHeight = bar.getAttribute('data-height') || '50%';
          setTimeout(() => {
            bar.style.height = targetHeight;
          }, 150);
        });
      });
    },
  };

  /**
   * Periodic Telemetry Poller (Every 60s)
   */
  const PollingManager = {
    timer: null,

    init: function () {
      this.timer = setInterval(() => {
        this.fetchStatus();
      }, APP_CONFIG.autoRefreshIntervalMs);
    },

    fetchStatus: function () {
      fetch('/api/status')
        .then((res) => {
          if (!res.ok) throw new Error('Status poll failed');
          return res.json();
        })
        .then((data) => {
          if (data.status === 'success') {
            // Update quick stat numbers if element exists
            const elVideos = document.getElementById('statTotalVideos');
            if (elVideos && data.total_videos !== undefined) elVideos.innerText = data.total_videos.toLocaleString('ar-EG');

            const elPublished = document.getElementById('statPublishedToday');
            if (elPublished && data.published_today !== undefined) elPublished.innerText = data.published_today.toLocaleString('ar-EG');

            const elPending = document.getElementById('statPendingVideos');
            if (elPending && data.pending_videos !== undefined) elPending.innerText = data.pending_videos.toLocaleString('ar-EG');

            const elErrors = document.getElementById('statActiveErrors');
            if (elErrors && data.active_errors !== undefined) elErrors.innerText = data.active_errors.toLocaleString('ar-EG');
          }
        })
        .catch((err) => {
          console.warn('Status poller silent error:', err);
        });
    },
  };

  /**
   * Initialize All Modules upon DOM Ready
   */
  document.addEventListener('DOMContentLoaded', () => {
    ThemeManager.init();
    NavigationManager.init();
    SocketManager.init();
    ActionManager.init();
    ChartManager.render();
    PollingManager.init();
  });

  // Export to Global Scope
  window.Dashboard = {
    ThemeManager,
    ToastManager,
    ActionManager,
    getCsrfToken,
  };
})();
