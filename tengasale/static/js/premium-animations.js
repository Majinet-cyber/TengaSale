/**
 * TengaSale Premium Animations v2.0
 * Count-up, sparkline preview, celebration bursts, success pulses
 */
(function (global) {
  'use strict';

  var prefersReducedMotion = global.matchMedia
    ? global.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

  // ── Count-up animation ────────────────────────────────────────────
  function animateCountUp(el, from, to, duration, format) {
    if (prefersReducedMotion) {
      el.textContent = format ? format(to) : to;
      return;
    }
    var start = null;
    var step = function (timestamp) {
      if (!start) start = timestamp;
      var progress = Math.min((timestamp - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var current = Math.round(from + (to - from) * eased);
      el.textContent = format ? format(current) : current;
      if (progress < 1) {
        requestAnimationFrame(step);
      } else {
        el.textContent = format ? format(to) : to;
      }
    };
    requestAnimationFrame(step);
  }

  function initCountUps() {
    document.querySelectorAll('[data-count-up]').forEach(function (el) {
      var to = parseFloat(el.getAttribute('data-count-up')) || 0;
      var from = parseFloat(el.getAttribute('data-count-from') || '0');
      var duration = parseInt(el.getAttribute('data-count-duration') || '800', 10);
      var prefix = el.getAttribute('data-count-prefix') || '';
      var suffix = el.getAttribute('data-count-suffix') || '';
      var decimals = parseInt(el.getAttribute('data-count-decimals') || '0', 10);

      var format = function (v) {
        return prefix + (decimals > 0 ? v.toFixed(decimals) : v) + suffix;
      };

      animateCountUp(el, from, to, duration, format);
    });
  }

  // ── Intersection observer for KPI card reveals ───────────────────
  function initFadeInOnScroll() {
    if (!global.IntersectionObserver) return;

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('ts-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15 });

    document.querySelectorAll('.ts-fade-in, .ts-kpi-card, .ts-glass-card').forEach(function (el) {
      observer.observe(el);
    });
  }

  // ── Success pulse ─────────────────────────────────────────────────
  function showSuccessPulse(el) {
    if (prefersReducedMotion) return;
    el.classList.add('ts-success-ring');
    setTimeout(function () { el.classList.remove('ts-success-ring'); }, 1200);
  }

  // ── Confetti burst (small) ────────────────────────────────────────
  function showCelebrationBurst(options) {
    if (prefersReducedMotion) return;
    if (!global.confetti) return;

    options = options || {};
    var origin = options.origin || { y: 0.6, x: 0.5 };
    var type = options.type || 'standard';

    if (type === 'money') {
      global.confetti({
        particleCount: 50,
        spread: 70,
        origin: origin,
        colors: ['#FF4D00', '#FFD700', '#16A34A', '#ffffff'],
        scalar: 1.2,
        shapes: ['circle'],
      });
    } else {
      global.confetti({
        particleCount: 30,
        spread: 50,
        origin: origin,
        colors: ['#FF4D00', '#16A34A', '#2563EB'],
        scalar: 0.9,
      });
    }
  }

  // ── Animated check mark ───────────────────────────────────────────
  function createAnimatedCheck(container) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 52 52');
    svg.setAttribute('width', '52');
    svg.setAttribute('height', '52');
    svg.className = 'ts-animated-check';
    svg.style.cssText = 'display:block;margin:0 auto;';

    var circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('cx', '26');
    circle.setAttribute('cy', '26');
    circle.setAttribute('r', '25');
    circle.setAttribute('fill', 'none');
    circle.setAttribute('stroke', '#16A34A');
    circle.setAttribute('stroke-width', '2');

    var check = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    check.setAttribute('fill', 'none');
    check.setAttribute('stroke', '#16A34A');
    check.setAttribute('stroke-width', '3');
    check.setAttribute('stroke-linecap', 'round');
    check.setAttribute('stroke-linejoin', 'round');
    check.setAttribute('d', 'M14 27 l8 8 l16-16');
    check.style.cssText = 'stroke-dasharray:40;stroke-dashoffset:40;transition:stroke-dashoffset 0.5s ease-out 0.2s;';

    svg.appendChild(circle);
    svg.appendChild(check);
    container.appendChild(svg);

    if (!prefersReducedMotion) {
      setTimeout(function () {
        check.style.strokeDashoffset = '0';
      }, 50);
    }

    return svg;
  }

  // ── Toast notification ────────────────────────────────────────────
  function showToastSuccess(message, duration) {
    duration = duration || 3000;

    var existing = document.getElementById('ts-toast-container');
    if (!existing) {
      existing = document.createElement('div');
      existing.id = 'ts-toast-container';
      existing.style.cssText = [
        'position:fixed',
        'bottom:24px',
        'left:50%',
        'transform:translateX(-50%)',
        'z-index:9999',
        'display:flex',
        'flex-direction:column',
        'gap:8px',
        'align-items:center',
        'pointer-events:none',
      ].join(';');
      document.body.appendChild(existing);
    }

    var toast = document.createElement('div');
    toast.style.cssText = [
      'background:rgba(15,23,42,0.92)',
      'color:#fff',
      'padding:12px 20px',
      'border-radius:14px',
      'font-size:14px',
      'font-weight:700',
      'box-shadow:0 8px 32px rgba(15,23,42,0.24)',
      'backdrop-filter:blur(16px)',
      'display:flex',
      'align-items:center',
      'gap:10px',
      'opacity:0',
      'transform:translateY(16px)',
      'transition:all 0.22s cubic-bezier(0.16,1,0.3,1)',
      'pointer-events:auto',
    ].join(';');

    toast.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22C55E" stroke-width="3" stroke-linecap="round"><path d="M20 6L9 17l-5-5"/></svg>' + message;
    existing.appendChild(toast);

    if (!prefersReducedMotion) {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          toast.style.opacity = '1';
          toast.style.transform = 'translateY(0)';
        });
      });
    } else {
      toast.style.opacity = '1';
    }

    setTimeout(function () {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(8px)';
      setTimeout(function () { toast.remove(); }, 300);
    }, duration);
  }

  // ── Chart.js premium defaults ────────────────────────────────────
  function applyPremiumChartDefaults() {
    if (!global.Chart) return;

    global.Chart.defaults.font.family = 'Inter, ui-sans-serif, system-ui, -apple-system, sans-serif';
    global.Chart.defaults.font.size = 12;
    global.Chart.defaults.color = '#94A3B8';
    global.Chart.defaults.plugins.legend.labels.usePointStyle = true;
    global.Chart.defaults.plugins.legend.labels.padding = 16;
    global.Chart.defaults.plugins.tooltip.padding = 12;
    global.Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(15,23,42,0.92)';
    global.Chart.defaults.plugins.tooltip.titleColor = '#F8FAFC';
    global.Chart.defaults.plugins.tooltip.bodyColor = '#CBD5E1';
    global.Chart.defaults.plugins.tooltip.borderColor = 'rgba(255,255,255,0.10)';
    global.Chart.defaults.plugins.tooltip.borderWidth = 1;
    global.Chart.defaults.plugins.tooltip.cornerRadius = 10;
    global.Chart.defaults.animation.duration = prefersReducedMotion ? 0 : 600;
    global.Chart.defaults.animation.easing = 'easeOutQuart';

    // Default border radius on bars
    if (global.Chart.defaults.elements && global.Chart.defaults.elements.bar) {
      global.Chart.defaults.elements.bar.borderRadius = 6;
      global.Chart.defaults.elements.bar.borderSkipped = 'bottom';
    }
  }

  // ── Stagger card entrance (IntersectionObserver) ─────────────────
  function initStaggerEntrance() {
    if (!global.IntersectionObserver) return;
    if (prefersReducedMotion) {
      // Make all stagger items immediately visible
      document.querySelectorAll('.ts-stagger-item').forEach(function (el) {
        el.style.opacity = '1';
        el.style.animation = 'none';
      });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          // Allow the CSS animation to fire (opacity starts 0, goes to 1)
          entry.target.style.animationPlayState = 'running';
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.1 });

    document.querySelectorAll('.ts-stagger-item').forEach(function (el) {
      // Pause animation until element is in viewport
      el.style.animationPlayState = 'paused';
      io.observe(el);
    });
  }

  // ── KYC bar chart animate on scroll ──────────────────────────────
  function initKycFunnelBars() {
    if (!global.IntersectionObserver) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var fill = entry.target.querySelector('.kyc-funnel-row__fill');
          if (fill && fill.getAttribute('data-width')) {
            fill.style.width = fill.getAttribute('data-width');
          }
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.2 });

    document.querySelectorAll('.kyc-funnel-row').forEach(function (row) {
      var fill = row.querySelector('.kyc-funnel-row__fill');
      if (fill) {
        var w = fill.style.width || '0%';
        fill.setAttribute('data-width', w);
        if (!prefersReducedMotion) {
          fill.style.width = '0%';
        }
        io.observe(row);
      }
    });
  }

  // ── Simulation bar chart animate on scroll ────────────────────────
  function initSimBars() {
    if (!global.IntersectionObserver) return;
    if (prefersReducedMotion) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.querySelectorAll('.sim-bar-fill').forEach(function (fill) {
            var w = fill.style.width;
            fill.style.width = '0';
            fill.style.transition = 'width 0.8s cubic-bezier(0.16,1,0.3,1)';
            requestAnimationFrame(function () {
              setTimeout(function () { fill.style.width = w; }, 60);
            });
          });
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.3 });

    document.querySelectorAll('.sim-bars').forEach(function (el) {
      io.observe(el);
    });
  }

  // ── Initialize ───────────────────────────────────────────────────
  function init() {
    initCountUps();
    initFadeInOnScroll();
    initStaggerEntrance();
    initKycFunnelBars();
    initSimBars();
    applyPremiumChartDefaults();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose globally
  global.TengaSalePremium = {
    animateCountUp: animateCountUp,
    showSuccessPulse: showSuccessPulse,
    showCelebrationBurst: showCelebrationBurst,
    createAnimatedCheck: createAnimatedCheck,
    showToastSuccess: showToastSuccess,
    applyPremiumChartDefaults: applyPremiumChartDefaults,
  };
})(typeof window !== 'undefined' ? window : globalThis);
