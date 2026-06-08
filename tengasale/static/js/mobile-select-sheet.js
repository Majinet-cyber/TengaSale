/**
 * TengaSale Premium Mobile Select Sheet
 * Replaces native <select> on mobile with a premium bottom sheet / desktop popover.
 * Usage: add data-ts-select to a <select> element.
 */
(function (global) {
  'use strict';

  var isMobile = function () { return global.innerWidth < 640; };

  function buildSheet(select) {
    var label = select.getAttribute('data-label') || select.getAttribute('aria-label') || 'Select';
    var searchable = select.options.length > 6 || select.getAttribute('data-searchable') === 'true';

    // Wrapper
    var wrapper = document.createElement('div');
    wrapper.className = 'ts-select-wrapper';
    wrapper.style.position = 'relative';

    // Trigger button
    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'ts-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('data-testid', 'select-trigger-' + (select.name || 'unknown'));

    var selectedOpt = select.options[select.selectedIndex];
    var triggerText = document.createElement('span');
    triggerText.className = selectedOpt && selectedOpt.value
      ? ''
      : 'ts-select-trigger__placeholder';
    triggerText.textContent = selectedOpt ? selectedOpt.text : (label || 'Choose…');

    var chevron = document.createElement('span');
    chevron.className = 'ts-select-trigger__chevron';
    chevron.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>';

    trigger.appendChild(triggerText);
    trigger.appendChild(chevron);

    // Backdrop (mobile)
    var backdrop = document.createElement('div');
    backdrop.className = 'ts-select-sheet-backdrop hidden';

    // Sheet
    var sheet = document.createElement('div');
    sheet.className = 'ts-select-sheet hidden';
    sheet.setAttribute('role', 'listbox');
    sheet.setAttribute('aria-label', label);

    // Sheet header
    var header = document.createElement('div');
    header.className = 'ts-select-sheet__header';
    var titleEl = document.createElement('span');
    titleEl.className = 'ts-select-sheet__title';
    titleEl.textContent = label;
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'ts-select-sheet__close';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';
    header.appendChild(titleEl);
    header.appendChild(closeBtn);
    sheet.appendChild(header);

    // Search
    var searchWrap = null;
    var searchInput = null;
    if (searchable) {
      searchWrap = document.createElement('div');
      searchWrap.className = 'ts-select-sheet__search';
      searchInput = document.createElement('input');
      searchInput.type = 'text';
      searchInput.placeholder = 'Search…';
      searchInput.setAttribute('aria-label', 'Search options');
      searchWrap.appendChild(searchInput);
      sheet.appendChild(searchWrap);
    }

    // Options list
    var list = document.createElement('div');
    list.className = 'ts-select-sheet__list';

    function buildOptions(filterText) {
      list.innerHTML = '';
      var opts = Array.from(select.options);
      opts.forEach(function (opt) {
        if (!opt.value && !opt.text.trim()) return;
        if (filterText && opt.text.toLowerCase().indexOf(filterText.toLowerCase()) === -1) return;

        var item = document.createElement('div');
        item.className = 'ts-select-option' + (opt.value === select.value ? ' ts-select-option--selected' : '');
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', opt.value === select.value ? 'true' : 'false');
        item.setAttribute('data-value', opt.value);

        var textNode = document.createElement('span');
        textNode.textContent = opt.text;
        var check = document.createElement('span');
        check.className = 'ts-select-option__check';
        if (opt.value === select.value) {
          check.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M20 6L9 17l-5-5"/></svg>';
        }
        item.appendChild(textNode);
        item.appendChild(check);

        item.addEventListener('click', function () {
          select.value = opt.value;
          select.dispatchEvent(new Event('change', { bubbles: true }));
          triggerText.textContent = opt.text;
          triggerText.className = opt.value ? '' : 'ts-select-trigger__placeholder';
          close();
        });

        list.appendChild(item);
      });
    }

    buildOptions('');
    sheet.appendChild(list);

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        buildOptions(searchInput.value);
      });
    }

    function open() {
      buildOptions(searchInput ? searchInput.value : '');
      trigger.setAttribute('aria-expanded', 'true');
      backdrop.classList.remove('hidden');
      sheet.classList.remove('hidden');
      if (searchInput) {
        searchInput.value = '';
        buildOptions('');
        setTimeout(function () { searchInput.focus(); }, 120);
      }
      document.body.style.overflow = 'hidden';
    }

    function close() {
      trigger.setAttribute('aria-expanded', 'false');
      backdrop.classList.add('hidden');
      sheet.classList.add('hidden');
      document.body.style.overflow = '';
    }

    trigger.addEventListener('click', function () {
      if (sheet.classList.contains('hidden')) open(); else close();
    });

    closeBtn.addEventListener('click', close);
    backdrop.addEventListener('click', close);

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !sheet.classList.contains('hidden')) close();
    });

    // Desktop: position sheet below trigger
    trigger.addEventListener('click', function () {
      if (!isMobile() && !sheet.classList.contains('hidden')) {
        var rect = trigger.getBoundingClientRect();
        sheet.style.position = 'fixed';
        sheet.style.top = (rect.bottom + 6) + 'px';
        sheet.style.left = rect.left + 'px';
        sheet.style.width = Math.max(rect.width, 220) + 'px';
        sheet.style.borderRadius = '14px';
      }
    });

    // Hide the original select but keep it functional
    select.style.display = 'none';
    select.setAttribute('data-ts-select-enhanced', 'true');

    wrapper.appendChild(trigger);
    wrapper.appendChild(backdrop);
    wrapper.appendChild(sheet);

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);

    // Sync if select changes programmatically
    select.addEventListener('change', function () {
      var opt = select.options[select.selectedIndex];
      if (opt) {
        triggerText.textContent = opt.text;
        triggerText.className = opt.value ? '' : 'ts-select-trigger__placeholder';
      }
    });
  }

  function init() {
    document.querySelectorAll('select[data-ts-select]').forEach(function (select) {
      if (!select.getAttribute('data-ts-select-enhanced')) {
        buildSheet(select);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.TengaSaleMobileSelect = { init: init, buildSheet: buildSheet };
})(typeof window !== 'undefined' ? window : globalThis);
