/**
 * IMEI capture — digit validation, paste helper, optional camera scanner.
 */
(function (global) {
  'use strict';

  var HTML5_QR_CDN = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      if (document.querySelector('script[src="' + src + '"]')) {
        resolve();
        return;
      }
      var s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function initImeiInput(options) {
    var input = options.input;
    var submitBtn = options.submitBtn;
    var counterEl = options.counterEl;
    var statusEl = options.statusEl;
    var pasteBtn = options.pasteBtn;
    var scanBtn = options.scanBtn;
    var scannerPanel = options.scannerPanel;
    var scannerMount = options.scannerMount;
    var scannerFallback = options.scannerFallback;
    var technicalDetails = options.technicalDetails;
    var onValid = options.onValid;

    if (!input) return;

    input.setAttribute('inputmode', 'numeric');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('maxlength', '15');
    input.setAttribute('pattern', '[0-9]{15}');

    var scannerInstance = null;
    var technicalError = '';

    function setStatus(state, message) {
      if (!statusEl) return;
      statusEl.dataset.state = state;
      statusEl.textContent = message || '';
    }

    function updateCounter(len) {
      if (counterEl) {
        counterEl.textContent = len + ' / 15 digits';
      }
    }

    function validate() {
      var digits = input.value.replace(/\D/g, '').slice(0, 15);
      if (input.value !== digits) {
        input.value = digits;
      }
      var len = digits.length;
      updateCounter(len);

      if (len === 0) {
        setStatus('neutral', '');
        if (submitBtn) submitBtn.disabled = true;
        return false;
      }
      if (len < 15) {
        setStatus('invalid', 'Enter exactly 15 digits.');
        if (submitBtn) submitBtn.disabled = true;
        return false;
      }
      setStatus('valid', '✓ Valid IMEI');
      if (submitBtn) submitBtn.disabled = false;
      if (typeof onValid === 'function') onValid(digits);
      return true;
    }

    input.addEventListener('input', validate);
    input.addEventListener('paste', function () {
      setTimeout(validate, 0);
    });

    if (pasteBtn && navigator.clipboard && navigator.clipboard.readText) {
      pasteBtn.addEventListener('click', function () {
        navigator.clipboard.readText().then(function (text) {
          input.value = String(text || '').replace(/\D/g, '').slice(0, 15);
          validate();
          input.focus();
        }).catch(function () {
          setStatus('invalid', 'Could not read clipboard.');
        });
      });
    } else if (pasteBtn) {
      pasteBtn.style.display = 'none';
    }

    function showScannerFallback(message) {
      if (scannerPanel) scannerPanel.hidden = false;
      if (scannerMount) scannerMount.hidden = true;
      if (scannerFallback) {
        scannerFallback.hidden = false;
        scannerFallback.textContent = message;
      }
    }

    function hideScanner() {
      if (scannerPanel) scannerPanel.hidden = true;
      if (scannerFallback) scannerFallback.hidden = true;
      if (scannerMount) scannerMount.hidden = false;
    }

    async function stopScanner() {
      if (scannerInstance && scannerInstance.isScanning) {
        try {
          await scannerInstance.stop();
        } catch (e) {
          /* ignore */
        }
      }
      scannerInstance = null;
    }

    async function startScanner() {
      technicalError = '';
      if (technicalDetails) {
        technicalDetails.open = false;
        technicalDetails.textContent = '';
      }

      if (!window.isSecureContext) {
        showScannerFallback('Scanner unavailable on this device. Enter IMEI manually.');
        return;
      }

      if (scannerPanel) scannerPanel.hidden = false;
      if (scannerFallback) scannerFallback.hidden = true;
      if (scannerMount) scannerMount.hidden = false;

      try {
        await loadScript(HTML5_QR_CDN);
      } catch (err) {
        technicalError = String(err);
        showScannerFallback('Scanner unavailable on this device. Enter IMEI manually.');
        if (technicalDetails) technicalDetails.textContent = technicalError;
        return;
      }

      if (!global.Html5Qrcode) {
        showScannerFallback('Scanner unavailable on this device. Enter IMEI manually.');
        return;
      }

      var mountId = scannerMount.id || 'imei-scanner-mount';
      if (!scannerMount.id) scannerMount.id = mountId;

      try {
        await stopScanner();
        scannerInstance = new global.Html5Qrcode(mountId);
        await scannerInstance.start(
          { facingMode: 'environment' },
          { fps: 10, qrbox: { width: 250, height: 120 }, aspectRatio: 1.0 },
          function (decoded) {
            var digits = String(decoded || '').replace(/\D/g, '');
            var match = digits.match(/\d{15}/);
            if (match) {
              input.value = match[0];
              validate();
              stopScanner().then(hideScanner);
            }
          },
          function () { /* frame errors ignored */ }
        );
      } catch (err) {
        technicalError = String(err && err.message ? err.message : err);
        var msg = 'Scanner unavailable on this device. Enter IMEI manually.';
        if (/denied|permission|notallowed/i.test(technicalError)) {
          msg = 'Camera access denied. Enter IMEI manually or enable camera permission.';
        }
        showScannerFallback(msg);
        if (technicalDetails) technicalDetails.textContent = technicalError;
      }
    }

    if (scanBtn) {
      scanBtn.addEventListener('click', function () {
        if (scannerPanel && !scannerPanel.hidden) {
          stopScanner().then(hideScanner);
          return;
        }
        startScanner();
      });
    }

    validate();
  }

  global.TengaSaleImeiScanner = { init: initImeiInput };
})(typeof window !== 'undefined' ? window : globalThis);
