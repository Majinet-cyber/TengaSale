/**
 * TengaSale Motion & UI JS
 * Animated counters, image lightbox, skeleton loading, section locking
 */

(function () {
    'use strict';

    /* ── Animated counter ───────────────────────────────────────────── */
    function animateCounter(el) {
        var raw  = el.textContent.replace(/[^0-9.]/g, '');
        var target = parseFloat(raw);
        if (isNaN(target) || target === 0) return;
        var prefix = el.dataset.prefix || '';
        var suffix = el.dataset.suffix || '';
        var decimals = (raw.indexOf('.') > -1) ? raw.split('.')[1].length : 0;
        var duration = 1000;
        var start = null;

        function step(ts) {
            if (!start) start = ts;
            var progress = Math.min((ts - start) / duration, 1);
            var ease = 1 - Math.pow(1 - progress, 3); /* ease-out-cubic */
            var current = ease * target;
            el.textContent = prefix + current.toFixed(decimals) + suffix;
            if (progress < 1) requestAnimationFrame(step);
            else el.textContent = prefix + target.toFixed(decimals) + suffix;
        }
        requestAnimationFrame(step);
    }

    function initCounters() {
        var els = document.querySelectorAll('[data-counter]');
        if (!els.length) return;
        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    animateCounter(entry.target);
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.5 });
        els.forEach(function (el) { observer.observe(el); });
    }

    /* ── Image lightbox ─────────────────────────────────────────────── */
    var lightbox = null;
    var lightboxImg = null;
    var rotateAngle = 0;

    function createLightbox() {
        if (lightbox) return;
        lightbox = document.createElement('div');
        lightbox.className = 'ts-lightbox';
        lightbox.style.display = 'none';
        lightbox.innerHTML = [
            '<button class="ts-lightbox-close" id="ts-lb-close" aria-label="Close">&#x2715;</button>',
            '<img src="" alt="Preview" id="ts-lb-img">',
            '<div class="ts-lightbox-controls">',
            '  <button class="ts-lightbox-btn" id="ts-lb-rotate">&#8635; Rotate</button>',
            '  <button class="ts-lightbox-btn" id="ts-lb-close2">Close</button>',
            '</div>'
        ].join('');
        document.body.appendChild(lightbox);
        lightboxImg = document.getElementById('ts-lb-img');

        document.getElementById('ts-lb-close').addEventListener('click', closeLightbox);
        document.getElementById('ts-lb-close2').addEventListener('click', closeLightbox);
        document.getElementById('ts-lb-rotate').addEventListener('click', function () {
            rotateAngle = (rotateAngle + 90) % 360;
            lightboxImg.style.transform = 'rotate(' + rotateAngle + 'deg)';
        });
        lightbox.addEventListener('click', function (e) {
            if (e.target === lightbox) closeLightbox();
        });
    }

    function openLightbox(src) {
        createLightbox();
        rotateAngle = 0;
        lightboxImg.style.transform = '';
        lightboxImg.src = src;
        lightbox.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    function closeLightbox() {
        if (!lightbox) return;
        lightbox.style.display = 'none';
        document.body.style.overflow = '';
    }

    function initImagePreviews() {
        /* Make all .kyc-preview-img and .kyc-preview images clickable */
        document.querySelectorAll('.kyc-preview-img, [data-lightbox-img]').forEach(function (img) {
            if (!img.src || img.src === window.location.href) return;
            img.style.cursor = 'zoom-in';
            img.addEventListener('click', function () {
                if (img.src && img.src !== window.location.href) openLightbox(img.src);
            });
        });

        /* Handle image load/error with skeleton fallback */
        document.querySelectorAll('.ts-img-wrap img').forEach(function (img) {
            img.classList.add('loading');
            function onLoad() { img.classList.remove('loading'); img.classList.add('loaded'); }
            function onError() {
                img.style.display = 'none';
                var wrap = img.closest('.ts-img-wrap');
                if (wrap) {
                    var fallback = wrap.querySelector('.ts-img-fallback');
                    if (fallback) fallback.style.display = 'flex';
                }
            }
            if (img.complete) {
                if (img.naturalWidth === 0) onError(); else onLoad();
            } else {
                img.addEventListener('load', onLoad);
                img.addEventListener('error', onError);
            }
        });
    }

    /* ── Smart image loading (general broken image handling) ─────────── */
    function initBrokenImages() {
        document.querySelectorAll('img[src]').forEach(function (img) {
            img.addEventListener('error', function () {
                /* Only replace once and only for real image attempts */
                if (img.dataset.fbDone) return;
                img.dataset.fbDone = '1';
                var alt = img.alt || '';
                var initials = alt.split(' ').slice(0, 2).map(function (w) { return w[0] || ''; }).join('').toUpperCase() || '?';
                img.style.display = 'none';
                var parent = img.parentElement;
                if (parent && !parent.querySelector('.ts-img-fallback')) {
                    var fb = document.createElement('div');
                    fb.className = 'ts-img-fallback';
                    fb.textContent = initials;
                    fb.style.cssText = 'width:100%;height:100%;min-height:48px;display:flex;align-items:center;justify-content:center;background:#f3f4f6;color:#667085;font-size:18px;font-weight:700;border-radius:inherit;';
                    parent.appendChild(fb);
                }
            });
        });
    }

    /* ── Section locking ────────────────────────────────────────────── */
    function initSectionLocking() {
        document.querySelectorAll('[data-lockable-section]').forEach(function (section) {
            var editBtn = section.querySelector('[data-section-edit-btn]');
            var saveBtn = section.querySelector('[data-section-save-btn]');
            if (!editBtn) return;

            editBtn.addEventListener('click', function () {
                section.classList.remove('section-locked');
                editBtn.style.display = 'none';
                if (saveBtn) saveBtn.style.display = '';
                var inputs = section.querySelectorAll('input, select, textarea');
                inputs.forEach(function (i) { i.removeAttribute('readonly'); i.disabled = false; });
            });
        });
    }

    /* ── Stagger animation trigger ──────────────────────────────────── */
    function initStagger() {
        document.querySelectorAll('.ts-stagger').forEach(function (container) {
            var observer = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.querySelectorAll(':scope > *').forEach(function (child, i) {
                            child.style.animationDelay = (i * 60) + 'ms';
                        });
                        observer.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.1 });
            observer.observe(container);
        });
    }

    /* ── KPI counter cards ──────────────────────────────────────────── */
    function initKpiCards() {
        document.querySelectorAll('.kpi-card .kpi-value').forEach(function (el) {
            el.setAttribute('data-counter', '1');
        });
    }

    /* ── Keyboard close for lightbox ────────────────────────────────── */
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeLightbox();
    });

    /* ── Init on DOM ready ──────────────────────────────────────────── */
    function init() {
        initCounters();
        initImagePreviews();
        initBrokenImages();
        initSectionLocking();
        initStagger();
        initKpiCards();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
