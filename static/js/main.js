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
      await startJob('job_search', { query }, 'job_search');
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

  const poll = setInterval(async function () {
    try {
      const resp = await fetch(`/api/status/${jobId}`);
      const data = await resp.json();
      updateSpinnerMsg(data.message || '...');
      if (data.status === 'done') {
        clearInterval(poll);
        hideSpinner();
        window.location.href = `/article/${data.slug}`;
      } else if (data.status === 'error') {
        clearInterval(poll);
        hideSpinner();
        showToast(data.message || 'Generation failed.', 'error');
      }
    } catch (err) { /* keep polling */ }
  }, 1500);
}

// ── Job polling ────────────────────────────────────────────────────────────
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

  const poll = setInterval(async function () {
    try {
      const resp = await fetch(`/api/status/${jobId}`);
      const data = await resp.json();
      updateSpinnerMsg(data.message || '...');

      if (data.status === 'done') {
        clearInterval(poll);
        hideSpinner();
        if (jobType === 'research' && data.slug) {
          window.location.href = `/article/${data.slug}`;
        } else if (jobType === 'job_search') {
          window.location.href = `/jobs/results/${jobId}`;
        } else {
          window.location.reload();
        }
      } else if (data.status === 'error') {
        clearInterval(poll);
        hideSpinner();
        showToast(data.message || 'Research failed.', 'error');
      }
    } catch (err) {
      // Network hiccup — keep polling
    }
  }, 1500);
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
