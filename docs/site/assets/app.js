/* 战国 Mod 开发文档 —— 前端交互脚本
   纯静态站点，无依赖：搜索过滤 / 键盘快捷键 / 移动端侧栏开关。 */
(function () {
  'use strict';

  var MAX_RESULTS = 20;
  var SNIPPET_RADIUS = 40;
  var DEBOUNCE_MS = 50;
  var ESCAPES = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  };

  var currentScript = document.currentScript;

  /* ---------------------------------------------------------- 工具函数 */

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value).replace(
      /[&<>"']/g,
      function (ch) {
        return ESCAPES[ch];
      }
    );
  }

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  /* 站点根目录：优先用 search-index.js 的 src 推导，退回 app.js 自身。 */
  function resolveSiteRoot() {
    var candidates = [
      document.querySelector('script[src*="search-index.js"]'),
      currentScript
    ];
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (el && el.src) {
        try {
          return new URL('../', new URL(el.src, document.baseURI));
        } catch (err) {
          /* 忽略，走下一个候选 */
        }
      }
    }
    return new URL('.', document.baseURI);
  }

  function resolvePageUrl(path, siteRoot) {
    try {
      return new URL(String(path || ''), siteRoot);
    } catch (err) {
      return null;
    }
  }

  /* 命中高亮：先在原串上定位匹配区间，再逐段转义后拼接 <mark>。 */
  function highlight(text, query, re) {
    var raw = String(text === null || text === undefined ? '' : text);
    if (!query) {
      return escapeHtml(raw);
    }
    re.lastIndex = 0;
    var out = '';
    var last = 0;
    var match;
    var guard = 0;
    while ((match = re.exec(raw)) !== null && guard < 500) {
      out += escapeHtml(raw.slice(last, match.index));
      out += '<mark>' + escapeHtml(match[0]) + '</mark>';
      last = match.index + match[0].length;
      if (match[0].length === 0) {
        re.lastIndex += 1;
      }
      guard += 1;
    }
    out += escapeHtml(raw.slice(last));
    return out;
  }

  /* 正文片段：命中位置前后各约 SNIPPET_RADIUS 个字符。 */
  function buildSnippet(text, query, re) {
    var raw = String(text === null || text === undefined ? '' : text);
    if (!raw) {
      return '';
    }
    re.lastIndex = 0;
    var match = re.exec(raw);
    if (!match) {
      var head = raw.slice(0, SNIPPET_RADIUS * 2);
      return escapeHtml(head) + (raw.length > head.length ? '…' : '');
    }
    var start = Math.max(0, match.index - SNIPPET_RADIUS);
    var end = Math.min(raw.length, match.index + match[0].length + SNIPPET_RADIUS);
    var slice = raw.slice(start, end);
    return (start > 0 ? '…' : '') + highlight(slice, query, re) + (end < raw.length ? '…' : '');
  }

  /* ---------------------------------------------------------- 侧栏开关 */

  var sidebar = document.getElementById('sidebar');
  var navToggle = document.getElementById('nav-toggle');

  function setSidebarOpen(open) {
    if (!sidebar) {
      return;
    }
    sidebar.classList.toggle('open', open);
    if (navToggle) {
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    document.body.classList.toggle('nav-open', open);
  }

  if (navToggle && sidebar) {
    navToggle.addEventListener('click', function (event) {
      event.stopPropagation();
      setSidebarOpen(!sidebar.classList.contains('open'));
    });
  }

  /* ---------------------------------------------------------- 搜索 */

  var index = window.DOC_SEARCH_INDEX;
  if (!index || !index.pages || !index.pages.length) {
    return; /* 没有索引就安静退出 */
  }

  var entries = [];
  for (var i = 0; i < index.pages.length; i++) {
    var page = index.pages[i] || {};
    var title = String(page.title === null || page.title === undefined ? '' : page.title);
    var summary = String(page.summary === null || page.summary === undefined ? '' : page.summary);
    var text = String(page.text === null || page.text === undefined ? '' : page.text);
    entries.push({
      path: String(page.path === null || page.path === undefined ? '' : page.path),
      section: String(page.section === null || page.section === undefined ? '' : page.section),
      title: title,
      summary: summary,
      text: text,
      titleLc: title.toLowerCase(),
      summaryLc: summary.toLowerCase(),
      textLc: text.toLowerCase()
    });
  }

  var siteRoot = resolveSiteRoot();
  var input = document.getElementById('search-input');
  var resultsBox = document.getElementById('search-results');
  var searchBox = input ? input.closest('.search') : null;
  var timer = null;

  function search(query) {
    var needle = query.trim().toLowerCase();
    if (!needle) {
      return [];
    }
    var hits = [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var inTitle = entry.titleLc.indexOf(needle) !== -1;
      var inSummary = entry.summaryLc.indexOf(needle) !== -1;
      var inText = entry.textLc.indexOf(needle) !== -1;
      if (!inTitle && !inSummary && !inText) {
        continue;
      }
      hits.push({
        entry: entry,
        rank: inTitle ? 0 : (inSummary ? 1 : 2),
        order: i
      });
    }
    hits.sort(function (a, b) {
      return a.rank - b.rank || a.order - b.order;
    });
    return hits.slice(0, MAX_RESULTS);
  }

  function hideResults() {
    if (resultsBox) {
      resultsBox.hidden = true;
      resultsBox.innerHTML = '';
    }
  }

  function render(hits, query) {
    if (!resultsBox) {
      return;
    }
    var re = new RegExp(escapeRegExp(query), 'gi');
    var html = '';

    if (!hits.length) {
      html = '<p class="search-empty">没有找到：' + escapeHtml(query) + '</p>';
    } else {
      for (var i = 0; i < hits.length; i++) {
        var entry = hits[i].entry;
        var url = resolvePageUrl(entry.path, siteRoot);
        var href = url ? url.pathname : escapeHtml(entry.path);
        html +=
          '<a class="search-result" href="' + escapeHtml(href) + '" data-href="' + escapeHtml(href) + '">' +
          '<span class="search-result-head">' +
          '<span class="search-result-title">' + highlight(entry.title, query, re) + '</span>' +
          (entry.section ? '<span class="search-result-section">' + escapeHtml(entry.section) + '</span>' : '') +
          '</span>' +
          '<span class="search-result-snippet">' + buildSnippet(entry.text, query, re) + '</span>' +
          '</a>';
      }
    }

    resultsBox.innerHTML = html;
    resultsBox.hidden = false;
  }

  if (input && resultsBox) {
    input.addEventListener('input', function () {
      var query = input.value.trim();
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
      if (!query) {
        hideResults();
        return;
      }
      timer = window.setTimeout(function () {
        timer = null;
        render(search(query), query);
      }, DEBOUNCE_MS);
    });

    input.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        input.value = '';
        hideResults();
      }
    });

    /* 点击结果：显式用根目录解析后的路径跳转（子目录页面同样正确）。 */
    resultsBox.addEventListener('click', function (event) {
      var target = event.target && event.target.closest ? event.target.closest('a.search-result') : null;
      if (!target) {
        return;
      }
      var href = target.getAttribute('data-href') || target.getAttribute('href');
      if (!href) {
        return;
      }
      event.preventDefault();
      window.location.href = href;
    });
  }

  /* ---------------------------------------------------------- 全局交互 */

  function isTypingTarget(el) {
    if (!el || !el.tagName) {
      return false;
    }
    var tag = el.tagName.toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable === true;
  }

  function focusSearch() {
    if (!input) {
      return;
    }
    input.focus();
    input.select();
  }

  document.addEventListener('keydown', function (event) {
    var key = event.key;

    if ((key === 'k' || key === 'K') && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      focusSearch();
      return;
    }

    if (key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !isTypingTarget(event.target)) {
      event.preventDefault();
      focusSearch();
      return;
    }

    if (key === 'Escape') {
      if (input) {
        input.value = '';
      }
      hideResults();
      if (document.activeElement === input && input) {
        input.blur();
      }
      if (sidebar && sidebar.classList.contains('open')) {
        setSidebarOpen(false);
      }
    }
  });

  document.addEventListener('click', function (event) {
    /* 点击搜索面板外部 -> 隐藏结果 */
    if (searchBox && !searchBox.contains(event.target)) {
      hideResults();
    }
    /* 移动端：点击侧栏外部 -> 收起侧栏 */
    if (sidebar && sidebar.classList.contains('open') && !sidebar.contains(event.target)) {
      if (!navToggle || !navToggle.contains(event.target)) {
        setSidebarOpen(false);
      }
    }
  });

  if (sidebar) {
    /* 移动端点击条目后自动收起 */
    sidebar.addEventListener('click', function (event) {
      if (!event.target || !event.target.closest) {
        return;
      }
      if (event.target.closest('a.nav-link') && window.matchMedia('(max-width: 860px)').matches) {
        setSidebarOpen(false);
      }
    });
  }
})();
