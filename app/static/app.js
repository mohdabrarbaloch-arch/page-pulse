/* ==========================================================================
   Page Pulse — dashboard client
   Vanilla JS, no build step. Talks to the FastAPI backend on the same origin.
   ========================================================================== */

(() => {
  'use strict';

  const TOKEN_KEY = 'pagepulse.token';
  const state = {
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: null,
    pages: [],
    activePageId: null,
    analysis: null,
    days: 180,
    charts: { engagement: null, followers: null },
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  /* ── Formatting ──────────────────────────────────────────────────────── */

  const nf = new Intl.NumberFormat('en-US');

  function num(value) {
    if (value === null || value === undefined) return '—';
    return nf.format(Math.round(value));
  }

  function compact(value) {
    if (value === null || value === undefined) return '—';
    const abs = Math.abs(value);
    if (abs >= 1e6) return (value / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (abs >= 1e3) return (value / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(Math.round(value));
  }

  function pct(value, digits = 2) {
    if (value === null || value === undefined) return '—';
    return `${Number(value).toFixed(digits)}%`;
  }

  function dateLabel(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function shortDate(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function initials(name) {
    return (name || '?')
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0].toUpperCase())
      .join('');
  }

  /* ── API ─────────────────────────────────────────────────────────────── */

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body !== undefined && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    if (state.token) headers.Authorization = `Bearer ${state.token}`;

    const response = await fetch(path, { ...options, headers });

    if (response.status === 204) return null;

    let payload = null;
    const text = await response.text();
    if (text) {
      try { payload = JSON.parse(text); } catch { payload = { detail: text }; }
    }

    if (!response.ok) {
      if (response.status === 401 && state.token) {
        signOut(false);
      }
      const detail = payload && payload.detail ? payload.detail : `Request failed (${response.status})`;
      const error = new Error(typeof detail === 'string' ? detail : 'Request failed');
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  /* ── Toast ───────────────────────────────────────────────────────────── */

  let toastTimer = null;
  function toast(message, isError = false) {
    const el = $('#toast');
    el.textContent = message;
    el.classList.toggle('toast--error', isError);
    el.classList.remove('is-hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add('is-hidden'), 3600);
  }

  /* ── Auth ────────────────────────────────────────────────────────────── */

  function showAuthError(message) {
    const el = $('#auth-error');
    el.textContent = message;
    el.classList.remove('is-hidden');
  }

  function setToken(token, user) {
    state.token = token;
    state.user = user;
    localStorage.setItem(TOKEN_KEY, token);
  }

  function signOut(notify = true) {
    state.token = '';
    state.user = null;
    state.pages = [];
    state.activePageId = null;
    state.analysis = null;
    localStorage.removeItem(TOKEN_KEY);
    destroyCharts();
    $('#app').classList.add('is-hidden');
    $('#auth-screen').classList.remove('is-hidden');
    if (notify) toast('Signed out.');
  }

  async function handleAuthSubmit(form, path) {
    const data = Object.fromEntries(new FormData(form).entries());
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    $('#auth-error').classList.add('is-hidden');
    try {
      const result = await api(path, { method: 'POST', body: JSON.stringify(data) });
      setToken(result.access_token, result.user);
      await enterApp();
    } catch (error) {
      showAuthError(error.message);
    } finally {
      button.disabled = false;
    }
  }

  /* ── Boot ────────────────────────────────────────────────────────────── */

  async function enterApp() {
    $('#auth-screen').classList.add('is-hidden');
    $('#app').classList.remove('is-hidden');
    await loadPages();
  }

  async function loadPages() {
    const result = await api('/api/pages');
    state.pages = result.pages || [];
    renderPageTabs();

    if (!state.pages.length) {
      state.activePageId = null;
      $('#empty-state').classList.remove('is-hidden');
      $('#dashboard').classList.add('is-hidden');
      $('#loading').classList.add('is-hidden');
      return;
    }

    $('#empty-state').classList.add('is-hidden');

    const stillThere = state.pages.some((p) => p.id === state.activePageId);
    if (!stillThere) state.activePageId = state.pages[0].id;
    await loadAnalysis();
  }

  function renderPageTabs() {
    const nav = $('#page-tabs');
    nav.innerHTML = '';
    if (state.pages.length < 2) return;

    state.pages.forEach((page) => {
      const button = document.createElement('button');
      button.className = 'page-tab' + (page.id === state.activePageId ? ' is-active' : '');
      button.textContent = page.name;
      button.addEventListener('click', async () => {
        if (page.id === state.activePageId) return;
        state.activePageId = page.id;
        renderPageTabs();
        await loadAnalysis();
      });
      nav.appendChild(button);
    });
  }

  async function loadAnalysis() {
    if (!state.activePageId) return;
    $('#loading').classList.remove('is-hidden');
    $('#dashboard').classList.add('is-hidden');
    try {
      const analysis = await api(
        `/api/pages/${state.activePageId}/analysis?days=${state.days}`
      );
      state.analysis = analysis;
      renderDashboard(analysis);
      $('#dashboard').classList.remove('is-hidden');
    } catch (error) {
      toast(error.message, true);
    } finally {
      $('#loading').classList.add('is-hidden');
    }
  }

  /* ── Rendering ───────────────────────────────────────────────────────── */

  function renderDashboard(a) {
    renderBanner(a);
    renderOverview(a);
    renderMetrics(a);
    renderInsights(a);
    renderMediaMix(a);
    renderSlots(a);
    renderPosts(a);
    renderCharts(a);

    $('#foot-note').textContent =
      `Generated ${new Date(a.generated_at).toLocaleString()} · ` +
      `window ${a.window_days} days · times reported in ${a.reporting_timezone}`;
  }

  function renderBanner(a) {
    const el = $('#source-banner');
    el.className = 'banner';
    if (a.data_source === 'live') el.classList.add('banner--live');
    if (a.data_source === 'import') el.classList.add('banner--import');
    el.textContent = a.data_source_note || '';
  }

  function renderOverview(a) {
    const page = a.page;
    $('#page-avatar').textContent = initials(page.name);
    $('#page-name').textContent = page.name;
    $('#page-category').textContent = page.category || 'Uncategorised';
    $('#page-verified').classList.toggle('is-hidden', !page.is_verified);
    $('#page-address').textContent = page.address || '';
    $('#page-description').textContent = page.description || '';

    $('#stat-followers').textContent = num(page.followers);
    $('#stat-likes').textContent = num(page.likes);
    $('#stat-talking').textContent = num(page.talking_about);
    $('#stat-posts').textContent = num(a.post_count);
  }

  function metricCard(label, value, note, delta) {
    const deltaHtml = delta
      ? `<div class="metric__delta metric__delta--${delta.dir}">${escapeHtml(delta.text)}</div>`
      : '';
    return `
      <article class="metric">
        <div class="metric__label">${escapeHtml(label)}</div>
        <div class="metric__value">${value}</div>
        ${deltaHtml}
        <div class="metric__note">${escapeHtml(note)}</div>
      </article>`;
  }

  function renderMetrics(a) {
    const e = a.engagement;
    const g = a.growth;
    const c = a.cadence;
    const cards = [];

    // Engagement rate — the note states the denominator and the sample it used.
    cards.push(
      metricCard(
        'Engagement rate',
        e.rate_percent === null ? '—' : pct(e.rate_percent),
        e.explained
      )
    );

    // Typical engagement per post.
    cards.push(
      metricCard(
        'Typical engagement',
        compact(e.median_engagement),
        `Median across ${num(e.post_count)} posts. Average is ${compact(e.mean_engagement)} — ` +
          `the median is shown because one viral post drags the mean.`
      )
    );

    // Posting cadence.
    const cadenceNote =
      c.consistency === 'bursty'
        ? `Longest gap ${c.longest_gap_days} days against a typical ${c.median_gap_days}.`
        : `Typical gap between posts: ${c.median_gap_days ?? '—'} days.`;
    cards.push(
      metricCard('Posts per week', c.posts_per_week.toFixed(1), cadenceNote)
    );

    // Follower growth.
    let delta = null;
    if (g.absolute_change !== null && g.absolute_change !== 0) {
      delta = {
        dir: g.absolute_change > 0 ? 'up' : 'down',
        text: `${g.absolute_change > 0 ? '+' : ''}${num(g.absolute_change)} followers`,
      };
    }
    cards.push(
      metricCard(
        'Followers',
        compact(g.current_followers),
        g.note,
        delta
      )
    );

    $('#metrics').innerHTML = cards.join('');
  }

  function renderInsights(a) {
    const container = $('#insights');
    if (!a.insights.length) {
      container.innerHTML = '<p class="card__sub">No findings for this window.</p>';
      return;
    }
    container.innerHTML = a.insights
      .map(
        (i) => `
        <article class="insight insight--${escapeHtml(i.severity)}">
          <div class="insight__bar"></div>
          <div>
            <div class="insight__title">${escapeHtml(i.title)}</div>
            <p class="insight__body">${escapeHtml(i.body)}</p>
            <span class="insight__tag">${escapeHtml(i.severity)}</span>
          </div>
        </article>`
      )
      .join('');
  }

  function renderMediaMix(a) {
    const container = $('#media-mix');
    if (!a.media_mix.length) {
      container.innerHTML = '<p class="card__sub">No posts in this window.</p>';
      return;
    }
    const max = Math.max(...a.media_mix.map((m) => m.median_engagement), 1);

    container.innerHTML = a.media_mix
      .map((m) => {
        const width = Math.max(2, (m.median_engagement / max) * 100);
        const flag = m.outperforms_share
          ? '<span class="mix__flag">earns more than it costs</span>'
          : '<span class="mix__flag mix__flag--low">underperforms its share</span>';
        const conf = `<span class="conf conf--${m.confidence}">${m.confidence}</span>`;
        return `
          <div class="mix__row">
            <div class="mix__top">
              <span class="mix__name">${escapeHtml(m.media_type)}</span>
              <span class="mix__val">${compact(m.median_engagement)} median</span>
            </div>
            <div class="mix__bar"><div class="mix__fill" style="width:${width}%"></div></div>
            <div class="mix__meta">
              <span>${m.post_count} posts · ${m.share_of_posts_percent}% of output · ${m.share_of_engagement_percent}% of engagement</span>
              ${conf}
            </div>
            <div class="mix__meta">${flag}</div>
          </div>`;
      })
      .join('');
  }

  function renderSlots(a) {
    const container = $('#slots');
    $('#slots-sub').textContent = `Local time (${a.reporting_timezone}). Four-hour bands.`;

    if (!a.best_slots.length) {
      container.innerHTML = '<p class="card__sub">Not enough posts to rank a time slot.</p>';
      return;
    }

    const anyRanked = a.best_slots.some((s) => s.confidence !== 'insufficient');
    if (!anyRanked) {
      container.insertAdjacentHTML(
        'beforeend',
        '<p class="card__sub" style="margin-bottom:8px">No slot has enough posts to rank yet — these are the busiest, not the best.</p>'
      );
    }

    container.innerHTML = a.best_slots
      .map(
        (s) => `
        <div class="slot">
          <div>
            <div class="slot__when">${escapeHtml(s.label)}</div>
            <div class="slot__meta">${s.post_count} posts · <span class="conf conf--${s.confidence}">${s.confidence} confidence</span></div>
          </div>
          <div class="slot__val">${compact(s.median_engagement)}</div>
        </div>`
      )
      .join('');
  }

  function renderPosts(a) {
    const body = $('#posts-body');
    const rows = [...a.top_posts, ...a.weakest_posts];
    const seen = new Set();
    const unique = rows.filter((p) => {
      if (seen.has(p.external_id)) return false;
      seen.add(p.external_id);
      return true;
    });

    if (!unique.length) {
      body.innerHTML = '<tr><td colspan="7" class="card__sub">No posts in this window.</td></tr>';
      return;
    }

    body.innerHTML = unique
      .map(
        (p) => `
        <tr>
          <td class="table__msg">
            <div>${escapeHtml(p.excerpt || '(no caption)')}</div>
            <div class="table__date">${dateLabel(p.posted_at)}</div>
          </td>
          <td><span class="type-pill type-pill--${escapeHtml(p.media_type)}">${escapeHtml(p.media_type)}</span></td>
          <td class="num">${num(p.likes)}</td>
          <td class="num">${num(p.comments)}</td>
          <td class="num">${num(p.shares)}</td>
          <td class="num"><strong>${num(p.engagement)}</strong></td>
          <td class="num">${p.engagement_rate_percent === null ? '—' : pct(p.engagement_rate_percent)}</td>
        </tr>`
      )
      .join('');
  }

  /* ── Charts ──────────────────────────────────────────────────────────── */

  function destroyCharts() {
    Object.keys(state.charts).forEach((key) => {
      if (state.charts[key]) {
        state.charts[key].destroy();
        state.charts[key] = null;
      }
    });
  }

  function chartTheme() {
    const styles = getComputedStyle(document.documentElement);
    return {
      text: styles.getPropertyValue('--text-dim').trim() || '#9aa7bf',
      grid: styles.getPropertyValue('--line-soft').trim() || '#1b2436',
      accent: styles.getPropertyValue('--accent').trim() || '#4f8cff',
      ok: styles.getPropertyValue('--ok').trim() || '#34d399',
    };
  }

  function renderCharts(a) {
    if (typeof Chart === 'undefined') {
      $('#chart-engagement-sub').textContent =
        'Chart library could not load (offline?). The numbers above are unaffected.';
      return;
    }

    destroyCharts();
    const theme = chartTheme();
    const labels = a.timeline.map((p) => shortDate(p.date));

    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = theme.text;

    const baseOptions = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(0,0,0,.85)',
          padding: 10,
          cornerRadius: 8,
          displayColors: false,
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { maxTicksLimit: 7, font: { size: 10 } },
        },
        y: {
          beginAtZero: true,
          grid: { color: theme.grid },
          ticks: { font: { size: 10 }, callback: (v) => compact(v) },
        },
      },
    };

    // Engagement per day.
    const engagementCanvas = $('#chart-engagement');
    if (engagementCanvas) {
      state.charts.engagement = new Chart(engagementCanvas, {
        type: 'bar',
        data: {
          labels,
          datasets: [
            {
              label: 'Engagement',
              data: a.timeline.map((p) => p.engagement),
              backgroundColor: theme.accent,
              borderRadius: 3,
              maxBarThickness: 14,
            },
          ],
        },
        options: baseOptions,
      });
      $('#chart-engagement-sub').textContent =
        `Total likes + comments + shares per day, across ${a.post_count} posts.`;
    }

    // Follower line. Only plotted where a snapshot exists, so the line does not
    // imply daily readings that were never taken.
    const followerCanvas = $('#chart-followers');
    if (followerCanvas) {
      const hasFollowers = a.timeline.some((p) => p.followers !== null);
      state.charts.followers = new Chart(followerCanvas, {
        type: 'line',
        data: {
          labels,
          datasets: [
            {
              label: 'Followers',
              data: a.timeline.map((p) => p.followers),
              borderColor: theme.ok,
              backgroundColor: 'transparent',
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 4,
              spanGaps: true,
              tension: 0.25,
            },
          ],
        },
        options: {
          ...baseOptions,
          scales: {
            ...baseOptions.scales,
            y: { ...baseOptions.scales.y, beginAtZero: false },
          },
        },
      });
      $('#chart-followers-sub').textContent = hasFollowers
        ? `Recorded snapshots only — ${a.growth.note}`
        : 'No follower history recorded for this page yet.';
    }
  }

  /* ── Add page modal ──────────────────────────────────────────────────── */

  async function openAddModal() {
    $('#add-modal').classList.remove('is-hidden');
    $('#add-error').classList.add('is-hidden');
    // The demo list is fetched, so show a placeholder rather than a blank panel.
    $('#demo-list').innerHTML = '<p class="card__sub">Loading sample pages…</p>';

    try {
      const health = await api('/health');
      const note = $('#live-note');
      const form = $('#live-form');
      if (health.graph_configured) {
        note.textContent =
          'A Facebook access token is configured. Enter the numeric page ID to pull live data.';
        form.classList.remove('is-hidden');
      } else {
        note.textContent =
          'No FB_GRAPH_TOKEN is set on the server, so live pages cannot be fetched. ' +
          'Add a page access token to .env and restart to enable this.';
        form.classList.add('is-hidden');
      }
    } catch {
      /* health is best-effort; the demo list still works */
    }

    try {
      const demos = await api('/api/pages/demo');
      $('#demo-list').innerHTML = demos
        .map(
          (d) => `
          <div class="demo-item">
            <div>
              <div class="demo-item__name">${escapeHtml(d.name)}</div>
              <div class="demo-item__meta">${escapeHtml(d.category)} · ${num(d.followers)} followers</div>
            </div>
            <button class="btn btn--primary" data-demo-slug="${escapeHtml(d.slug)}">Add</button>
          </div>`
        )
        .join('');
    } catch (error) {
      $('#demo-list').innerHTML = `<p class="card__sub">${escapeHtml(error.message)}</p>`;
    }
  }

  function closeAddModal() {
    $('#add-modal').classList.add('is-hidden');
  }

  async function addDemoPage(slug, button) {
    button.disabled = true;
    try {
      const page = await api('/api/pages/demo', {
        method: 'POST',
        body: JSON.stringify({ slug, days: 180 }),
      });
      closeAddModal();
      state.activePageId = page.id;
      await loadPages();
      toast(`Added ${page.name}.`);
    } catch (error) {
      const el = $('#add-error');
      el.textContent = error.message;
      el.classList.remove('is-hidden');
    } finally {
      button.disabled = false;
    }
  }

  /* ── Wiring ──────────────────────────────────────────────────────────── */

  function wire() {
    $$('[data-auth-tab]').forEach((tab) => {
      tab.addEventListener('click', () => {
        const target = tab.dataset.authTab;
        $$('[data-auth-tab]').forEach((t) => {
          const active = t === tab;
          t.classList.toggle('is-active', active);
          t.setAttribute('aria-selected', String(active));
        });
        $('#login-form').classList.toggle('is-hidden', target !== 'login');
        $('#register-form').classList.toggle('is-hidden', target !== 'register');
        $('#auth-error').classList.add('is-hidden');
      });
    });

    $('#login-form').addEventListener('submit', (e) => {
      e.preventDefault();
      handleAuthSubmit(e.target, '/api/auth/login');
    });

    $('#register-form').addEventListener('submit', (e) => {
      e.preventDefault();
      handleAuthSubmit(e.target, '/api/auth/register');
    });

    $('#signout-btn').addEventListener('click', () => signOut());

    $('#window-select').addEventListener('change', (e) => {
      state.days = Number(e.target.value);
      loadAnalysis();
    });

    $('#add-page-btn').addEventListener('click', openAddModal);
    $$('[data-open-add]').forEach((b) => b.addEventListener('click', openAddModal));
    $$('[data-close-modal]').forEach((b) => b.addEventListener('click', closeAddModal));

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeAddModal();
    });

    $('#demo-list').addEventListener('click', (e) => {
      const button = e.target.closest('[data-demo-slug]');
      if (button) addDemoPage(button.dataset.demoSlug, button);
    });

    $('#live-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(e.target).entries());
      const button = e.target.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        const page = await api('/api/pages/live', {
          method: 'POST',
          body: JSON.stringify({ external_id: data.external_id, days: 180 }),
        });
        closeAddModal();
        state.activePageId = page.id;
        await loadPages();
        toast(`Connected ${page.name}.`);
      } catch (error) {
        const el = $('#add-error');
        el.textContent = error.message;
        el.classList.remove('is-hidden');
      } finally {
        button.disabled = false;
      }
    });
  }

  async function boot() {
    wire();
    if (!state.token) return;
    try {
      state.user = await api('/api/auth/me');
      await enterApp();
    } catch {
      signOut(false);
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
