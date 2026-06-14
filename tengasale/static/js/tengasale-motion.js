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
        /* Make all KYC/lightbox images clickable */
        document.querySelectorAll('.kyc-preview-img, [data-lightbox-img], .ts-kyc-img img').forEach(function (img) {
            if (!img.src || img.src === window.location.href) return;
            img.style.cursor = 'zoom-in';
            img.removeAttribute('data-lb-init');
            if (img.dataset.lbInit) return;
            img.dataset.lbInit = '1';
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

    /* ── Broken image handling ───────────────────────────────────────── */
    function showImageLoadError(img) {
        if (img.dataset.fbDone) return;
        img.dataset.fbDone = '1';
        var src = img.src || '';
        console.warn('Image unavailable:', src);
        img.style.display = 'none';
        var parent = img.parentElement;
        if (!parent || parent.querySelector('.ts-img-fallback')) return;
        var fb = document.createElement('div');
        fb.className = 'ts-img-fallback ts-img-fallback--missing';
        fb.setAttribute('role', 'img');
        fb.setAttribute('aria-label', img.alt || 'Image not available');
        fb.textContent = img.alt ? img.alt : 'Photo not captured';
        fb.style.cssText = 'width:100%;height:100%;min-height:48px;display:flex;align-items:center;justify-content:center;background:#f8fafc;color:#667085;font-size:12px;font-weight:500;border-radius:inherit;padding:8px;text-align:center;border:1px dashed #e5e7eb;';
        parent.appendChild(fb);
    }

    function initBrokenImages() {
        document.querySelectorAll('img[src]').forEach(function (img) {
            if (img.dataset.skipImgFallback === '1') return;
            if (!img.src || img.src === window.location.href) return;
            img.addEventListener('error', function () { showImageLoadError(img); });
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

    /* Audio feedback and toasts */
    var soundKeys = {
        enabled: 'tengasale.sound.enabled',
        volume: 'tengasale.sound.volume'
    };
    var soundFiles = {
        success: '/static/sounds/success.mp3',
        error: '/static/sounds/error.mp3',
        notification: '/static/sounds/notification.mp3',
        claim: '/static/sounds/claim.mp3',
        payment: '/static/sounds/payment.mp3',
        spinWin: '/static/sounds/spin-win.mp3',
        lock: '/static/sounds/lock.mp3'
    };

    function storageGet(key, fallback) {
        try {
            var value = window.localStorage.getItem(key);
            return value === null ? fallback : value;
        } catch (e) {
            return fallback;
        }
    }

    function storageSet(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (e) {
            return false;
        }
        return true;
    }

    function soundEnabled() {
        return storageGet(soundKeys.enabled, 'false') === 'true';
    }

    function soundVolume() {
        var value = parseFloat(storageGet(soundKeys.volume, '0.35'));
        if (isNaN(value)) return 0.35;
        return Math.max(0, Math.min(1, value));
    }

    function setSoundEnabled(enabled) {
        storageSet(soundKeys.enabled, enabled ? 'true' : 'false');
        updateSoundToggles();
    }

    function setSoundVolume(volume) {
        var next = Math.max(0, Math.min(1, parseFloat(volume)));
        storageSet(soundKeys.volume, isNaN(next) ? '0.35' : String(next));
    }

    function playSound(name) {
        if (!soundEnabled()) return;
        var src = soundFiles[name];
        if (!src || typeof window.Audio !== 'function') return;
        try {
            var audio = new window.Audio(src);
            audio.volume = soundVolume();
            var result = audio.play();
            if (result && typeof result.catch === 'function') {
                result.catch(function () {});
            }
        } catch (e) {}
    }

    window.TengaSaleAudio = {
        play: playSound,
        setEnabled: setSoundEnabled,
        getEnabled: soundEnabled,
        setVolume: setSoundVolume,
        getVolume: soundVolume
    };

    function getToastStack() {
        var stack = document.querySelector('[data-ts-toast-stack]');
        if (stack) return stack;
        stack = document.createElement('div');
        stack.className = 'ts-toast-stack';
        stack.setAttribute('data-ts-toast-stack', '1');
        stack.setAttribute('aria-live', 'polite');
        stack.setAttribute('aria-atomic', 'true');
        document.body.appendChild(stack);
        return stack;
    }

    function closeToast(toast) {
        if (!toast || toast.dataset.closing === '1') return;
        toast.dataset.closing = '1';
        toast.classList.add('is-leaving');
        window.setTimeout(function () {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 180);
    }

    function showToast(message, options) {
        options = options || {};
        var type = options.type || 'info';
        var stack = getToastStack();
        var toast = document.createElement('div');
        toast.className = 'ts-toast ts-toast--' + type;
        toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
        toast.innerHTML = [
            '<div class="ts-toast__message"></div>',
            '<button type="button" class="ts-toast__close" aria-label="Close">&times;</button>'
        ].join('');
        toast.querySelector('.ts-toast__message').textContent = message;
        toast.querySelector('.ts-toast__close').addEventListener('click', function () {
            closeToast(toast);
        });
        stack.appendChild(toast);
        if (options.sound) playSound(options.sound);
        window.setTimeout(function () { closeToast(toast); }, options.duration || 3600);
        return toast;
    }

    window.TengaSaleToast = {
        show: showToast
    };

    function updateSoundToggles() {
        var enabled = soundEnabled();
        document.querySelectorAll('[data-ts-sound-toggle]').forEach(function (btn) {
            btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
            var label = btn.querySelector('span');
            var icon = btn.querySelector('i');
            if (label) label.textContent = enabled ? 'Sound on' : 'Sound off';
            if (icon) {
                icon.classList.toggle('bi-volume-up', enabled);
                icon.classList.toggle('bi-volume-mute', !enabled);
            }
        });
    }

    function initAudioFeedback() {
        updateSoundToggles();
        document.querySelectorAll('[data-ts-sound-toggle]').forEach(function (btn) {
            if (btn.dataset.soundToggleInit) return;
            btn.dataset.soundToggleInit = '1';
            btn.addEventListener('click', function () {
                var enabled = !soundEnabled();
                setSoundEnabled(enabled);
                showToast(enabled ? 'Sound feedback enabled' : 'Sound feedback muted', {
                    type: enabled ? 'success' : 'info',
                    sound: enabled ? 'notification' : null
                });
            });
        });
        document.querySelectorAll('[data-ts-sound]').forEach(function (el) {
            if (el.dataset.soundInit) return;
            el.dataset.soundInit = '1';
            el.addEventListener('click', function () {
                playSound(el.dataset.tsSound);
            });
        });
    }

    function isDevHost() {
        return /localhost|127\.0\.0\.1/.test(window.location.hostname);
    }

    function devLog() {
        if (!isDevHost()) return;
        try {
            console.log.apply(console, arguments);
        } catch (e) {}
    }

    var COOLDOWN_STORAGE_KEY = 'underwriter_claim_cooldown_expires_at';

    function readCooldownExpiresAt() {
        try {
            var stored = window.localStorage.getItem(COOLDOWN_STORAGE_KEY);
            if (!stored) return null;
            var value = parseInt(stored, 10);
            return isNaN(value) ? null : value;
        } catch (e) {
            return null;
        }
    }

    function writeCooldownExpiresAt(expiresAt) {
        if (!expiresAt) return;
        try {
            window.localStorage.setItem(COOLDOWN_STORAGE_KEY, String(expiresAt));
        } catch (e) {}
    }

    function clearCooldownExpiresAt() {
        try {
            window.localStorage.removeItem(COOLDOWN_STORAGE_KEY);
        } catch (e) {}
    }

    function cooldownSecondsRemaining(expiresAt) {
        if (!expiresAt) return 0;
        return Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    }

    function initClaimForms() {
        document.querySelectorAll('[data-claim-next-form]').forEach(function (form) {
            if (form.dataset.claimInit) return;
            form.dataset.claimInit = '1';
            form.addEventListener('submit', function () {
                var button = form.querySelector('button[type="submit"]');
                var label = form.querySelector('[data-claim-label]');
                var sub = form.querySelector('[data-claim-sub]');
                var skeleton = document.querySelector('[data-claim-skeleton]');
                form.classList.add('is-claiming');
                if (button) {
                    button.disabled = true;
                    button.setAttribute('aria-busy', 'true');
                }
                if (label) label.textContent = 'CLAIMING…';
                if (sub) sub.textContent = 'Assigning application';
                if (skeleton) skeleton.hidden = false;
                var expiresAt = Date.now() + (5 * 60 * 1000);
                writeCooldownExpiresAt(expiresAt);
                devLog('claim response', { status: 'submitting', cooldownExpiresAt: expiresAt });
                try {
                    window.sessionStorage.setItem('tengasale.lastAction', 'claim');
                    window.sessionStorage.setItem('tengasale.reviewLoading', '1');
                } catch (e) {}
                showToast('Claiming next application...', { type: 'info', duration: 1800 });
            });
        });
    }

    function formatCooldown(seconds) {
        var total = Math.max(0, parseInt(seconds, 10) || 0);
        if (total < 60) {
            return total + 'S';
        }
        var mins = Math.floor(total / 60);
        var secs = total % 60;
        return mins + ':' + (secs < 10 ? '0' : '') + secs;
    }

    function renderCooldownLabel(el, seconds) {
        if (!el) return;
        var prefix = seconds < 60 ? 'NEXT CLAIM IN ' : 'NEXT CLAIM IN ';
        el.textContent = prefix + formatCooldown(seconds);
    }

    function resolveCooldownExpiresAt(home, cooldownBtn) {
        var serverExpires = parseInt(home.getAttribute('data-cooldown-expires-at') || '0', 10);
        var storedExpires = readCooldownExpiresAt();
        var btnRemaining = cooldownBtn
            ? parseInt(cooldownBtn.getAttribute('data-cooldown-remaining') || '0', 10)
            : 0;

        if (serverExpires > 0) {
            writeCooldownExpiresAt(serverExpires);
            return serverExpires;
        }
        if (storedExpires && storedExpires > Date.now()) {
            return storedExpires;
        }
        if (btnRemaining > 0) {
            return Date.now() + (btnRemaining * 1000);
        }
        clearCooldownExpiresAt();
        return null;
    }

    function initQueueCooldown() {
        var home = document.querySelector('.uw-home[data-queue-status-url]');
        if (!home) return;

        var cooldownBtn = home.querySelector('[data-cooldown-btn]');
        var cooldownLabel = home.querySelector('[data-cooldown-label]');
        var expiresAt = resolveCooldownExpiresAt(home, cooldownBtn);
        var remaining = cooldownSecondsRemaining(expiresAt);

        if (cooldownBtn && remaining > 0) {
            renderCooldownLabel(cooldownLabel, remaining);
            var timer = window.setInterval(function () {
                remaining = cooldownSecondsRemaining(expiresAt);
                cooldownBtn.setAttribute('data-cooldown-remaining', String(remaining));
                if (remaining <= 0) {
                    window.clearInterval(timer);
                    clearCooldownExpiresAt();
                    window.location.reload();
                    return;
                }
                renderCooldownLabel(cooldownLabel, remaining);
            }, 1000);
        } else if (!cooldownBtn) {
            clearCooldownExpiresAt();
        }

        var pollUrl = home.getAttribute('data-queue-status-url');
        var pollInterval = parseInt(home.getAttribute('data-poll-interval') || '30', 10) * 1000;
        if (!pollUrl || pollInterval <= 0) return;

        window.setInterval(function () {
            fetch(pollUrl, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (data) {
                    if (!data) return;
                    var countEl = home.querySelector('.uw-active-card__count');
                    if (countEl && typeof data.active_count === 'number' && typeof data.max_active === 'number') {
                        countEl.textContent = data.active_count + '/' + data.max_active;
                    }
                    if (typeof data.cooldown_expires_at === 'number' && data.cooldown_expires_at > Date.now()) {
                        writeCooldownExpiresAt(data.cooldown_expires_at);
                    }
                    if (data.can_claim && cooldownBtn) {
                        clearCooldownExpiresAt();
                        window.location.reload();
                    }
                })
                .catch(function () {});
        }, pollInterval);
    }

    function initReviewLoadingState() {
        var reviewBody = document.querySelector('[data-review-application-body]');
        var loading = document.querySelector('[data-review-loading]');
        if (!reviewBody && !loading) return;

        var reviewId = null;
        var parts = window.location.pathname.split('/').filter(Boolean);
        if (parts.length >= 3 && parts[0] === 'sales' && parts[1] === 'applications') {
            reviewId = parts[2];
        }
        devLog('review id used', reviewId);
        devLog('review route', window.location.pathname);

        var pending = false;
        try {
            pending = window.sessionStorage.getItem('tengasale.reviewLoading') === '1';
            window.sessionStorage.removeItem('tengasale.reviewLoading');
        } catch (e) {}

        if (loading) loading.hidden = true;
        if (reviewBody) reviewBody.hidden = false;

        if (pending && loading) {
            loading.hidden = false;
            if (reviewBody) reviewBody.hidden = true;
            window.setTimeout(function () {
                loading.hidden = true;
                if (reviewBody) {
                    reviewBody.hidden = false;
                    var hasContent = reviewBody.textContent && reviewBody.textContent.trim().length > 0;
                    if (!hasContent) {
                        devLog('review page empty after load');
                    }
                }
            }, 350);
        }
    }

    function initMessageFeedback() {
        var lastAction = null;
        try {
            lastAction = window.sessionStorage.getItem('tengasale.lastAction');
            window.sessionStorage.removeItem('tengasale.lastAction');
        } catch (e) {}
        if (!lastAction) return;
        var success = document.querySelector('.alert-success, .messages .success');
        var error = document.querySelector('.alert-danger, .alert-error, .messages .error');
        if (success) playSound(lastAction === 'claim' ? 'success' : 'notification');
        if (error) playSound('error');
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
        initAudioFeedback();
        initClaimForms();
        initQueueCooldown();
        initReviewLoadingState();
        initMessageFeedback();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
