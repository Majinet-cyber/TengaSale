/**
 * Global MWK formatting helper.
 * Usage: formatMWK(2500) → "MWK 2,500"
 */
(function (global) {
  'use strict';

  function toNumber(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function formatMWK(value, options) {
    options = options || {};
    var n = toNumber(value);
    var decimals = !!options.decimals;
    var signed = !!options.signed;
    var abs = Math.abs(n);
    var formatted;

    if (!decimals && Math.floor(abs) === abs) {
      formatted = abs.toLocaleString('en-MW', { maximumFractionDigits: 0 });
    } else {
      formatted = abs.toLocaleString('en-MW', {
        minimumFractionDigits: decimals ? 2 : 0,
        maximumFractionDigits: decimals ? 2 : 0,
      });
    }

    if (options.plain) {
      return formatted;
    }

    var prefix = 'MWK ';
    if (signed) {
      prefix = (n >= 0 ? '+' : '-') + 'MWK ';
    }
    return prefix + formatted;
  }

  global.formatMWK = formatMWK;
})(typeof window !== 'undefined' ? window : globalThis);
