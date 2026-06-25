// ── Dark mode ──────────────────────────────────────────────────────────────
(function () {
  if (localStorage.getItem('theme') === 'dark') {
    document.documentElement.classList.add('dark-pre');
  }
})();

document.addEventListener('DOMContentLoaded', function () {
  // Apply saved theme
  if (localStorage.getItem('theme') === 'dark') {
    document.body.classList.add('dark');
  }

  const toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      document.body.classList.toggle('dark');
      const isDark = document.body.classList.contains('dark');
      localStorage.setItem('theme', isDark ? 'dark' : 'light');
      toggle.textContent = isDark ? '☀ Light' : '☾ Dark';
    });
    const isDark = document.body.classList.contains('dark');
    toggle.textContent = isDark ? '☀ Light' : '☾ Dark';
  }

  // ── Research form ────────────────────────────────────────────────────────
  const researchForm = document.getElementById('research-form');
  if (researchForm) {
    researchForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      const topic = document.getElementById('research-input').value.trim();
      if (!topic) return;
      await startJob('research', { topic }, 'research');
    });
  }

  // ── Job search form ──────────────────────────────────────────────────────
  const jobForm = document.getElementById('job-form');
  if (jobForm) {
    jobForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      const query = document.getElementById('job-input').value.trim();
      if (!query) return;
      const companyEl = document.getElementById('job-company');
      const company = companyEl ? companyEl.value.trim() : '';
      await startJob('job_search', { query, company }, 'job_search');
    });
  }

  // ── Generator form ───────────────────────────────────────────────────────
  const generatorForm = document.getElementById('generator-form');
  if (generatorForm) {
    generatorForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      const description = document.getElementById('generator-input').value.trim();
      const type = document.getElementById('generator-type').value;
      if (!description) return;
      await startGeneratorJob(type, description);
    });
  }
});

// ── SSE stream helper ──────────────────────────────────────────────────────
function watchJob(jobId, onDone, onError) {
  const es = new EventSource(`/api/stream/${jobId}`);

  es.onmessage = function (e) {
    let data;
    try { data = JSON.parse(e.data); } catch (_) { return; }
    updateSpinnerMsg(data.message || '...');
    if (data.status === 'done') {
      es.close();
      onDone(data);
    } else if (data.status === 'error') {
      es.close();
      onError(data.message || 'Job failed.');
    }
  };

  es.onerror = function () {
    es.close();
    onError('Connection lost. Please try again.');
  };
}

// ── Generator job ──────────────────────────────────────────────────────────
async function startGeneratorJob(type, description) {
  const endpoint = type === 'prompt' ? '/api/generate/prompt' : '/api/generate/skill';
  showSpinner('Generating ' + type + '...');

  let jobId;
  try {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description })
    });
    const data = await resp.json();
    if (data.error) { hideSpinner(); showToast(data.error, 'error'); return; }
    jobId = data.job_id;
  } catch (err) {
    hideSpinner();
    showToast('Network error. Try again.', 'error');
    return;
  }

  watchJob(jobId,
    function (data) { hideSpinner(); window.location.href = `/article/${data.slug}`; },
    function (msg)  { hideSpinner(); showToast(msg, 'error'); }
  );
}

// ── Job start + stream ─────────────────────────────────────────────────────
async function startJob(type, payload, jobType) {
  const endpoint = type === 'research' ? '/api/research' : '/api/jobs/search';
  showSpinner('Starting...');

  let jobId;
  try {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await resp.json();
    if (data.error) { hideSpinner(); showToast(data.error, 'error'); return; }
    jobId = data.job_id;
  } catch (err) {
    hideSpinner();
    showToast('Network error. Try again.', 'error');
    return;
  }

  watchJob(jobId,
    function (data) {
      hideSpinner();
      if (jobType === 'research' && data.slug) {
        window.location.href = `/article/${data.slug}`;
      } else if (jobType === 'job_search') {
        window.location.href = `/jobs/results/${jobId}`;
      } else {
        window.location.reload();
      }
    },
    function (msg) { hideSpinner(); showToast(msg, 'error'); }
  );
}

// ── Spinner helpers ────────────────────────────────────────────────────────
function showSpinner(msg) {
  const overlay = document.getElementById('spinner-overlay');
  if (overlay) {
    overlay.classList.add('active');
    updateSpinnerMsg(msg);
  }
}

function hideSpinner() {
  const overlay = document.getElementById('spinner-overlay');
  if (overlay) overlay.classList.remove('active');
}

function updateSpinnerMsg(msg) {
  const el = document.getElementById('spinner-msg');
  if (el) el.textContent = msg;
}

// ── Article search ─────────────────────────────────────────────────────────
(function () {
  const searchInput = document.getElementById('article-search');
  if (!searchInput) return;

  let debounceTimer;

  searchInput.addEventListener('input', function () {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => runArticleSearch(this.value.trim()), 300);
  });

  async function runArticleSearch(q) {
    const grid = document.getElementById('article-grid');
    const emptySearch = document.getElementById('articles-empty-search');
    const countEl = document.getElementById('article-count');
    if (!grid) return;

    if (!q) {
      // Restore all cards
      Array.from(grid.children).forEach(c => c.style.display = '');
      if (emptySearch) emptySearch.style.display = 'none';
      grid.style.display = '';
      if (countEl) countEl.textContent = grid.children.length;
      return;
    }

    try {
      const resp = await fetch('/api/search/articles?q=' + encodeURIComponent(q));
      const articles = await resp.json();
      renderArticleCards(articles, grid, emptySearch, countEl);
    } catch (err) {
      // On network error leave current view unchanged
    }
  }

  function renderArticleCards(articles, grid, emptySearch, countEl) {
    if (articles.length === 0) {
      grid.style.display = 'none';
      if (emptySearch) emptySearch.style.display = '';
      if (countEl) countEl.textContent = '0';
      return;
    }
    grid.style.display = '';
    if (emptySearch) emptySearch.style.display = 'none';
    if (countEl) countEl.textContent = articles.length;
    grid.innerHTML = articles.map(art => `
      <div class="card article-card">
        <div class="card-topic">${esc(art.topic || 'Research').slice(0, 80)}</div>
        <h3>${esc(art.title)}</h3>
        <div class="card-meta">
          <span>${esc(art.created_at.slice(0, 10))}</span>
          ${art.word_count ? `<span class="meta-sep">·</span><span>${art.word_count} words</span>` : ''}
        </div>
        <a href="/article/${esc(art.slug)}" class="btn btn-primary btn-read">Read Article</a>
      </div>
    `).join('');
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();

// ── Toast notification ─────────────────────────────────────────────────────
function showToast(msg, type) {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position:fixed; bottom:20px; right:20px; z-index:9999;
    padding:12px 18px; border-radius:8px; font-size:0.875rem;
    background:${type === 'error' ? '#ef4444' : '#10b981'}; color:#fff;
    box-shadow:0 4px 12px rgba(0,0,0,0.2);
    animation: fadeIn 0.2s ease;
  `;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}
