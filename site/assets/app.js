// ─── Uniface Docs Viewer ───────────────────────────────────────────
// Loads scraped data, builds a Lunr search index (cached in localStorage),
// renders the TOC tree, and handles search + navigation.

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const els = {
    sidebar: $("#sidebar"),
    toc: $("#toc"),
    content: $("#content"),
    results: $("#results"),
    search: $("#search"),
    stats: $("#search-stats"),
    indexStatus: $("#indexing-status"),
    collapseAll: $("#collapse-all"),
    pageCount: $("#page-count"),
  };

  const state = {
    toc: null,            // hierarchical TOC
    docs: null,           // { id -> { title, breadcrumbs, html, url } }
    searchMeta: null,     // [{ id, title, breadcrumbs, text }]
    idx: null,            // Lunr index
    metaById: null,       // id -> meta entry
    selectedResult: 0,
    lastQuery: "",
  };

  // ─── Data loading ────────────────────────────────────────────────

  async function loadJSON(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`Failed to load ${path}: ${r.status}`);
    return r.json();
  }

  async function loadAllData() {
    const [toc, docs, searchMeta] = await Promise.all([
      loadJSON("assets/toc.json"),
      loadJSON("assets/docs.json"),
      loadJSON("assets/search-meta.json"),
    ]);
    state.toc = toc;
    state.docs = docs;
    state.searchMeta = searchMeta;
    state.metaById = Object.fromEntries(searchMeta.map(m => [m.id, m]));
    els.pageCount.textContent = searchMeta.length;
  }

  // ─── Lunr index (cached in localStorage) ─────────────────────────

  const INDEX_KEY = "uniface-lunr-idx-v2";
  const INDEX_HASH_KEY = "uniface-lunr-hash-v2";

  function corpusHash(corpus) {
    // Quick fingerprint: count + first/last ids
    if (!corpus.length) return "empty";
    return `${corpus.length}:${corpus[0].id}:${corpus[corpus.length - 1].id}`;
  }

  function buildLunrIndex(corpus) {
    return lunr(function () {
      // Multi-language: German + English fallback
      if (lunr.multiLanguage) {
        this.use(lunr.multiLanguage("en", "de"));
      }
      this.ref("id");
      this.field("title", { boost: 8 });
      this.field("breadcrumbs", { boost: 3 });
      this.field("text");
      this.metadataWhitelist = ["position"];
      corpus.forEach((d) => this.add(d));
    });
  }

  function loadOrBuildIndex() {
    const hash = corpusHash(state.searchMeta);
    const cachedHash = localStorage.getItem(INDEX_HASH_KEY);
    const cached = localStorage.getItem(INDEX_KEY);
    if (cachedHash === hash && cached) {
      try {
        return lunr.Index.load(JSON.parse(cached));
      } catch (e) {
        console.warn("cached index broken, rebuilding", e);
      }
    }
    const idx = buildLunrIndex(state.searchMeta);
    try {
      localStorage.setItem(INDEX_KEY, JSON.stringify(idx));
      localStorage.setItem(INDEX_HASH_KEY, hash);
    } catch (e) {
      // Quota — search will still work, just won't cache
      console.warn("localStorage quota; index not cached", e);
    }
    return idx;
  }

  // ─── TOC rendering ───────────────────────────────────────────────

  function renderToc() {
    const root = document.createElement("ul");
    state.toc.forEach((node) => root.appendChild(renderTocNode(node, 0)));
    els.toc.appendChild(root);
  }

  function renderTocNode(node, depth) {
    const li = document.createElement("li");
    li.className = "toc-node";
    li.dataset.id = node.id;
    if (node.children.length) li.dataset.hasChildren = "1";

    const row = document.createElement("div");
    row.className = "toc-row";

    const toggle = document.createElement("span");
    toggle.className = "toggle" + (node.children.length ? "" : " leaf");
    toggle.textContent = node.children.length ? "▶" : "·";
    row.appendChild(toggle);

    const link = document.createElement("a");
    link.className = "toc-link";
    link.href = `#${node.id}`;
    link.textContent = node.title;
    row.appendChild(link);

    li.appendChild(row);

    if (node.children.length) {
      const ul = document.createElement("ul");
      node.children.forEach((c) => ul.appendChild(renderTocNode(c, depth + 1)));
      li.appendChild(ul);
    }

    // Click on row (but not on the link itself) toggles expansion
    row.addEventListener("click", (e) => {
      if (e.target === link) return;
      if (node.children.length) {
        li.classList.toggle("expanded");
      } else {
        // Leaf — let the link handle it
        link.click();
      }
    });

    // Expand chevron click
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      if (node.children.length) li.classList.toggle("expanded");
    });

    return li;
  }

  function expandPathToId(id) {
    // Find the LI for this id and expand all ancestors
    const el = els.toc.querySelector(`li[data-id="${CSS.escape(id)}"]`);
    if (!el) return null;
    let cur = el.parentElement;
    while (cur && cur !== els.toc) {
      if (cur.tagName === "LI") cur.classList.add("expanded");
      cur = cur.parentElement;
    }
    return el;
  }

  function setActiveToc(id) {
    els.toc.querySelectorAll(".toc-row.active").forEach((e) => e.classList.remove("active"));
    const el = expandPathToId(id);
    if (el) {
      el.querySelector(".toc-row").classList.add("active");
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  // ─── Page rendering ──────────────────────────────────────────────

  function renderPage(id) {
    const doc = state.docs[id];
    if (!doc) {
      els.content.innerHTML = `<div class="welcome"><h1>Page not found</h1><p>No content for id <code>${id}</code>.</p></div>`;
      return;
    }
    els.results.hidden = true;
    els.content.hidden = false;

    const crumbs = doc.breadcrumbs || [];
    const crumbHtml = crumbs.map((c, i) =>
      `<span>${escapeHtml(c)}</span>${i < crumbs.length - 1 ? '<span class="sep">›</span>' : ""}`
    ).join("");

    els.content.innerHTML = `
      <div class="breadcrumbs">${crumbHtml}</div>
      <div class="content-body">${doc.html}</div>
      <div class="source-link">
        Source: <a href="${escapeAttr(doc.url)}" target="_blank" rel="noopener">${escapeHtml(doc.url)}</a>
      </div>
    `;

    els.content.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveToc(id);
    document.title = `${doc.title || crumbs[crumbs.length - 1] || "Doc"} — Uniface 10.4`;
  }

  // ─── Search ──────────────────────────────────────────────────────

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }
  function escapeAttr(s) { return escapeHtml(s); }

  function runSearch(qRaw) {
    const q = qRaw.trim();
    state.lastQuery = q;
    if (!q) {
      els.results.hidden = true;
      els.content.hidden = false;
      els.stats.textContent = "";
      return;
    }

    let hits = [];
    try {
      // Try wildcard fuzzy search if user didn't add operators
      const hasOp = /[+\-*~^:]/.test(q);
      const lunrQ = hasOp ? q : q.split(/\s+/).map(t => t.length > 2 ? `${t}* ${t}~1` : t).join(" ");
      hits = state.idx.search(lunrQ);
    } catch (e) {
      // Fall back to literal
      try { hits = state.idx.search(q); } catch (_) { hits = []; }
    }

    els.stats.textContent = `${hits.length} result${hits.length === 1 ? "" : "s"}`;
    renderResults(hits, q);
  }

  function renderResults(hits, query) {
    els.results.hidden = false;
    els.content.hidden = true;

    if (!hits.length) {
      els.results.innerHTML = `
        <div class="results-head">Search results · ${escapeHtml(query)}</div>
        <div class="result-empty">No matches. Try a shorter or different term.</div>
      `;
      return;
    }

    const terms = query.split(/\s+/).filter(t => t.length > 1);

    const itemsHtml = hits.slice(0, 50).map((hit, i) => {
      const meta = state.metaById[hit.ref];
      if (!meta) return "";
      const snippet = makeSnippet(meta.text, terms);
      const title = highlight(meta.title || "(untitled)", terms);
      const crumb = highlight(meta.breadcrumbs || "", terms);
      return `
        <div class="result${i === 0 ? " selected" : ""}" data-id="${escapeAttr(meta.id)}">
          <div class="result-crumbs">${crumb}</div>
          <div class="result-title">${title}</div>
          <div class="result-snippet">${snippet}</div>
        </div>
      `;
    }).join("");

    els.results.innerHTML = `
      <div class="results-head">Search results · ${escapeHtml(query)} · ${hits.length} match${hits.length === 1 ? "" : "es"}</div>
      ${itemsHtml}
    `;

    state.selectedResult = 0;
    els.results.querySelectorAll(".result").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.dataset.id;
        location.hash = id;
      });
    });
  }

  function makeSnippet(text, terms) {
    if (!text) return "";
    // Find the first term position
    const lower = text.toLowerCase();
    let pos = -1;
    for (const t of terms) {
      const i = lower.indexOf(t.toLowerCase());
      if (i >= 0 && (pos === -1 || i < pos)) pos = i;
    }
    const start = Math.max(0, pos - 80);
    const end = Math.min(text.length, (pos >= 0 ? pos : 0) + 220);
    let snippet = text.slice(start, end);
    if (start > 0) snippet = "… " + snippet;
    if (end < text.length) snippet = snippet + " …";
    return highlight(snippet, terms);
  }

  function highlight(s, terms) {
    if (!s) return "";
    let out = escapeHtml(s);
    terms.forEach(t => {
      if (t.length < 2) return;
      const re = new RegExp(`(${escapeRegex(t)})`, "gi");
      out = out.replace(re, '<mark class="hl">$1</mark>');
    });
    return out;
  }

  function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  // ─── Routing ─────────────────────────────────────────────────────

  function handleHashChange() {
    const id = location.hash.slice(1);
    if (id && state.docs[id]) {
      renderPage(id);
    }
  }

  // ─── Keyboard ────────────────────────────────────────────────────

  function bindKeys() {
    document.addEventListener("keydown", (e) => {
      // Slash to focus search (when not already in an input)
      if (e.key === "/" && document.activeElement !== els.search) {
        e.preventDefault();
        els.search.focus();
        els.search.select();
        return;
      }
      if (e.key === "Escape") {
        if (document.activeElement === els.search) {
          els.search.blur();
        }
        if (els.search.value) {
          els.search.value = "";
          runSearch("");
        }
        return;
      }
      // Result navigation
      if (!els.results.hidden) {
        const results = els.results.querySelectorAll(".result");
        if (!results.length) return;
        if (e.key === "ArrowDown") {
          e.preventDefault();
          state.selectedResult = Math.min(results.length - 1, state.selectedResult + 1);
          updateSelectedResult(results);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          state.selectedResult = Math.max(0, state.selectedResult - 1);
          updateSelectedResult(results);
        } else if (e.key === "Enter" && document.activeElement === els.search) {
          e.preventDefault();
          const id = results[state.selectedResult]?.dataset.id;
          if (id) location.hash = id;
        }
      }
    });
  }

  function updateSelectedResult(results) {
    results.forEach((el) => el.classList.remove("selected"));
    const el = results[state.selectedResult];
    if (el) {
      el.classList.add("selected");
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  // ─── Init ────────────────────────────────────────────────────────

  let searchTimer = null;
  function bindSearch() {
    els.search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => runSearch(els.search.value), 80);
    });
  }

  function bindCollapseAll() {
    els.collapseAll.addEventListener("click", () => {
      els.toc.querySelectorAll("li.toc-node.expanded").forEach((el) => el.classList.remove("expanded"));
    });
  }

  async function init() {
    try {
      els.indexStatus.textContent = "loading data…";
      await loadAllData();
      renderToc();

      els.indexStatus.textContent = "building index…";
      // Yield to paint so the user sees the TOC before index work
      await new Promise(r => setTimeout(r, 30));
      state.idx = loadOrBuildIndex();

      els.indexStatus.textContent = "ready";
      els.indexStatus.classList.add("ready");

      bindSearch();
      bindKeys();
      bindCollapseAll();
      window.addEventListener("hashchange", handleHashChange);
      handleHashChange();
    } catch (e) {
      console.error(e);
      els.indexStatus.textContent = "error";
      els.content.innerHTML = `
        <div class="welcome">
          <h1>Could not load data</h1>
          <p>Is <code>assets/toc.json</code>, <code>assets/docs.json</code>, and <code>assets/search-meta.json</code> present?</p>
          <p>Run the scraper and the build step first — see the README.</p>
          <pre>${escapeHtml(e.message)}</pre>
        </div>
      `;
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
