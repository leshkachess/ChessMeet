const tg = window.Telegram?.WebApp;
const initialParams = new URLSearchParams(location.search);
const fragmentParams = new URLSearchParams(location.hash.replace(/^#/, ''));
const fallbackAuthToken = fragmentParams.get('auth') || initialParams.get('auth') || '';
if (fallbackAuthToken) {
  const cleanUrl = new URL(location.href);
  cleanUrl.searchParams.delete('auth');
  cleanUrl.hash = '';
  history.replaceState({}, '', `${cleanUrl.pathname}${cleanUrl.search}${cleanUrl.hash}`);
}
if (tg) {
  tg.ready();
  tg.expand();
  tg.BackButton?.onClick?.(() => goBack());
}

const state = {
  config: null,
  me: null,
  games: [],
  my: { created: [], responded: [], pending_reviews: [] },
  screen: initialParams.get('screen') || 'home',
  activeChatGameId: initialParams.get('game') || null,
  chat: { game: null, opponent: null, messages: [] },
  dailyPuzzle: null,
  profilePhotoDraft: '',
  publicProfile: null,
  selectedPlace: null,
  map: null,
  marker: null,
  puzzleSelectedSquare: null,
  puzzleLastMove: null,
  countdownInterval: null,
  gamesQuery: '',
  gamesFormat: 'all',
  gamesLevel: 'all',
  gamesDate: 'all',
  gamesBoard: 'all',
  responseGameId: null,
  responsesPanelGameId: null,
  gameResponses: [],
  cityStats: null,
  cityPlaces: [],
  userLocation: null,
  showGameHistory: false,
  editingGameId: null,
  draftNoticeHidden: false,
};

const app = document.getElementById('app');


const APP_VERSION = '1.5.1';
const OFFICIAL_BOT_USERNAME = 'chessmeetbot';
const CACHE_PREFIX = 'chessmeet_v0121_';
const AUTO_REFRESH_MS = 15000;
let autoRefreshTimer = null;
let hydrateInFlight = false;

function cacheKey(key) {
  return `${CACHE_PREFIX}${key}`;
}

function readCache(key, maxAgeMs = 10 * 60 * 1000) {
  try {
    const raw = localStorage.getItem(cacheKey(key));
    if (!raw) return null;
    const item = JSON.parse(raw);
    if (!item || item.version !== APP_VERSION) return null;
    if (Date.now() - Number(item.ts || 0) > maxAgeMs) return null;
    return item.data;
  } catch (_) {
    return null;
  }
}

function writeCache(key, data) {
  try {
    localStorage.setItem(cacheKey(key), JSON.stringify({ version: APP_VERSION, ts: Date.now(), data }));
  } catch (_) {}
}

function hydrateFromCache() {
  const cached = readCache('bootstrap', 30 * 60 * 1000);
  if (!cached) return false;
  state.config = cached.config || state.config;
  state.me = cached.user || state.me;
  state.games = cached.games || state.games || [];
  state.my = cached.my || state.my || { created: [], responded: [], pending_reviews: [] };
  state.dailyPuzzle = cached.daily_puzzle || state.dailyPuzzle;
  if (state.me) applyTheme(state.me.theme_mode || 'light');
  return true;
}

function selectedCity() {
  return state.me?.profile_city || state.config?.default_city || 'Минск';
}

function currentCity() {
  return encodeURIComponent(selectedCity());
}

function cityCatalog() {
  return state.config?.cities || [{ name: 'Минск', country: 'BY', timezone: 'Europe/Minsk' }];
}

function selectedCityInfo() {
  return cityCatalog().find(city => city.name === selectedCity()) || cityCatalog()[0];
}

function cityOptions(selected = selectedCity()) {
  const groups = [
    ['BY', 'Беларусь'],
    ['RU', 'Россия'],
  ];
  return groups.map(([country, label]) => {
    const options = cityCatalog().filter(city => city.country === country);
    if (!options.length) return '';
    return `<optgroup label="${label}">${options.map(city =>
      `<option value="${h(city.name)}" ${city.name === selected ? 'selected' : ''}>${h(city.name)}</option>`
    ).join('')}</optgroup>`;
  }).join('');
}

function resolveTheme(mode) {
  const selected = mode || 'light';
  if (selected === 'dark') return 'dark';
  if (selected === 'system') {
    const tgDark = tg?.colorScheme === 'dark';
    const mediaDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    return (tgDark || mediaDark) ? 'dark' : 'light';
  }
  return 'light';
}

function applyTheme(mode) {
  const resolved = resolveTheme(mode);
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeMode = mode || 'light';
  document.body.dataset.theme = resolved;
}

function currentThemeMode() {
  return state.me?.theme_mode || 'light';
}

function telegramLanguage() {
  return window.ChessMeetI18n.normalize(tg?.initDataUnsafe?.user?.language_code || 'en');
}

function currentLanguage() {
  return window.ChessMeetI18n.normalize(
    state.me?.ui_language || localStorage.getItem(cacheKey('ui_language')) || telegramLanguage()
  );
}

function tr(text) {
  return window.ChessMeetI18n.text(String(text ?? ''), currentLanguage());
}

const STATUS_LABELS = {
  open: 'Открыта',
  pending: 'Есть отклики',
  confirmed: 'Подтверждена',
  cancelled: 'Отменена',
  expired: 'Истекла',
  completed: 'Завершена',
};

const STATUS_TONES = {
  open: 'blue',
  pending: 'amber',
  confirmed: 'green',
  completed: 'violet',
  cancelled: 'muted',
  expired: 'muted',
};

const PIECES = {
  K: '♔', Q: '♕', R: '♖', B: '♗', N: '♘', P: '♙',
  k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟',
};

function h(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function safeMapUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:' && ['openstreetmap.org', 'www.openstreetmap.org'].includes(url.hostname)
      ? url.toString()
      : '';
  } catch (_) {
    return '';
  }
}

function distanceKm(latitude, longitude) {
  if (!state.userLocation) return null;
  const lat2 = Number(latitude), lon2 = Number(longitude);
  if (!Number.isFinite(lat2) || !Number.isFinite(lon2)) return null;
  const toRad = value => value * Math.PI / 180;
  const lat1 = state.userLocation.latitude, lon1 = state.userLocation.longitude;
  const dLat = toRad(lat2 - lat1), dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function initials(name) {
  return (name || 'И').trim().slice(0, 1).toUpperCase();
}

function avatar(user, cls = 'avatar') {
  const photo = user?.photo_data_url;
  if (photo) return `<img class="${cls} avatar-img" src="${photo}" alt="avatar" />`;
  return `<div class="${cls}">${initials(user?.display_name || user?.first_name || user?.username)}</div>`;
}

function ratingText(user) {
  const count = Number(user?.rating_count || 0);
  if (!count) return 'без оценок';
  return `${Number(user?.rating_avg || 0).toFixed(1)} ★ · ${count}`;
}

function reliabilityGames(user) {
  return Number(user?.reliability?.games_count || user?.games_count || 0);
}

function profileBadges(user) {
  const badges = [];
  const streak = Number(user?.puzzle_streak || 0);
  const solved = Number(user?.puzzle_solved_count || 0);
  const games = reliabilityGames(user);
  const ratingCount = Number(user?.rating_count || 0);
  const ratingAvg = Number(user?.rating_avg || 0);

  if (streak >= 3) badges.push({ icon: '🔥', label: `Серия ${streak}` });
  if (games >= 1) badges.push({ icon: '♟', label: `${games} партий` });
  if (solved >= 5) badges.push({ icon: '🧩', label: `${solved} задач` });
  if (!badges.length) badges.push({ icon: '🌱', label: 'Новый игрок' });

  return `<div class="badge-row">${badges.slice(0, 4).map(b => `<span class="mini-badge"><b>${b.icon}</b>${h(b.label)}</span>`).join('')}</div>`;
}

function customBadgeRow(badges = []) {
  if (!badges.length) return '';
  return `<div class="custom-badges">${badges.map(b => `
    <span class="custom-badge" style="--badge-color:${h(b.color || '#2f8a4b')}">
      <b>${h(b.icon || '🏅')}</b><span>${h(b.title || 'Значок')}</span>
    </span>
  `).join('')}</div>`;
}

function ownedBadgesSection(user) {
  const badges = user?.badges || [];
  if (!badges.length) {
    return `<section class="flow-card"><div class="step-label">Значки</div><p class="muted-copy">Пока нет выданных значков. Когда администратор выдаст значок, он появится здесь.</p></section>`;
  }
  return `
    <form id="badges-form" class="flow-card">
      <div class="step-label">Мои значки</div>
      <p class="muted-copy">Выбери, какие значки показывать в публичном профиле.</p>
      <div class="badge-settings-list">
        ${badges.map(b => `
          <label class="badge-toggle">
            <span class="custom-badge" style="--badge-color:${h(b.color || '#2f8a4b')}"><b>${h(b.icon || '🏅')}</b><span>${h(b.title || 'Значок')}</span></span>
            <input type="checkbox" name="visible_badge_ids" value="${b.id}" ${b.is_visible ? 'checked' : ''} />
          </label>
        `).join('')}
      </div>
      <button class="big-primary" type="submit">Сохранить значки</button>
    </form>
  `;
}

function showToast(message) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = tr(message);
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2600);
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const initData = tg?.initData || '';
  if (initData) headers['X-Telegram-Init-Data'] = initData;
  else if (fallbackAuthToken) headers['X-ChessMeet-Auth'] = fallbackAuthToken;
  const res = await fetch(path, { ...options, headers });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => item.msg || String(item)).join(', ')
      : data.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Ошибка запроса');
  }
  return data;
}

async function bootstrap() {
  try {
    applyTheme(localStorage.getItem(cacheKey('theme_mode')) || 'light');
    if (!tg?.initData && !fallbackAuthToken) {
      for (let attempt = 0; attempt < 15 && !tg?.initData; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }
    if (!tg?.initData && !fallbackAuthToken) {
      let config = {};
      try {
        const response = await fetch('/api/config');
        if (response.ok) config = await response.json();
      } catch (_) {}
      renderTelegramLaunch(OFFICIAL_BOT_USERNAME);
      return;
    }
    const hadCache = hydrateFromCache();
    if (hadCache) render();

    const boot = await api('/api/bootstrap');
    state.config = boot.config || state.config;
    state.me = boot.user || state.me;
    state.games = boot.games || [];
    state.my = boot.my || { created: [], responded: [], pending_reviews: [] };
    state.dailyPuzzle = boot.daily_puzzle || state.dailyPuzzle;
    state.profilePhotoDraft = state.me?.photo_data_url || '';
    applyTheme(state.me?.theme_mode || 'light');
    if (state.me?.ui_language) localStorage.setItem(cacheKey('ui_language'), state.me.ui_language);
    else localStorage.removeItem(cacheKey('ui_language'));
    localStorage.setItem(cacheKey('theme_mode'), state.me?.theme_mode || 'light');
    writeCache('bootstrap', boot);
    render();
    afterRender();
    startAutoRefresh();
    hydrate({ silent: true });
  } catch (err) {
    if (hydrateFromCache()) {
      showToast('Показаны сохранённые данные. Обновление не удалось.');
      render();
      afterRender();
      return;
    }
    app.innerHTML = `<main class="app-shell"><div class="content"><div class="notice-card danger">Не удалось загрузить приложение.<br>${h(err.message)}</div></div></main>`;
  }
}

async function hydrate({ silent = false } = {}) {
  if (hydrateInFlight) return;
  hydrateInFlight = true;
  const tasks = [];
  if (['home', 'games'].includes(state.screen)) tasks.push(loadGames());
  if (['home', 'games'].includes(state.screen)) tasks.push(loadCityStats());
  if (state.screen === 'home') tasks.push(loadCityPlaces());
  if (['home', 'my'].includes(state.screen)) tasks.push(loadMy());
  if (['home', 'profile', 'puzzle'].includes(state.screen)) tasks.push(loadDailyPuzzle());
  if (state.screen === 'chat' && state.activeChatGameId) tasks.push(loadChat(state.activeChatGameId));
  try { await Promise.all(tasks); } catch (err) { if (!silent) showToast(err.message); }
  hydrateInFlight = false;
  render();
  afterRender();
}

async function loadGames() {
  const city = currentCity();
  const cached = readCache(`games_${city}`, 5 * 60 * 1000);
  if (cached && (!state.games || !state.games.length)) state.games = cached;
  const data = await api(`/api/games?city=${city}`);
  state.games = data.games || [];
  writeCache(`games_${city}`, state.games);
}

async function loadMy() {
  const cached = readCache('my', 5 * 60 * 1000);
  if (cached && (!state.my || (!state.my.created?.length && !state.my.responded?.length))) state.my = cached;
  state.my = await api('/api/my');
  writeCache('my', state.my);
}

async function loadDailyPuzzle() {
  const cacheId = `daily_puzzle_${currentCity()}`;
  const cached = readCache(cacheId, 30 * 60 * 1000);
  if (cached && !state.dailyPuzzle) state.dailyPuzzle = cached;
  state.dailyPuzzle = await api('/api/daily-puzzle');
  writeCache(cacheId, state.dailyPuzzle);
}

async function loadCityStats() {
  state.cityStats = await api(`/api/cities/${currentCity()}/stats`);
}

async function loadCityPlaces() {
  const data = await api(`/api/cities/${currentCity()}/places`);
  state.cityPlaces = data.places || [];
}

async function loadChat(gameId) {
  const cached = readCache(`chat_${gameId}`, 2 * 60 * 1000);
  if (cached && (!state.chat?.messages || !state.chat.messages.length)) state.chat = cached;
  const data = await api(`/api/games/${gameId}/chat`);
  state.chat = { game: data.game, opponent: data.opponent, messages: data.messages || [] };
  writeCache(`chat_${gameId}`, state.chat);
}

function startAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    if (document.hidden) return;
    if (['home', 'games', 'my', 'puzzle', 'chat'].includes(state.screen)) hydrate({ silent: true });
  }, AUTO_REFRESH_MS);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && ['home', 'games', 'my', 'puzzle', 'chat'].includes(state.screen)) hydrate({ silent: true });
});

function navigate(screen, { scrollTop = true } = {}) {
  state.screen = screen;
  trackEvent('screen_view', { screen, city: selectedCity() });
  const url = new URL(location.href);
  url.searchParams.set('screen', screen);
  if (screen !== 'chat') url.searchParams.delete('game');
  history.pushState({ screen }, '', url.toString());
  render();
  if (scrollTop) requestAnimationFrame(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
    document.querySelector('.content')?.scrollTo?.({ top: 0, left: 0, behavior: 'auto' });
  });
  hydrate();
}

function goBack() {
  if (history.state?.screen && history.length > 1) history.back();
  else if (state.screen !== 'home') navigate('home', { scrollTop: false });
}

window.addEventListener('popstate', () => {
  state.screen = new URLSearchParams(location.search).get('screen') || 'home';
  render();
  hydrate({ silent: true });
});

function afterRender() {
  if (state.screen === 'create') initCreateMap();
  if (state.screen === 'chat') scrollChatDown();
  ensureCountdown();
  updateCountdown();
}

function destroyMapIfNeeded(force = false) {
  if (state.map && (force || state.screen !== 'create')) {
    try { state.map.remove(); } catch (_) {}
    state.map = null;
    state.marker = null;
  }
}

function shell(content) {
  return `
    <main class="app-shell">
      ${topbar()}
      <section class="content">${content}</section>
      ${bottomNav()}
      ${responseModal()}
      ${responsesManagerModal()}
    </main>
  `;
}

function topbar() {
  const title = {
    home: 'ChessMeet', games: 'Партии рядом', create: 'Создать партию', my: 'Мои партии', puzzle: 'Задача дня', profile: 'Профиль', user: 'Игрок', chat: 'Чат'
  }[state.screen] || 'ChessMeet';
  const city = selectedCity();
  return `
    <header class="topbar-v7">
      ${state.screen !== 'home' ? '<button class="screen-back" type="button" data-back aria-label="Назад">←</button>' : ''}
      <div>
        <div class="brand-row"><span class="brand-mark">♜</span><span>ChessMeet</span><span class="version-pill">v1.5.1</span></div>
        <h1>${title}</h1>
        <p>${city} · офлайн-шахматы в Telegram</p>
        <label class="city-filter"><span>Город</span><select id="city-filter-select">${cityOptions(city)}</select></label>
      </div>
      <button class="avatar-btn" data-nav="profile">${avatar(state.me, 'top-avatar')}</button>
    </header>
  `;
}

function navIcon(name) {
  const common = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
  const icons = {
    home: `<svg ${common}><path d="M4 11.5 12 4l8 7.5"/><path d="M6.8 10.5V20h10.4v-9.5"/><path d="M10 20v-5h4v5"/></svg>`,
    games: `<svg ${common}><rect x="4" y="4" width="16" height="16" rx="3.5"/><path d="M4 12h16"/><path d="M12 4v16"/><path d="M8 8h.01"/><path d="M16 8h.01"/><path d="M8 16h.01"/><path d="M16 16h.01"/></svg>`,
    puzzle: `<svg ${common}><path d="M12 3.5 20.5 12 12 20.5 3.5 12 12 3.5Z"/><path d="M12 8v4l2.5 2.5"/><path d="M12 16h.01"/></svg>`,
    my: `<svg ${common}><path d="M7 4h10a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-3 2-3-2V6a2 2 0 0 1 2-2Z"/><path d="M8 9h8"/><path d="M8 13h6"/></svg>`,
    profile: `<svg ${common}><circle cx="12" cy="8" r="3.25"/><path d="M5.5 20a6.5 6.5 0 0 1 13 0"/></svg>`,
  };
  return icons[name] || icons.home;
}

function bottomNav() {
  const items = [
    ['home', 'home', 'Дом'],
    ['games', 'games', 'Игры'],
    ['puzzle', 'puzzle', 'Задача'],
    ['my', 'my', 'Мои'],
    ['profile', 'profile', 'Профиль'],
  ];
  return `
    <nav class="bottom-nav-v7">
      ${items.map(([key, icon, label]) => `
        <button class="nav-item ${state.screen === key ? 'active' : ''}" data-nav="${key}">
          <span class="nav-icon">${navIcon(icon)}</span><small>${label}</small>
        </button>
      `).join('')}
    </nav>
  `;
}

function homeScreen() {
  const streak = state.dailyPuzzle?.stats?.streak ?? state.me?.puzzle_streak ?? 0;
  const nextGame = [...(state.my.created || []), ...(state.my.responded || [])].find(g => g.status === 'confirmed');
  return `
    <section class="home-hero">
      <div class="hero-copy">
        <p>Живая партия вместо онлайн-очереди</p>
        <h2>Найди соперника и сыграй за настоящей доской</h2>
      </div>
      <button class="hero-cta" data-nav="create">+ Создать партию</button>
    </section>

    <section class="city-dashboard">
      <div><small>Сообщество города</small><strong>${h(selectedCity())}</strong></div>
      <div class="city-stat-row">
        ${metric('игроков', state.cityStats?.players ?? '—')}
        ${metric('ищут партию', state.cityStats?.open_games ?? state.games.length)}
        ${metric('встреч создано', state.cityStats?.matched_games ?? '—')}
      </div>
    </section>

    ${onboardingCard()}

    <section class="quick-grid home-quick-grid">
      ${quickAction('games', 'games', 'Найти партию', `Список заявок: ${selectedCity()}`)}
      ${quickAction('my', 'my', 'Мои встречи', nextGame ? `${nextGame.place}` : 'Отклики, чаты, оценки')}
      ${quickAction('puzzle', 'puzzle', 'Задача дня', `Серия ${streak} 🔥`)}
      ${quickAction('profile', 'profile', 'Профиль', ratingText(state.me))}
    </section>

    ${state.cityPlaces.length ? `<section class="section-head-v7"><div><h3>Популярные места</h3><p>Здесь уже играют в ${h(selectedCity())}</p></div></section><div class="places-strip">${state.cityPlaces.slice(0, 6).map(placeCard).join('')}</div>` : ''}

    <section class="section-head-v7">
      <div><h3>Ближайшие заявки</h3><p>Новые партии, на которые можно откликнуться</p></div>
      <button data-nav="games">Все</button>
    </section>
    <div class="card-list">${state.games.slice(0, 3).map(gameCard).join('') || emptyCityState()}</div>
  `;
}

function placeCard(place) {
  const url = safeMapUrl(place.map_url);
  return `<article class="place-card"><span>♟</span><strong>${h(place.place)}</strong><small>${h(place.address || selectedCity())}</small><div>${place.rating_count ? `${Number(place.rating_avg || 0).toFixed(1)} ★` : 'Новое место'} · ${Number(place.games_count || 0)} партий</div>${url ? `<a href="${h(url)}" target="_blank" rel="noopener noreferrer">На карте</a>` : ''}</article>`;
}

function responsesManagerModal() {
  if (!state.responsesPanelGameId) return '';
  return `<div class="modal-backdrop" data-close-responses>
    <section class="response-modal" data-modal-panel role="dialog" aria-modal="true" aria-label="Отклики">
      <button class="modal-close" type="button" data-close-responses>×</button>
      <div class="step-label">Кандидаты</div><h2>Отклики на партию</h2>
      <div class="candidate-list">${state.gameResponses.map(item => {
        const user = item.responder || {};
        return `<article class="candidate-card">${userStrip(user)}
          <div class="candidate-proposal"><b>${h(item.proposed_date_label || 'Дата без изменений')} · ${h(item.proposed_time_label || 'время без изменений')}</b><p>${h(item.proposed_comment || 'Без комментария')}</p></div>
          ${item.status === 'pending' ? `<div class="game-actions"><button class="ghost-action danger-text" data-decline-response="${item.id}">Отклонить</button><button class="primary-action" data-accept-response="${item.id}">Принять</button></div>` : `<span class="status muted">${h(item.status)}</span>`}
        </article>`;
      }).join('') || '<p class="muted-copy">Откликов пока нет.</p>'}</div>
    </section>
  </div>`;
}

function responseModal() {
  const game = state.games.find(item => Number(item.id) === Number(state.responseGameId));
  if (!game) return '';
  return `<div class="modal-backdrop" data-close-response>
    <section class="response-modal" data-modal-panel role="dialog" aria-modal="true" aria-label="Отклик на партию">
      <button class="modal-close" type="button" data-close-response>×</button>
      <div class="step-label">Отклик на партию</div>
      <h2>${h(game.place)}</h2>
      <p class="muted-copy">${h(game.date_label)} · ${h(game.time_label)} · ${h(game.game_format)}</p>
      <form id="response-form" data-game-id="${game.id}">
        <div class="two-cols"><label>Предложить дату<input type="date" name="proposed_date_label" value="${h(game.date_label || '')}" /></label><label>Время<input type="time" name="proposed_time_label" value="${h(game.time_label || '')}" /></label></div>
        <label>Сообщение<textarea name="proposed_comment" maxlength="300" placeholder="Например: буду с доской, могу на 15 минут позже">Могу в это время</textarea></label>
        <div class="response-templates">
          <button type="button" data-response-template="Могу в это время">В это время</button>
          <button type="button" data-response-template="Могу на 15 минут позже">+15 минут</button>
          <button type="button" data-response-template="Буду с доской">Буду с доской</button>
        </div>
        <button class="big-primary" type="submit">Отправить отклик</button>
      </form>
    </section>
  </div>`;
}

function emptyCityState() {
  return `<section class="empty-city-card"><div class="empty-city-icon">♟</div><h3>Стань первым в городе ${h(selectedCity())}</h3><p>Создай заявку или включи уведомления — сообщим, когда появится подходящая партия.</p><button class="big-primary" data-nav="create">Создать первую заявку</button><button class="ghost-wide" data-enable-city-alerts>Сообщать о новых партиях</button></section>`;
}

function metric(label, value) {
  return `<article class="metric-card"><strong>${h(value)}</strong><span>${h(label)}</span></article>`;
}

function quickAction(screen, icon, title, subtitle) {
  return `<button class="quick-card" data-nav="${screen}"><span class="quick-icon">${navIcon(icon)}</span><span>${h(title)}</span><small>${h(subtitle)}</small></button>`;
}

function userStrip(user, extra = '') {
  const tgId = user?.telegram_id;
  const isMe = state.me && tgId && Number(tgId) === Number(state.me.telegram_id);
  const tag = tgId ? 'button' : 'div';
  const action = tgId ? (isMe ? 'data-nav="profile"' : `data-view-profile="${tgId}"`) : '';
  return `
    <${tag} class="user-strip user-strip-clickable" ${action}>
      ${avatar(user)}
      <div>
        <strong>${h(user?.display_name || 'Игрок')}</strong>
        <small>${h(user?.level || 'Уровень не указан')} · ${h(ratingText(user))}${extra ? ` · ${h(extra)}` : ''}</small>
      </div>
    </${tag}>
  `;
}

function gameCard(game) {
  const mine = state.me && Number(game.creator_telegram_id) === Number(state.me.telegram_id);
  const tone = STATUS_TONES[game.status] || 'muted';
  const distance = distanceKm(game.latitude, game.longitude);
  return `
    <article class="game-card">
      <div class="game-card-top">
        ${userStrip(game.creator)}
        <span class="status ${tone}">${STATUS_LABELS[game.status] || game.status}</span>
      </div>
      <div class="game-title">${h(game.place)}</div>
      <div class="game-meta">
        <span>📍 ${h(game.area || game.city || 'Минск')}</span>
        <span>🗓 ${h(game.date_label)} ${game.is_flexible ? `${h(game.time_window_start || game.time_label)}–${h(game.time_window_end || '')}` : h(game.time_label)}</span>
        <span>⏱ ${h(game.game_format)}</span>
        ${distance !== null ? `<span>↗ ${distance < 10 ? distance.toFixed(1) : Math.round(distance)} км</span>` : ''}
        <span>${game.has_board ? '♟ Доска есть' : '♟ Нужна доска'}</span>
      </div>
      ${game.place_rating?.count ? `<div class="place-rating-line">📍 Место: ${Number(game.place_rating.avg || 0).toFixed(1)}★ · ${game.place_rating.count}</div>` : ''}
      ${game.match_score ? `<div class="success-strip">Совпадение ${game.match_score}% · ${h((game.match_reasons || []).join(', '))}</div>` : ''}
      ${game.waitlist_available ? `<div class="note-strip">Партия занята · в очереди: ${Number(game.waitlist_count || 0)}</div>` : ''}
      ${game.comment ? `<p class="game-comment">${h(game.comment)}</p>` : ''}
      <div class="game-actions">
        ${safeMapUrl(game.map_url) ? `<a class="ghost-action" href="${h(safeMapUrl(game.map_url))}" target="_blank" rel="noopener noreferrer">Карта</a>` : ''}
        <button class="ghost-action" data-view-profile="${game.creator?.telegram_id || game.creator_telegram_id}">Профиль</button>
        ${mine ? `<button class="primary-action" data-nav="my">Моя заявка</button>` : game.waitlist_available
          ? (game.my_waitlist_position
            ? `<button class="ghost-action" data-leave-waitlist="${game.id}">В очереди №${game.my_waitlist_position}</button>`
            : `<button class="primary-action" data-join-waitlist="${game.id}">Встать в очередь</button>`)
          : `<button class="primary-action" data-respond="${game.id}">Откликнуться</button>`}
      </div>
    </article>
  `;
}

function gamesScreen() {
  const q = state.gamesQuery.trim().toLowerCase();
  const openCount = state.games.filter(g => g.status === 'open').length;
  let filtered = state.games.filter(g => {
    const text = `${g.place} ${g.area} ${g.address} ${g.game_format} ${g.level}`.toLowerCase();
    const qOk = !q || text.includes(q);
    const fOk = state.gamesFormat === 'all' || String(g.game_format || '').toLowerCase().includes(state.gamesFormat);
    const levelOk = state.gamesLevel === 'all' || String(g.level || '') === state.gamesLevel;
    const boardOk = state.gamesBoard === 'all' || (state.gamesBoard === 'yes' ? Boolean(g.has_board) : !g.has_board);
    const today = new Date();
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
    const dateKey = String(g.date_label || '').slice(0, 10);
    const dateOk = state.gamesDate === 'all'
      || (state.gamesDate === 'today' && dateKey === today.toISOString().slice(0, 10))
      || (state.gamesDate === 'tomorrow' && dateKey === tomorrow.toISOString().slice(0, 10));
    return qOk && fOk && levelOk && boardOk && dateOk;
  });
  if (state.userLocation) {
    filtered = [...filtered].sort((a, b) => (distanceKm(a.latitude, a.longitude) ?? Infinity) - (distanceKm(b.latitude, b.longitude) ?? Infinity));
  }
  return `
    <section class="games-summary-card">
      <div>
        <span>Открытые</span>
        <strong>${openCount}</strong>
      </div>
      <button data-nav="create">+ Создать</button>
    </section>
    <section class="tool-card">
      <button class="location-button ${state.userLocation ? 'active' : ''}" data-use-location>${state.userLocation ? '✓ Сначала ближайшие' : '◎ Показать ближайшие ко мне'}</button>
      <label class="search-field"><span>⌕</span><input id="games-search" placeholder="Поиск по месту, району, формату" value="${h(state.gamesQuery)}" /></label>
      <div class="chip-row">
        ${filterChip('all', 'Все')}${filterChip('блиц', 'Блиц')}${filterChip('рапид', 'Рапид')}${filterChip('классика', 'Классика')}
      </div>
      <div class="advanced-filters">
        <select id="games-date-filter"><option value="all">Любая дата</option><option value="today" ${state.gamesDate === 'today' ? 'selected' : ''}>Сегодня</option><option value="tomorrow" ${state.gamesDate === 'tomorrow' ? 'selected' : ''}>Завтра</option></select>
        <select id="games-level-filter"><option value="all">Любой уровень</option>${['Новичок','Средний','Сильный любитель','Любой'].map(x => `<option ${state.gamesLevel === x ? 'selected' : ''}>${x}</option>`).join('')}</select>
        <select id="games-board-filter"><option value="all">Любая доска</option><option value="yes" ${state.gamesBoard === 'yes' ? 'selected' : ''}>Доска есть</option><option value="no" ${state.gamesBoard === 'no' ? 'selected' : ''}>Нужна доска</option></select>
      </div>
    </section>
    <div class="card-list">${filtered.map(gameCard).join('') || (state.games.length ? empty('Под такие фильтры заявок пока нет.') : emptyCityState())}</div>
  `;
}

function filterChip(value, label) {
  return `<button class="filter-chip ${state.gamesFormat === value ? 'active' : ''}" data-filter-format="${value}">${label}</button>`;
}


function gameToFormDefaults(game) {
  return {
    city: game?.city || state.me?.profile_city || 'Минск',
    place: game?.place || '',
    area: game?.area || '',
    address: game?.address || '',
    latitude: game?.latitude ?? null,
    longitude: game?.longitude ?? null,
    map_url: game?.map_url || '',
    date_label: game?.date_label || new Date().toISOString().slice(0, 10),
    time_label: game?.time_label || '18:30',
    is_flexible: Boolean(game?.is_flexible),
    time_window_start: game?.time_window_start || '18:00',
    time_window_end: game?.time_window_end || '21:00',
    game_format: game?.game_format || 'Рапид 10+5',
    level: game?.level || 'Средний',
    has_board: game ? Boolean(game.has_board) : true,
    comment: game?.comment || '',
  };
}

function getCreateDraft() {
  try { return JSON.parse(localStorage.getItem('chessmeet_create_draft') || 'null') || null; } catch (_) { return null; }
}

function setCreateDraft(data) {
  try { localStorage.setItem('chessmeet_create_draft', JSON.stringify(data)); } catch (_) {}
}

function clearCreateDraft() {
  try { localStorage.removeItem('chessmeet_create_draft'); } catch (_) {}
}

function createPayloadFromForm(form) {
  const fd = new FormData(form);
  const date = fd.get('date_label');
  const time = fd.get('time_label');
  return {
    city: fd.get('city'), place: fd.get('place'), area: fd.get('area'), address: fd.get('address') || state.selectedPlace?.address || '', place_id: '',
    latitude: state.selectedPlace?.latitude ?? null, longitude: state.selectedPlace?.longitude ?? null, map_url: state.selectedPlace?.map_url || '',
    date_label: date, time_label: time, scheduled_at: date && time ? `${date}T${time}:00Z` : null,
    is_flexible: fd.get('is_flexible') === 'on', time_window_start: fd.get('time_window_start') || '', time_window_end: fd.get('time_window_end') || '',
    game_format: fd.get('game_format'), level: fd.get('level'), has_board: fd.get('has_board') === 'on', comment: fd.get('comment') || '',
  };
}

function previewText(payload, isEdit = false) {
  const when = payload.is_flexible ? `${payload.date_label} ${payload.time_window_start || payload.time_label}–${payload.time_window_end || ''}` : `${payload.date_label} ${payload.time_label}`;
  return `${isEdit ? 'Проверить изменения' : 'Проверить заявку'}:\n\n📍 ${payload.place}\n${payload.address ? `📌 ${payload.address}\n` : ''}🗓 ${when}\n♟ ${payload.game_format}\n🎯 ${payload.level}\n${payload.comment ? `💬 ${payload.comment}\n` : ''}\n${isEdit ? 'Сохранить изменения?' : 'Опубликовать заявку?'}`;
}

function onboardingCard() {
  if (localStorage.getItem('chessmeet_onboarding_seen') === '1') return '';
  return `
    <section class="onboarding-card">
      <div class="step-label">Как это работает</div>
      <ol>
        <li>Создай заявку или откликнись на чужую.</li>
        <li>Автор принимает отклик — чат открывается сразу.</li>
        <li>После партии оставь отзыв, фото или запись в дневник.</li>
      </ol>
      <button class="ghost-wide" data-close-onboarding>Понятно</button>
    </section>
  `;
}

function communityRulesCard() {
  return `
    <section class="flow-card rules-card"><div class="step-label">Правила сообщества</div>
      <ul>
        <li>Встречайтесь только в публичных местах.</li>
        <li>Не передавайте личные данные незнакомым людям.</li>
        <li>Если планы изменились — отмените заявку заранее.</li>
        <li>После партии оставляйте честный отзыв и no-show только по делу.</li>
        <li>Будьте уважительны: ChessMeet — для живых партий, а не спама.</li>
      </ul>
    </section>
  `;
}

function createScreen() {
  const editing = state.editingGameId ? [...(state.my.created || []), ...(state.my.responded || [])].find(g => Number(g.id) === Number(state.editingGameId)) : null;
  const draft = !editing ? getCreateDraft() : null;
  const defaults = gameToFormDefaults(editing || draft || null);
  if (!state.selectedPlace && (defaults.latitude || defaults.longitude || defaults.map_url)) {
    state.selectedPlace = { latitude: defaults.latitude, longitude: defaults.longitude, map_url: defaults.map_url, address: defaults.address, place: defaults.place, area: defaults.area };
  }
  return `
    ${!editing && draft && !state.draftNoticeHidden ? `<section class="draft-card"><div><b>Есть черновик заявки</b><small>Форма восстановлена автоматически.</small></div><button class="ghost-action" data-clear-draft>Очистить</button></section>` : ''}
    <form id="create-form" class="flow-form" data-editing-id="${editing ? editing.id : ''}">
      <section class="flow-card"><div class="step-label">1 · Когда</div>
        <div class="two-cols"><label>Дата<input type="date" name="date_label" value="${h(defaults.date_label)}" required /></label><label>Время<input type="time" name="time_label" value="${h(defaults.time_label)}" required /></label></div>
        <label class="toggle-row"><span>Гибкое время<small>Например: свободен с 18:00 до 21:00</small></span><input type="checkbox" name="is_flexible" ${defaults.is_flexible ? 'checked' : ''} /></label>
        <div class="two-cols"><label>С<input type="time" name="time_window_start" value="${h(defaults.time_window_start)}" /></label><label>До<input type="time" name="time_window_end" value="${h(defaults.time_window_end)}" /></label></div>
        <div class="two-cols"><label>Формат<select name="game_format">${['Блиц 5+3','Рапид 10+5','Классика','Свободная игра'].map(x => `<option ${defaults.game_format === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label><label>Уровень<select name="level">${['Новичок','Средний','Сильный любитель','Любой'].map(x => `<option ${defaults.level === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label></div>
      </section>
      <section class="flow-card"><div class="step-label">2 · Где</div>
        <label>Город<select name="city" required>${cityOptions(defaults.city || selectedCity())}</select></label>
        <label>Место<input name="place" value="${h(state.selectedPlace?.place || defaults.place || '')}" placeholder="Кафе, парк, клуб" required /></label>
        <label>Район / ориентир<input name="area" value="${h(state.selectedPlace?.area || defaults.area || '')}" placeholder="Немига, центр, Восток" /></label>
        <label>Адрес<input name="address" value="${h(state.selectedPlace?.address || defaults.address || '')}" placeholder="Можно поставить точку на карте" /></label>
        <div id="create-map" class="map-v7"></div>
        ${safeMapUrl(state.selectedPlace?.map_url) ? `<div class="map-picked">✓ Точка выбрана · <a href="${h(safeMapUrl(state.selectedPlace.map_url))}" target="_blank" rel="noopener noreferrer">открыть карту</a></div>` : `<div class="map-hint">Нажми на карту, чтобы сохранить точную точку встречи.</div>`}
      </section>
      <section class="flow-card"><div class="step-label">3 · Детали</div>
        <label class="toggle-row"><span>У меня есть доска<small>Если выключено — ищешь соперника с доской</small></span><input type="checkbox" name="has_board" ${defaults.has_board ? 'checked' : ''} /></label>
        <label>Комментарий<textarea name="comment" placeholder="Например: могу играть около часа, без рейтинга, дружеская партия.">${h(defaults.comment)}</textarea></label>
        <div class="tip-card">Совет: выбирай публичное место — кафе, парк, библиотеку или клуб.</div>
        <button class="big-primary" type="submit">${editing ? 'Сохранить изменения' : 'Проверить и опубликовать'}</button>
        ${editing ? `<button type="button" class="ghost-wide" data-cancel-edit>Отменить редактирование</button>` : ''}
      </section>
    </form>
  `;
}

function myScreen() {
  const all = [...(state.my.created || []), ...(state.my.responded || [])];
  const waitlisted = state.my.waitlisted || [];
  const reviews = state.my.pending_reviews || [];
  const reviewIds = new Set(reviews.map(g => Number(g.id)));
  const confirmed = uniqueById(all.filter(g => g.status === 'confirmed' && !reviewIds.has(Number(g.id))));
  const waiting = uniqueById(all.filter(g => ['open', 'pending'].includes(g.status)));
  const history = uniqueById(all.filter(g => ['completed', 'cancelled', 'expired'].includes(g.status)));
  const diary = uniqueById(all.filter(g => g.my_diary));
  return `
    ${reviews.length ? sectionBlock('Ждут оценки', reviews.map(myGameCard).join('')) : ''}
    ${waitlisted.length ? sectionBlock('Лист ожидания', waitlisted.map(myGameCard).join('')) : ''}
    ${sectionBlock('Подтверждённые встречи', confirmed.map(myGameCard).join('') || empty('Пока нет подтверждённых встреч.'))}
    ${sectionBlock('Ожидают действия', waiting.map(myGameCard).join('') || empty('Активных ожиданий нет.'))}
    ${collapsibleHistoryBlock(history, diary)}
  `;
}

function collapsibleHistoryBlock(history, diary) {
  const total = history.length + diary.length;
  if (!state.showGameHistory) {
    return `
      <section class="section-block history-collapsed">
        <button class="history-toggle-card" data-toggle-history="1">
          <span><strong>История партий</strong><small>${total ? `${total} записей` : 'пока пусто'}</small></span>
          <span class="history-chevron">Показать</span>
        </button>
      </section>
    `;
  }
  return `
    <section class="section-block history-expanded">
      <div class="history-expanded-head">
        <h3>История партий</h3>
        <button class="ghost-action" data-toggle-history="0">Скрыть</button>
      </div>
      <div class="card-list">
        ${history.map(myGameCard).join('') || empty('История пока пустая.')}
      </div>
      <div class="history-diary-title">Шахматный дневник</div>
      <div class="card-list">
        ${diary.map(diaryCard).join('') || empty('Пока нет записей в дневнике. После партии можно добавить результат и заметки.')}
      </div>
    </section>
  `;
}

function uniqueById(items) {
  const seen = new Set();
  return items.filter(item => !seen.has(item.id) && seen.add(item.id));
}

function sectionBlock(title, html) {
  return `<section class="section-block"><h3>${h(title)}</h3><div class="card-list">${html}</div></section>`;
}

function diaryCard(game) {
  const d = game.my_diary || {};
  return `
    <article class="game-card diary-card">
      <div class="game-card-top"><span class="status violet">Дневник</span><span class="muted">#${game.id}</span></div>
      <div class="game-title">${h(game.place)}</div>
      <div class="game-meta"><span>🗓 ${h(game.date_label)} ${h(game.time_label)}</span><span>🏁 ${h(d.result || 'результат не указан')}</span></div>
      ${d.notes ? `<p class="game-comment">${h(d.notes)}</p>` : ''}
    </article>
  `;
}

function myGameCard(game) {
  const isCreator = Number(game.creator_telegram_id) === Number(state.me.telegram_id);
  const isResponder = game.accepted_response && Number(game.accepted_response.responder_telegram_id) === Number(state.me.telegram_id);
  const opponent = game.opponent;
  const canCancel = ['open', 'pending', 'confirmed'].includes(game.status);
  const canChat = ['confirmed', 'completed'].includes(game.status);
  const myCheckedIn = isCreator ? game.creator_checked_in : (isResponder ? game.responder_checked_in : false);
  const afterGameActionsAvailable = Boolean(game.rating_can_submit);
  return `
    <article class="game-card my-card">
      <div class="game-card-top"><span class="status ${STATUS_TONES[game.status] || 'muted'}">${STATUS_LABELS[game.status] || game.status}</span><span class="muted">#${game.id}</span></div>
      <div class="game-title">${h(game.place)}</div>
      ${opponent ? `<div class="opponent-panel">${userStrip(opponent)}</div>` : ''}
      <div class="game-meta"><span>🗓 ${h(game.date_label)} ${game.is_flexible ? `${h(game.time_window_start || game.time_label)}–${h(game.time_window_end || '')}` : h(game.time_label)}</span><span>⏱ ${h(game.game_format)}</span><span>${game.status === 'confirmed' ? 'Чат открыт' : 'Ожидание'}</span></div>
      ${game.status === 'confirmed' ? `<div class="checkin-strip"><span>${game.creator_checked_in ? `✓ Автор ${game.creator_late_minutes ? `опаздывает на ${game.creator_late_minutes} мин.` : 'на месте'}` : 'Автор ещё не отметился'} · ${game.responder_checked_in ? `✓ Соперник ${game.responder_late_minutes ? `опаздывает на ${game.responder_late_minutes} мин.` : 'на месте'}` : 'Соперник ещё не отметился'}</span>${!myCheckedIn ? `<div class="checkin-actions"><button class="primary-action" data-check-in="${game.id}" data-late-minutes="0">Я на месте</button><button class="ghost-action" data-check-in="${game.id}" data-late-minutes="10">Опоздаю на 10 мин.</button></div>` : `<b>✓ Ты отметился</b>`}</div>` : ''}
      ${game.my_rating ? `<div class="success-strip">Твоя оценка: ${game.my_rating.score} ★</div>` : ''}
      ${afterGameActionsAvailable ? `<div class="after-game-panel"><div class="after-game-title">После партии</div>${!game.my_rating && opponent ? ratingForm(game.id) : ''}${!game.my_place_rating ? placeRatingForm(game.id) : `<div class="success-strip">Оценка места: ${game.my_place_rating.score} ★</div>`}<div class="diary-actions"><button class="ghost-action" data-diary-game="${game.id}">${game.my_diary ? 'Изменить дневник' : 'Добавить в дневник'}</button></div><label class="photo-upload-chip">📷 Фото с партии<input type="file" accept="image/*" data-game-photo="${game.id}" /></label><div class="game-actions wrap">${!game.no_show_target_id ? `<button class="ghost-action danger-text" data-no-show="${game.id}">Не пришёл</button>` : ''}${opponent ? `<button class="ghost-action danger-text" data-report-user="${opponent.telegram_id}" data-report-game="${game.id}">Жалоба</button>` : ''}</div></div>` : ''}
      ${game.rating_available_at && !afterGameActionsAvailable ? `<div class="note-strip">Отзыв, фото, жалоба и no-show откроются через час после встречи.</div>` : ''}
      ${game.no_show_target_id ? `<div class="note-strip danger-note">Отмечен no-show по этой партии.</div>` : ''}
      ${renderGamePhotos(game)}
      <div class="game-actions wrap">
        ${isCreator && game.status === 'pending' ? `<button class="primary-action" data-manage-responses="${game.id}">Отклики (${Number(game.pending_responses_count || 0)})</button>` : ''}
        ${canChat ? `<button class="primary-action" data-open-chat="${game.id}">Чат</button>` : ''}
        ${game.status === 'confirmed' ? `<button class="ghost-action" data-add-calendar="${game.id}">В календарь</button>` : ''}
        ${isCreator && ['open','pending'].includes(game.status) && !game.accepted_response_id ? `<button class="ghost-action" data-edit-game="${game.id}">Редактировать</button>` : ''}
        ${canChat ? `<button class="ghost-action" data-rematch="${game.id}">Реванш</button>` : ''}
        ${safeMapUrl(game.map_url) ? `<a class="ghost-action" href="${h(safeMapUrl(game.map_url))}" target="_blank" rel="noopener noreferrer">Карта</a>` : ''}
        ${canCancel ? `<button class="ghost-action danger-text" data-cancel-game="${game.id}">Отменить</button>` : ''}
      </div>
    </article>
  `;
}

function renderGamePhotos(game) {
  const photos = game.photos || [];
  if (!photos.length) return '';
  return `<div class="game-photos">${photos.map(p => `<img src="${p.photo_data_url}" alt="game photo" />`).join('')}</div>`;
}

function ratingForm(gameId) {
  return `
    <form class="rating-form" data-rate-game="${gameId}">
      <label>Оценка соперника<select name="score"><option value="5">5 — отлично</option><option value="4">4 — хорошо</option><option value="3">3 — нормально</option><option value="2">2 — слабовато</option><option value="1">1 — плохо</option></select></label>
      <label>Комментарий<textarea name="comment" placeholder="Короткий отзыв"></textarea></label>
      <button class="big-primary" type="submit">Поставить оценку</button>
    </form>
  `;
}

function placeRatingForm(gameId) {
  return `
    <form class="rating-form place-rating-form" data-place-rate-game="${gameId}">
      <label>Оценка места<select name="score"><option value="5">5 — отлично</option><option value="4">4 — хорошо</option><option value="3">3 — нормально</option><option value="2">2 — так себе</option><option value="1">1 — неудобно</option></select></label>
      <label>Комментарий о месте<textarea name="comment" placeholder="Тихо? Удобно? Хорошие столы?"></textarea></label>
      <button class="ghost-wide" type="submit">Оценить место</button>
    </form>
  `;
}

function puzzleScreen() {
  const d = state.dailyPuzzle;
  if (!d) return empty('Загружаю задачу...');
  const p = d.puzzle || {};
  const stats = d.stats || {};
  const solved = d.solved;
  return `
    <section class="puzzle-hero ${solved ? 'solved' : ''}">
      <div><span class="status ${solved ? 'green' : 'amber'}">${solved ? 'Решено сегодня' : 'Мат в 1'}</span><h2>${h(p.title || 'Задача дня')}</h2><p>${h(p.description || 'Сделай ход прямо на доске.')}</p></div>
      <div class="timer-card"><small>Следующая</small><strong id="puzzle-countdown">--:--:--</strong><span id="puzzle-timezone-label">00:00 · ${h(selectedCity())}</span></div>
    </section>
    <section class="puzzle-board-card">
      ${renderPuzzleBoard(p.fen || '')}
      <div class="puzzle-feedback ${solved ? 'ok' : ''}">${solved ? `Верно${p.solution_san ? `: ${h(p.solution_san)}` : ''}. Серия ${stats.streak || 0} 🔥` : 'Выбери фигуру, затем клетку назначения.'}</div>
    </section>
    <section class="metric-grid puzzle-stats">
      ${metric('Серия', `${stats.streak || 0} 🔥`)}${metric('Рекорд', stats.best_streak || 0)}${metric('Всего решено', stats.solved_count || 0)}${metric('Сегодня', p.date || '—')}
    </section>
  `;
}

function sideToMove(fen) { return (fen.split(' ')[1] || 'w') === 'b' ? 'b' : 'w'; }
function isOwnPiece(piece, side) { return !!piece && (side === 'w' ? piece === piece.toUpperCase() : piece === piece.toLowerCase()); }

function fenToMap(fen) {
  const map = {};
  const board = (fen.split(' ')[0] || '').split('/');
  for (let r = 0; r < 8; r++) {
    let file = 0;
    for (const ch of board[r] || '') {
      if (/\d/.test(ch)) file += Number(ch);
      else {
        const square = `${'abcdefgh'[file]}${8 - r}`;
        map[square] = ch;
        file++;
      }
    }
  }
  return map;
}

function renderPuzzleBoard(fen) {
  const board = fenToMap(fen);
  const side = sideToMove(fen);
  const files = side === 'b' ? 'hgfedcba'.split('') : 'abcdefgh'.split('');
  const ranks = side === 'b' ? [1,2,3,4,5,6,7,8] : [8,7,6,5,4,3,2,1];
  let cells = '';
  for (const rank of ranks) {
    for (const file of files) {
      const sq = `${file}${rank}`;
      const piece = board[sq] || '';
      const light = ((file.charCodeAt(0) - 97) + rank) % 2 === 1;
      const selected = state.puzzleSelectedSquare === sq;
      const moved = state.puzzleLastMove && (state.puzzleLastMove.slice(0,2) === sq || state.puzzleLastMove.slice(2,4) === sq);
      cells += `<button class="sq ${light ? 'light' : 'dark'} ${selected ? 'selected' : ''} ${moved ? 'moved' : ''}" data-puzzle-square="${sq}" data-puzzle-piece="${piece}"><span class="piece ${piece === piece.toUpperCase() ? 'white' : 'black'}">${PIECES[piece] || ''}</span></button>`;
    }
  }
  return `<div class="chessboard-v7">${cells}</div>`;
}

function profileScreen() {
  const me = state.me || {};
  const referral = me.referral || {};
  const nextReferralTier = referral.next_tier;
  return `
    <section class="profile-head">
      ${avatar({ ...me, photo_data_url: state.profilePhotoDraft || me.photo_data_url }, 'profile-avatar')}
      <h2>${h(me.display_name || 'Игрок')}</h2>
      <p>${h(me.level || 'Средний')} · ${h(me.profile_city || 'Минск')}</p>
      <div class="profile-metrics"><span>${ratingText(me)}</span><span>${me.puzzle_streak || 0} 🔥</span><span>${me.puzzle_solved_count || 0} задач</span></div>
      ${profileBadges(me)}
      ${customBadgeRow((me.badges || []).filter(b => b.is_visible))}
    </section>
    <section class="flow-card language-card">
      <div><div class="step-label">Язык приложения</div><small>Изменение применяется и сохраняется сразу</small></div>
      <div class="language-switch" role="group" aria-label="Язык приложения">
        <button type="button" data-set-language="ru" class="${currentLanguage() === 'ru' ? 'active' : ''}">RU</button>
        <button type="button" data-set-language="en" class="${currentLanguage() === 'en' ? 'active' : ''}">EN</button>
      </div>
    </section>
    <section class="flow-card invite-card">
      <div class="step-label">Реферальная программа</div>
      <div class="referral-tier"><span>Твой уровень</span><strong>${h(referral.tier || 'Новичок')}</strong></div>
      <p>Приглашай друзей в ChessMeet. За каждого друга, который создаст заявку или откликнется на партию, ты получишь 10 очков.</p>
      <div class="referral-metrics">
        <div><strong>${Number(referral.registered || 0)}</strong><small>перешли</small></div>
        <div><strong>${Number(referral.activated || 0)}</strong><small>активны</small></div>
        <div><strong>${Number(referral.points || 0)}</strong><small>очков</small></div>
      </div>
      ${nextReferralTier ? `<div class="referral-progress"><span style="width:${Math.min(100, Math.round((Number(referral.activated || 0) / nextReferralTier.required) * 100))}%"></span></div><small>До уровня «${h(nextReferralTier.name)}»: ${nextReferralTier.remaining}</small>` : '<div class="success-strip">Достигнут максимальный уровень программы</div>'}
      <button class="big-primary" data-share-invite>Поделиться приглашением</button>
      <button class="ghost-wide" data-copy-invite>Скопировать ссылку</button>
      ${(referral.recent || []).length ? `<div class="referral-list">${referral.recent.map(item => `<div><span>${h(item.display_name)}</span><small>${item.status === 'activated' ? '✓ активен · +10' : 'ожидает первого действия'}</small></div>`).join('')}</div>` : ''}
    </section>
    ${ownedBadgesSection(me)}
    ${communityRulesCard()}
    <form id="profile-form" class="flow-form">
      <section class="flow-card"><div class="step-label">Публичный профиль</div>
        <label>Фото<input type="file" id="profile-photo-input" accept="image/*" /></label>
        <label>Публичное имя<input name="display_name" value="${h(me.display_name || '')}" required /></label>
        <div class="two-cols"><label>Город<select name="profile_city">${cityOptions(me.profile_city || selectedCity())}</select></label><label>Уровень<select name="level">${['Новичок','Средний','Сильный любитель','Тренер / профи'].map(x => `<option ${me.level === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label></div>
        <label>О себе<textarea name="bio" placeholder="Коротко о себе">${h(me.bio || '')}</textarea></label>
        <label class="toggle-row"><span>Показывать Telegram username<small>По умолчанию скрыт</small></span><input type="checkbox" name="show_telegram_username" ${me.show_telegram_username ? 'checked' : ''} /></label>
      </section>
      <section class="flow-card"><div class="step-label">Оформление</div>
        <label>Тема приложения
          <select name="theme_mode" id="theme-mode-select">
            <option value="light" ${(me.theme_mode || 'light') === 'light' ? 'selected' : ''}>Светлая</option>
            <option value="dark" ${me.theme_mode === 'dark' ? 'selected' : ''}>Тёмная</option>
            <option value="system" ${me.theme_mode === 'system' ? 'selected' : ''}>Системная</option>
          </select>
        </label>
        <div class="theme-preview">
          <span>♜</span><div><b>По умолчанию — светлая</b><small>Можно переключить на тёмную или системную тему.</small></div>
        </div>
      </section>
      <section class="flow-card"><div class="step-label">Уведомления</div>
        <label class="toggle-row"><span>Напоминания о партиях<small>За 3 часа и за 30 минут до встречи</small></span><input type="checkbox" name="notify_game_reminders" ${me.notify_game_reminders !== false ? 'checked' : ''} /></label>
        <label class="toggle-row"><span>Новые заявки<small>Когда другой игрок публикует партию в твоём городе</small></span><input type="checkbox" name="notify_new_requests" ${me.notify_new_requests === true ? 'checked' : ''} /></label>
        <div class="two-cols">
          <label>Формат уведомлений<select name="subscription_format">${[['all','Все форматы'],['блиц','Блиц'],['рапид','Рапид'],['классика','Классика']].map(([value,label]) => `<option value="${value}" ${(me.subscription_format || 'all') === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
          <label>Уровень соперника<select name="subscription_level">${['all','Новичок','Средний','Сильный любитель','Тренер / профи'].map(value => `<option value="${value}" ${(me.subscription_level || 'all') === value.toLowerCase() ? 'selected' : ''}>${value === 'all' ? 'Любой уровень' : value}</option>`).join('')}</select></label>
        </div>
        <label class="toggle-row"><span>Продление серии<small>В 21:00 по времени выбранного города, если задача дня ещё не решена</small></span><input type="checkbox" name="notify_puzzle_streak" ${me.notify_puzzle_streak !== false ? 'checked' : ''} /></label>
        <button class="big-primary" type="submit">Сохранить профиль</button>
      </section>
    </form>
  `;
}

function publicProfileScreen() {
  const u = state.publicProfile;
  if (!u) return empty('Профиль не загружен.');
  return `
    <section class="profile-head public-profile">
      ${avatar(u, 'profile-avatar')}
      <h2>${h(u.display_name)}</h2>
      <p>${h(u.level || 'Уровень не указан')} · ${h(u.profile_city || 'Минск')}</p>
      <div class="profile-metrics"><span>${ratingText(u)}</span><span>${u.puzzle_streak || 0} 🔥</span><span>${u.puzzle_solved_count || 0} задач</span></div>
      <div class="profile-metrics"><span>Сыграно: ${u.reliability?.games_count || 0}</span><span>No-show: ${u.reliability?.no_show_count || 0}</span></div>
      ${profileBadges(u)}
      ${customBadgeRow(u.badges || [])}
      ${u.username ? `<div class="handle-pill">@${h(u.username)}</div>` : `<div class="handle-pill muted-pill">Telegram username скрыт</div>`}
      ${u.bio ? `<p class="bio-text">${h(u.bio)}</p>` : ''}
      <div class="game-actions wrap profile-actions">
        <button class="ghost-action" data-favorite-user="${u.telegram_id}">${u.is_favorite ? '★ В избранном' : '☆ В избранное'}</button>
        <button class="ghost-action danger-text" data-block-user="${u.telegram_id}">${u.is_blocked ? 'Заблокирован' : 'Заблокировать'}</button>
      </div>
      <button class="ghost-wide" data-nav="games">Назад к партиям</button>
    </section>
  `;
}

function chatScreen() {
  const opponent = state.chat.opponent;
  const game = state.chat.game;
  const messages = state.chat.messages || [];
  if (!game) return empty('Чат загружается...');
  return `
    <section class="chat-head">${userStrip(opponent)}<div class="chat-game-line">${h(game.place)} · ${h(game.date_label)} ${h(game.time_label)}</div></section>
    <div class="chat-safety-note">Не передавай личные данные, если не уверен в собеседнике.</div>
    <section class="chat-panel" id="messages-box">
      ${messages.map(m => `<div class="msg ${m.mine ? 'mine' : ''}"><span>${h(m.text)}</span><small>${h(formatTime(m.created_at))}</small></div>`).join('') || `<div class="empty-chat">Сообщений пока нет. Напиши первым.</div>`}
    </section>
    <section class="chat-templates">
      ${['Я буду с доской', 'Опоздаю на 5 минут', 'Уже на месте', 'Давай встретимся у входа'].map(t => `<button type="button" data-chat-template="${h(t)}">${h(t)}</button>`).join('')}
    </section>
    <form id="chat-form" class="chat-compose"><input name="text" placeholder="Сообщение сопернику" autocomplete="off" /><button type="submit">➤</button></form>
  `;
}

function empty(text) { return `<div class="notice-card">${h(text)}</div>`; }
function formatTime(raw) { try { return new Date(raw).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } }

function renderScreen() {
  switch (state.screen) {
    case 'games': return gamesScreen();
    case 'create': return createScreen();
    case 'my': return myScreen();
    case 'puzzle': return puzzleScreen();
    case 'profile': return profileScreen();
    case 'user': return publicProfileScreen();
    case 'chat': return chatScreen();
    default: return homeScreen();
  }
}

function registrationCityScreen() {
  return `<main class="registration-shell"><section class="registration-card">
    <div class="brand-mark">♜</div>
    <h1>Выберите ваш город</h1>
    <p>Так мы сразу покажем партии и игроков рядом.</p>
    <form id="registration-city-form">
      <select name="profile_city">${cityOptions(state.config?.default_city || 'Минск')}<option value="__custom__">＋ Добавить свой город</option></select>
      <div id="custom-city-fields" hidden><label>Название города<input name="custom_city" maxlength="80" autocomplete="address-level2" placeholder="Например, Псков" /></label></div>
      <button class="big-primary" type="submit">Продолжить</button>
    </form>
  </section></main>`;
}

function bindRegistrationCity() {
  const form = document.getElementById('registration-city-form');
  if (!form) return;
  const select = form.elements.profile_city;
  const custom = document.getElementById('custom-city-fields');
  select.addEventListener('change', () => { custom.hidden = select.value !== '__custom__'; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      let data;
      if (select.value === '__custom__') {
        const cityName = form.elements.custom_city.value.trim();
        if (cityName.length < 2) throw new Error('Введите название города');
        data = await api('/api/me/city-request', { method: 'POST', body: JSON.stringify({ city_name: cityName }) });
        showToast('Спасибо! Заявка на город отправлена администратору');
      } else {
        data = await api('/api/me/preferences', { method: 'PATCH', body: JSON.stringify({ profile_city: select.value }) });
      }
      state.me = data.user;
      writeCache('bootstrap', { config: state.config, user: state.me, games: state.games, my: state.my, daily_puzzle: state.dailyPuzzle });
      render();
      hydrate({ silent: true });
    } catch (error) { showToast(error.message); }
  });
}

function render() {
  applyTheme(currentThemeMode());
  // app.innerHTML replaces the map container. Leaflet must be detached first,
  // otherwise it remains bound to a stale DOM node and the next map is blank.
  destroyMapIfNeeded(true);
  if (state.me && !state.me.onboarding_completed) {
    app.innerHTML = registrationCityScreen();
    bindRegistrationCity();
    tg?.BackButton?.hide?.();
    return;
  }
  app.innerHTML = shell(renderScreen());
  if (tg?.BackButton) {
    if (state.screen === 'home') tg.BackButton.hide();
    else tg.BackButton.show();
  }
  window.ChessMeetI18n.apply(app, currentLanguage());
  bindEvents();
  afterRender();
}

function bindEvents() {
  document.querySelectorAll('[data-back]').forEach(el => el.addEventListener('click', goBack));
  document.querySelectorAll('[data-nav]').forEach(el => el.addEventListener('click', () => navigate(el.dataset.nav)));
  document.querySelectorAll('[data-respond]').forEach(el => el.addEventListener('click', () => { state.responseGameId = Number(el.dataset.respond); render(); }));
  document.querySelectorAll('[data-join-waitlist]').forEach(el => el.addEventListener('click', () => changeWaitlist(el.dataset.joinWaitlist, true)));
  document.querySelectorAll('[data-leave-waitlist]').forEach(el => el.addEventListener('click', () => changeWaitlist(el.dataset.leaveWaitlist, false)));
  document.querySelectorAll('[data-close-response]').forEach(el => el.addEventListener('click', () => { state.responseGameId = null; render(); }));
  document.querySelectorAll('[data-manage-responses]').forEach(el => el.addEventListener('click', () => openResponsesManager(el.dataset.manageResponses)));
  document.querySelectorAll('[data-close-responses]').forEach(el => el.addEventListener('click', () => { state.responsesPanelGameId = null; state.gameResponses = []; render(); }));
  document.querySelectorAll('[data-accept-response]').forEach(el => el.addEventListener('click', () => processResponse(el.dataset.acceptResponse, 'accept')));
  document.querySelectorAll('[data-decline-response]').forEach(el => el.addEventListener('click', () => processResponse(el.dataset.declineResponse, 'decline')));
  document.querySelectorAll('[data-modal-panel]').forEach(el => el.addEventListener('click', event => event.stopPropagation()));
  document.querySelectorAll('[data-response-template]').forEach(el => el.addEventListener('click', () => {
    const input = document.querySelector('#response-form textarea[name="proposed_comment"]');
    if (input) input.value = el.dataset.responseTemplate;
  }));
  const responseForm = document.getElementById('response-form');
  if (responseForm) responseForm.addEventListener('submit', submitResponse);
  document.querySelectorAll('[data-view-profile]').forEach(el => el.addEventListener('click', () => openPublicProfile(el.dataset.viewProfile)));
  document.querySelectorAll('[data-open-chat]').forEach(el => el.addEventListener('click', () => openChat(el.dataset.openChat)));
  document.querySelectorAll('[data-add-calendar]').forEach(el => el.addEventListener('click', () => addGameToCalendar(el.dataset.addCalendar)));
  document.querySelectorAll('[data-cancel-game]').forEach(el => el.addEventListener('click', () => cancelGame(el.dataset.cancelGame)));
  document.querySelectorAll('[data-no-show]').forEach(el => el.addEventListener('click', () => reportNoShow(el.dataset.noShow)));
  document.querySelectorAll('[data-rematch]').forEach(el => el.addEventListener('click', () => createRematch(el.dataset.rematch)));
  document.querySelectorAll('[data-check-in]').forEach(el => el.addEventListener('click', () => checkInGame(el.dataset.checkIn, el.dataset.lateMinutes)));
  document.querySelectorAll('[data-favorite-user]').forEach(el => el.addEventListener('click', () => toggleFavorite(el.dataset.favoriteUser)));
  document.querySelectorAll('[data-block-user]').forEach(el => el.addEventListener('click', () => blockUser(el.dataset.blockUser)));
  document.querySelectorAll('[data-report-user]').forEach(el => el.addEventListener('click', () => reportUser(el.dataset.reportUser, el.dataset.reportGame || null)));
  document.querySelectorAll('[data-game-photo]').forEach(el => el.addEventListener('change', e => uploadGamePhoto(e, el.dataset.gamePhoto)));
  document.querySelectorAll('[data-rate-game]').forEach(form => form.addEventListener('submit', submitRating));
  document.querySelectorAll('[data-place-rate-game]').forEach(form => form.addEventListener('submit', submitPlaceRating));
  document.querySelectorAll('[data-diary-game]').forEach(el => el.addEventListener('click', () => updateDiary(el.dataset.diaryGame)));
  document.querySelectorAll('[data-share-invite]').forEach(el => el.addEventListener('click', shareInvite));
  document.querySelectorAll('[data-copy-invite]').forEach(el => el.addEventListener('click', copyInviteLink));
  document.querySelectorAll('[data-set-language]').forEach(el => el.addEventListener('click', () => setLanguage(el.dataset.setLanguage)));
  document.querySelectorAll('[data-enable-city-alerts]').forEach(el => el.addEventListener('click', enableCityAlerts));
  document.querySelectorAll('[data-use-location]').forEach(el => el.addEventListener('click', useMyLocation));
  document.querySelectorAll('[data-toggle-history]').forEach(el => el.addEventListener('click', () => { state.showGameHistory = el.dataset.toggleHistory === '1'; render(); }));
  document.querySelectorAll('[data-edit-game]').forEach(el => el.addEventListener('click', () => { state.editingGameId = Number(el.dataset.editGame); state.selectedPlace = null; navigate('create'); }));
  document.querySelectorAll('[data-cancel-edit]').forEach(el => el.addEventListener('click', () => { state.editingGameId = null; state.selectedPlace = null; navigate('my'); }));
  document.querySelectorAll('[data-close-onboarding]').forEach(el => el.addEventListener('click', () => { localStorage.setItem('chessmeet_onboarding_seen', '1'); render(); }));
  document.querySelectorAll('[data-clear-draft]').forEach(el => el.addEventListener('click', () => { clearCreateDraft(); state.draftNoticeHidden = true; render(); }));
  document.querySelectorAll('[data-puzzle-square]').forEach(el => el.addEventListener('click', () => handlePuzzleClick(el.dataset.puzzleSquare, el.dataset.puzzlePiece || '')));
  document.querySelectorAll('[data-filter-format]').forEach(el => el.addEventListener('click', () => { state.gamesFormat = el.dataset.filterFormat; render(); }));
  const search = document.getElementById('games-search');
  if (search) search.addEventListener('input', e => { state.gamesQuery = e.target.value; render(); });
  const dateFilter = document.getElementById('games-date-filter');
  if (dateFilter) dateFilter.addEventListener('change', e => { state.gamesDate = e.target.value; render(); });
  const levelFilter = document.getElementById('games-level-filter');
  if (levelFilter) levelFilter.addEventListener('change', e => { state.gamesLevel = e.target.value; render(); });
  const boardFilter = document.getElementById('games-board-filter');
  if (boardFilter) boardFilter.addEventListener('change', e => { state.gamesBoard = e.target.value; render(); });
  const createForm = document.getElementById('create-form');
  if (createForm) {
    createForm.addEventListener('submit', submitCreate);
    createForm.addEventListener('input', () => { if (!createForm.dataset.editingId) setCreateDraft(createPayloadFromForm(createForm)); });
    createForm.addEventListener('change', () => { if (!createForm.dataset.editingId) setCreateDraft(createPayloadFromForm(createForm)); });
  }
  const badgesForm = document.getElementById('badges-form');
  if (badgesForm) badgesForm.addEventListener('submit', submitBadgeVisibility);
  const profileForm = document.getElementById('profile-form');
  if (profileForm) profileForm.addEventListener('submit', submitProfile);
  const cityFilter = document.getElementById('city-filter-select');
  if (cityFilter) cityFilter.addEventListener('change', e => setCity(e.target.value));
  const themeSelect = document.getElementById('theme-mode-select');
  if (themeSelect) themeSelect.addEventListener('change', e => applyTheme(e.target.value));
  const photoInput = document.getElementById('profile-photo-input');
  if (photoInput) photoInput.addEventListener('change', handlePhoto);
  document.querySelectorAll('[data-chat-template]').forEach(el => el.addEventListener('click', () => sendChatText(el.dataset.chatTemplate)));
  const chatForm = document.getElementById('chat-form');
  if (chatForm) chatForm.addEventListener('submit', submitChat);
}

async function submitResponse(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const id = form.dataset.gameId;
  const fd = new FormData(form);
  const payload = {
    proposed_date_label: fd.get('proposed_date_label') || '',
    proposed_time_label: fd.get('proposed_time_label') || '',
    proposed_comment: fd.get('proposed_comment') || '',
  };
  try {
    await api(`/api/games/${id}/respond`, { method: 'POST', body: JSON.stringify(payload) });
    trackEvent('game_response_sent', { game_id: Number(id), city: selectedCity() });
    tg?.HapticFeedback?.notificationOccurred?.('success');
    state.responseGameId = null;
    showToast('Отклик отправлен');
    await loadGames(); await loadMy(); navigate('my');
  } catch (e) { showToast(e.message); }
}

async function changeWaitlist(gameId, join) {
  try {
    await api(`/api/games/${gameId}/waitlist`, { method: join ? 'POST' : 'DELETE' });
    showToast(join ? 'Ты добавлен в лист ожидания' : 'Ты вышел из листа ожидания');
    await loadGames();
    await loadMy();
    render();
  } catch (err) { showToast(err.message); }
}

async function openResponsesManager(gameId) {
  try {
    const data = await api(`/api/games/${gameId}/responses`);
    state.responsesPanelGameId = Number(gameId);
    state.gameResponses = data.responses || [];
    render();
  } catch (err) { showToast(err.message); }
}

async function processResponse(responseId, action) {
  try {
    await api(`/api/responses/${responseId}/${action}`, { method: 'POST' });
    tg?.HapticFeedback?.notificationOccurred?.(action === 'accept' ? 'success' : 'warning');
    trackEvent(`response_${action}ed`, { response_id: Number(responseId) });
    state.responsesPanelGameId = null;
    state.gameResponses = [];
    await loadMy();
    render();
    showToast(action === 'accept' ? 'Соперник выбран' : 'Отклик отклонён');
  } catch (err) { showToast(err.message); }
}
async function cancelGame(id) { const reason = prompt('Причина отмены:\n1. Не могу прийти\n2. Изменились планы\n3. Нашёл соперника\n4. Ошибка в заявке\n5. Другое', 'Изменились планы') || ''; try { await api(`/api/games/${id}/cancel`, { method: 'POST', body: JSON.stringify({ reason }) }); showToast('Отменено'); await loadMy(); render(); } catch (e) { showToast(e.message); } }

async function reportNoShow(id) {
  if (!confirm('Отметить, что соперник не пришёл?')) return;
  try { await api(`/api/games/${id}/no-show`, { method: 'POST' }); showToast('No-show отмечен'); await loadMy(); render(); } catch (e) { showToast(e.message); }
}
async function createRematch(id) {
  try { await api(`/api/games/${id}/rematch`, { method: 'POST' }); showToast('Заявка на реванш создана на завтра'); await loadMy(); navigate('my'); } catch (e) { showToast(e.message); }
}
async function toggleFavorite(id) {
  try { const data = await api(`/api/users/${id}/favorite`, { method: 'POST' }); state.publicProfile = data.user; showToast(data.favorited ? 'Добавлен в избранное' : 'Удалён из избранного'); render(); } catch (e) { showToast(e.message); }
}
async function blockUser(id) {
  if (!confirm('Заблокировать этого пользователя? Он не сможет откликаться на твои заявки.')) return;
  try { const data = await api(`/api/users/${id}/block`, { method: 'POST' }); state.publicProfile = data.user; showToast('Пользователь заблокирован'); render(); } catch (e) { showToast(e.message); }
}
async function reportUser(id) {
  const reason = prompt('Причина жалобы: не пришёл, грубость, спам, небезопасное место...') || '';
  if (!reason.trim()) return;
  const comment = prompt('Комментарий (необязательно):') || '';
  try { await api(`/api/users/${id}/report`, { method: 'POST', body: JSON.stringify({ reason, comment }) }); showToast('Жалоба сохранена'); } catch (e) { showToast(e.message); }
}
function uploadGamePhoto(event, gameId) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 1_500_000) { showToast('Фото слишком большое. Лучше до 1.5 MB.'); return; }
  const reader = new FileReader();
  reader.onload = async () => {
    try { await api(`/api/games/${gameId}/photos`, { method: 'POST', body: JSON.stringify({ photo_data_url: String(reader.result || ''), caption: '' }) }); showToast('Фото добавлено'); await loadMy(); render(); } catch (e) { showToast(e.message); }
  };
  reader.readAsDataURL(file);
}

async function submitRating(e) { e.preventDefault(); const fd = new FormData(e.currentTarget); try { await api(`/api/games/${e.currentTarget.dataset.rateGame}/rate`, { method: 'POST', body: JSON.stringify({ score: Number(fd.get('score')), comment: fd.get('comment') || '' }) }); showToast('Оценка сохранена'); await loadMy(); const me = await api('/api/me'); state.me = me.user; render(); } catch (err) { showToast(err.message); } }

async function submitPlaceRating(e) {
  e.preventDefault();
  const fd = new FormData(e.currentTarget);
  try {
    await api(`/api/games/${e.currentTarget.dataset.placeRateGame}/place-rating`, { method: 'POST', body: JSON.stringify({ score: Number(fd.get('score')), comment: fd.get('comment') || '' }) });
    showToast('Оценка места сохранена');
    await loadMy(); render();
  } catch (err) { showToast(err.message); }
}

async function updateDiary(gameId) {
  const result = prompt('Результат партии (например: я выиграл / ничья / играли без счёта):', '') || '';
  const notes = prompt('Заметка в дневник:', '') || '';
  if (!result && !notes) return;
  try {
    await api(`/api/games/${gameId}/diary`, { method: 'POST', body: JSON.stringify({ result, notes }) });
    showToast('Запись добавлена в дневник');
    await loadMy(); render();
  } catch (err) { showToast(err.message); }
}

function inviteLink() {
  const id = state.me?.telegram_id;
  return `https://t.me/${OFFICIAL_BOT_USERNAME}?start=ref_${id}`;
}

function shareInvite() {
  const link = inviteLink();
  const text = `Сыграем в шахматы офлайн? Я использую ChessMeet: ${link}`;
  if (navigator.share) navigator.share({ title: 'ChessMeet', text, url: link }).catch(() => {});
  else { navigator.clipboard?.writeText(text); showToast('Текст приглашения скопирован'); }
}

function copyInviteLink() {
  navigator.clipboard?.writeText(inviteLink());
  showToast('Реферальная ссылка скопирована');
}

async function submitCreate(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const payload = createPayloadFromForm(form);
  const editingId = form.dataset.editingId;
  if (!confirm(previewText(payload, Boolean(editingId)))) return;
  try {
    if (editingId) {
      await api(`/api/games/${editingId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      state.editingGameId = null;
      state.selectedPlace = null;
      showToast('Заявка обновлена');
    } else {
      await api('/api/games', { method: 'POST', body: JSON.stringify(payload) });
      clearCreateDraft();
      state.selectedPlace = null;
      showToast('Заявка опубликована');
      tg?.HapticFeedback?.notificationOccurred?.('success');
    }
    await loadGames(); await loadMy(); navigate('my');
  } catch (err) { showToast(err.message); }
}


async function submitBadgeVisibility(e) {
  e.preventDefault();
  const ids = Array.from(e.currentTarget.querySelectorAll('input[name="visible_badge_ids"]:checked')).map(x => Number(x.value));
  try {
    const data = await api('/api/me/badges', { method: 'PATCH', body: JSON.stringify({ visible_badge_ids: ids }) });
    state.me.badges = data.badges || [];
    showToast('Значки сохранены');
    render();
  } catch (err) {
    showToast(err.message);
  }
}

async function submitProfile(e) {
  e.preventDefault();
  const submitButton = e.currentTarget.querySelector('button[type="submit"]');
  if (submitButton) {
    submitButton.disabled = true;
    submitButton.textContent = tr('Сохраняем...');
  }
  const fd = new FormData(e.currentTarget);
  const payload = {
    display_name: fd.get('display_name'),
    profile_city: fd.get('profile_city'),
    level: fd.get('level'),
    bio: fd.get('bio') || '',
    show_telegram_username: fd.get('show_telegram_username') === 'on',
    photo_data_url: state.profilePhotoDraft || '',
    notify_game_reminders: fd.get('notify_game_reminders') === 'on',
    notify_new_requests: fd.get('notify_new_requests') === 'on',
    notify_puzzle_streak: fd.get('notify_puzzle_streak') === 'on',
    theme_mode: fd.get('theme_mode') || 'light',
    ui_language: currentLanguage(),
    subscription_format: fd.get('subscription_format') || 'all',
    subscription_level: fd.get('subscription_level') || 'all',
  };
  try {
    const data = await api('/api/me', { method: 'PATCH', body: JSON.stringify(payload) });
    state.me = data.user;
    applyTheme(state.me.theme_mode || 'light');
    localStorage.setItem(cacheKey('theme_mode'), state.me.theme_mode || 'light');
    localStorage.setItem(cacheKey('ui_language'), state.me.ui_language || telegramLanguage());
    state.profilePhotoDraft = data.user.photo_data_url || '';
    await Promise.all([loadGames(), loadDailyPuzzle(), loadCityStats()]);
    showToast('Настройки сохранены');
    render();
  } catch (err) {
    showToast(err.message);
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.textContent = tr('Сохранить профиль');
    }
  }
}

function renderTelegramLaunch(botUsername) {
  const link = `https://t.me/${OFFICIAL_BOT_USERNAME}?start=app`;
  app.innerHTML = `
    <main class="auth-launch-shell">
      <section class="auth-launch-card">
        <div class="auth-launch-icon">♜</div>
        <div class="step-label">ChessMeet</div>
        <h1>Открой приложение в Telegram</h1>
        <p>Для безопасного входа Telegram должен подтвердить твой аккаунт. Обычная ссылка в браузере не содержит данных авторизации.</p>
        <a class="big-primary auth-launch-button" href="${h(link)}">Открыть ChessMeet в Telegram</a>
        <small>Если приложение уже открыто в Telegram, закрой это окно и нажми кнопку Mini App в основном боте ещё раз.</small>
      </section>
    </main>`;
}
async function checkInGame(id, lateMinutes = 0) {
  try {
    await api(`/api/games/${id}/check-in`, { method: 'POST', body: JSON.stringify({ late_minutes: Number(lateMinutes || 0) }) });
    tg?.HapticFeedback?.notificationOccurred?.('success');
    trackEvent('game_check_in', { game_id: Number(id) });
    showToast('Отметка «Я на месте» сохранена');
    await loadMy();
    render();
  } catch (e) { showToast(e.message); }
}

async function setLanguage(language) {
  const next = window.ChessMeetI18n.normalize(language);
  if (next === currentLanguage() && state.me?.ui_language === next) return;
  try {
    const data = await api('/api/me/preferences', {
      method: 'PATCH',
      body: JSON.stringify({ ui_language: next }),
    });
    state.me = { ...(state.me || {}), ...data.user };
    localStorage.setItem(cacheKey('ui_language'), next);
    showToast(next === 'ru' ? 'Язык изменён' : 'Language changed');
    render();
  } catch (err) {
    showToast(err.message);
  }
}

async function setCity(city) {
  if (!city || city === selectedCity()) return;
  try {
    const data = await api('/api/me/preferences', {
      method: 'PATCH',
      body: JSON.stringify({ profile_city: city }),
    });
    state.me = { ...(state.me || {}), ...data.user };
    state.selectedPlace = null;
    await Promise.all([loadGames(), loadDailyPuzzle(), loadCityStats()]);
    showToast(`Город: ${city}`);
    render();
  } catch (err) {
    showToast(err.message);
    render();
  }
}

async function enableCityAlerts() {
  try {
    const data = await api('/api/me/preferences', {
      method: 'PATCH',
      body: JSON.stringify({ notify_new_requests: true }),
    });
    state.me = { ...(state.me || {}), ...data.user };
    showToast(`Уведомления для города ${selectedCity()} включены`);
    trackEvent('city_alert_enabled', { city: selectedCity() });
    render();
  } catch (err) {
    showToast(err.message);
  }
}

function trackEvent(eventName, eventData = {}) {
  api('/api/analytics/event', {
    method: 'POST',
    body: JSON.stringify({ event_name: eventName, event_data: eventData }),
  }).catch(() => {});
}

function useMyLocation() {
  if (!navigator.geolocation) {
    showToast('Геолокация недоступна');
    return;
  }
  navigator.geolocation.getCurrentPosition(
    position => {
      state.userLocation = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      };
      trackEvent('location_enabled', { city: selectedCity() });
      render();
    },
    () => showToast('Не удалось получить геолокацию'),
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
  );
}

function handlePhoto(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  if (file.size > 1_200_000) { showToast('Фото слишком большое. Лучше до 1.2 MB.'); return; }
  const reader = new FileReader();
  reader.onload = () => { state.profilePhotoDraft = String(reader.result || ''); render(); };
  reader.readAsDataURL(file);
}

async function openPublicProfile(id) { try { const data = await api(`/api/users/${id}`); state.publicProfile = data.user; navigate('user'); } catch (e) { showToast(e.message); } }
async function openChat(id) { try { state.activeChatGameId = id; await loadChat(id); const url = new URL(location.href); url.searchParams.set('screen', 'chat'); url.searchParams.set('game', id); history.replaceState({}, '', url.toString()); state.screen = 'chat'; render(); scrollChatDown(); } catch (e) { showToast(e.message); } }
async function sendChatText(text) { const clean = String(text || '').trim(); if (!clean) return; try { await api(`/api/games/${state.activeChatGameId}/chat`, { method: 'POST', body: JSON.stringify({ text: clean }) }); await loadChat(state.activeChatGameId); render(); scrollChatDown(); } catch (err) { showToast(err.message); } }
async function submitChat(e) { e.preventDefault(); const input = e.currentTarget.querySelector('input[name="text"]'); const text = input.value.trim(); if (!text) return; await sendChatText(text); input.value = ''; }
function scrollChatDown() { const box = document.getElementById('messages-box'); if (box) box.scrollTop = box.scrollHeight; }

function findMyGame(gameId) {
  const all = [...(state.my.created || []), ...(state.my.responded || []), ...(state.my.pending_reviews || [])];
  return all.find(g => Number(g.id) === Number(gameId));
}

function compactDateForIcs(dateLabel, timeLabel) {
  const date = String(dateLabel || '').replaceAll('-', '');
  const time = String(timeLabel || '18:00').replace(':', '').padEnd(4, '0');
  return `${date}T${time}00`;
}

function addMinutesToIcs(dateLabel, timeLabel, minutes) {
  const raw = `${dateLabel}T${timeLabel || '18:00'}:00`;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return compactDateForIcs(dateLabel, timeLabel);
  d.setMinutes(d.getMinutes() + minutes);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${y}${m}${day}T${hh}${mm}00`;
}

function compactDateForIcsFromDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${y}${m}${day}T${hh}${mm}00`;
}

function parseGameLocalDateTime(game) {
  const dateMatch = String(game.date_label || '').match(/(\d{4}-\d{2}-\d{2})/);
  const timeSource = game.is_flexible ? (game.time_window_start || game.time_label || '18:00') : (game.time_label || '18:00');
  const timeMatch = String(timeSource || '').match(/(\d{1,2})[:.](\d{2})/);
  if (dateMatch && timeMatch) {
    const [yy, mm, dd] = dateMatch[1].split('-').map(Number);
    return new Date(yy, mm - 1, dd, Number(timeMatch[1]), Number(timeMatch[2]), 0);
  }
  if (game.scheduled_at) {
    const d = new Date(game.scheduled_at);
    if (!Number.isNaN(d.getTime())) return d;
  }
  return null;
}

function addGameToCalendar(gameId) {
  const game = findMyGame(gameId);
  if (!game) { showToast('Партия не найдена'); return; }
  const startDate = parseGameLocalDateTime(game);
  if (!startDate) { showToast('Не удалось определить дату партии для календаря'); return; }
  let endDate;
  if (game.is_flexible && game.time_window_end && /\d{4}-\d{2}-\d{2}/.test(String(game.date_label || ''))) {
    const [yy, mm, dd] = String(game.date_label).match(/(\d{4})-(\d{2})-(\d{2})/).slice(1).map(Number);
    const tm = String(game.time_window_end).match(/(\d{1,2})[:.](\d{2})/);
    endDate = tm ? new Date(yy, mm - 1, dd, Number(tm[1]), Number(tm[2]), 0) : new Date(startDate.getTime() + 90 * 60000);
  } else {
    endDate = new Date(startDate.getTime() + 90 * 60000);
  }
  const dtStart = compactDateForIcsFromDate(startDate);
  const dtEnd = compactDateForIcsFromDate(endDate);
  const opponent = game.opponent?.display_name ? ` с ${game.opponent.display_name}` : '';
  const title = `ChessMeet: партия${opponent}`;
  const description = [game.game_format, game.comment, game.map_url].filter(Boolean).join('\n');
  const ics = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//ChessMeet//MiniApp//RU',
    'BEGIN:VEVENT',
    `UID:chessmeet-${game.id}@local`,
    `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')}`,
    `DTSTART:${dtStart}`,
    `DTEND:${dtEnd}`,
    `SUMMARY:${title.replace(/[,;]/g, ' ')}`,
    `LOCATION:${String(game.address || game.place || '').replace(/[,;]/g, ' ')}`,
    `DESCRIPTION:${description.replace(/\n/g, '\\n').replace(/[,;]/g, ' ')}`,
    'END:VEVENT',
    'END:VCALENDAR'
  ].join('\r\n');
  const blob = new Blob([ics], { type: 'text/calendar;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `chessmeet-game-${game.id}.ics`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  showToast('Файл календаря создан');
}

function handlePuzzleClick(square, piece) {
  const d = state.dailyPuzzle;
  if (!d || d.solved) return;
  const side = sideToMove(d.puzzle?.fen || '');
  if (!state.puzzleSelectedSquare) {
    if (!isOwnPiece(piece, side)) { showToast('Сначала выбери свою фигуру'); return; }
    state.puzzleSelectedSquare = square; render(); return;
  }
  if (state.puzzleSelectedSquare === square) { state.puzzleSelectedSquare = null; render(); return; }
  if (isOwnPiece(piece, side)) { state.puzzleSelectedSquare = square; render(); return; }
  const move = `${state.puzzleSelectedSquare}${square}`.toLowerCase();
  state.puzzleSelectedSquare = null;
  state.puzzleLastMove = move;
  answerPuzzle(move);
}

async function answerPuzzle(move) { try { const data = await api('/api/daily-puzzle/answer', { method: 'POST', body: JSON.stringify({ selected_move: move }) }); state.dailyPuzzle = data; const me = await api('/api/me'); state.me = me.user; showToast(data.correct ? `Верно: ${data.solution_san || move}` : 'Пока неверно'); if (!data.correct) state.puzzleLastMove = null; render(); } catch (e) { showToast(e.message); } }

function nextCityMidnight() {
  const now = new Date();
  const timeZone = selectedCityInfo()?.timezone || 'Europe/Minsk';
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hourCycle: 'h23',
  });
  const parts = Object.fromEntries(formatter.formatToParts(now).map(part => [part.type, part.value]));
  const localAsUtc = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    Number(parts.hour), Number(parts.minute), Number(parts.second),
  );
  const nextLocalMidnight = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) + 1);
  return Math.max(0, nextLocalMidnight - localAsUtc);
}
function updateCountdown() {
  const el = document.getElementById('puzzle-countdown');
  if (!el) return;
  const ms = nextCityMidnight();
  const hours = String(Math.floor(ms / 3600000)).padStart(2, '0');
  const minutes = String(Math.floor((ms % 3600000) / 60000)).padStart(2, '0');
  const seconds = String(Math.floor((ms % 60000) / 1000)).padStart(2, '0');
  el.textContent = `${hours}:${minutes}:${seconds}`;
}
function ensureCountdown() { if (!state.countdownInterval) state.countdownInterval = setInterval(updateCountdown, 1000); }

function initCreateMap() {
  const el = document.getElementById('create-map');
  if (!el || state.map) return;
  const selectedLat = Number(state.selectedPlace?.latitude);
  const selectedLng = Number(state.selectedPlace?.longitude);
  const hasSelectedCoordinates = Number.isFinite(selectedLat) && Number.isFinite(selectedLng);
  const cityInfo = selectedCityInfo();
  const cityLat = Number(cityInfo?.latitude);
  const cityLng = Number(cityInfo?.longitude);
  const hasCityCoordinates = Number.isFinite(cityLat) && Number.isFinite(cityLng);
  const center = hasSelectedCoordinates
    ? [selectedLat, selectedLng]
    : (hasCityCoordinates ? [cityLat, cityLng] : [53.9, 27.5667]);
  // Always use the built-in picker. Telegram Desktop/Android may retain a
  // half-loaded global Leaflet object after blocking its stylesheet or CDN.
  initFallbackMap(el, center, hasSelectedCoordinates ? 16 : 12, hasSelectedCoordinates);
}

async function selectMapPoint(lat, lng) {
  let address = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  let place = 'Точка на карте';
  try {
    const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&accept-language=${encodeURIComponent(currentLanguage())}&lat=${lat}&lon=${lng}`);
    if (!res.ok) throw new Error('Reverse geocoding failed');
    const data = await res.json();
    address = data.display_name || address;
    place = data.name || data.address?.amenity || data.address?.road || place;
  } catch (_) {}
  const cityInput = document.querySelector('#create-form input[name="city"]');
  const currentCityValue = cityInput?.value?.trim() || state.me?.profile_city || state.config?.default_city || 'Минск';
  state.selectedPlace = { latitude: lat, longitude: lng, address, place, area: currentCityValue, map_url: `https://www.openstreetmap.org/?mlat=${lat.toFixed(6)}&mlon=${lng.toFixed(6)}#map=17/${lat.toFixed(6)}/${lng.toFixed(6)}` };
  render();
}

function initFallbackMap(el, center, zoom, showMarker) {
  const tileSize = 256;
  const scale = tileSize * (2 ** zoom);
  const project = ([lat, lng]) => {
    const safeLat = Math.max(-85.0511, Math.min(85.0511, lat));
    const sin = Math.sin(safeLat * Math.PI / 180);
    return [(lng + 180) / 360 * scale, (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale];
  };
  const unproject = ([x, y]) => {
    const lng = x / scale * 360 - 180;
    const n = Math.PI - 2 * Math.PI * y / scale;
    return [180 / Math.PI * Math.atan(Math.sinh(n)), lng];
  };
  const [cx, cy] = project(center);
  const width = el.clientWidth || 360;
  const height = el.clientHeight || 320;
  el.classList.add('fallback-map');
  el.innerHTML = '<div class="fallback-tiles"></div><div class="fallback-map-label">OpenStreetMap · нажмите, чтобы выбрать место</div>';
  const layer = el.querySelector('.fallback-tiles');
  const firstX = Math.floor((cx - width / 2) / tileSize);
  const lastX = Math.floor((cx + width / 2) / tileSize);
  const firstY = Math.floor((cy - height / 2) / tileSize);
  const lastY = Math.floor((cy + height / 2) / tileSize);
  const tileCount = 2 ** zoom;
  for (let y = firstY; y <= lastY; y += 1) {
    if (y < 0 || y >= tileCount) continue;
    for (let x = firstX; x <= lastX; x += 1) {
      const wrappedX = ((x % tileCount) + tileCount) % tileCount;
      const img = document.createElement('img');
      img.alt = '';
      img.draggable = false;
      img.src = `/api/map-tiles/${zoom}/${wrappedX}/${y}.png`;
      img.addEventListener('error', () => {
        const label = el.querySelector('.fallback-map-label');
        if (label) label.textContent = 'Не удалось загрузить карту · проверьте доступ к OpenStreetMap';
      }, { once: true });
      img.style.left = `${x * tileSize - cx + width / 2}px`;
      img.style.top = `${y * tileSize - cy + height / 2}px`;
      layer.appendChild(img);
    }
  }
  if (showMarker) el.insertAdjacentHTML('beforeend', '<div class="fallback-marker">●</div>');
  el.addEventListener('click', async event => {
    const rect = el.getBoundingClientRect();
    const point = unproject([cx + event.clientX - rect.left - rect.width / 2, cy + event.clientY - rect.top - rect.height / 2]);
    await selectMapPoint(point[0], point[1]);
  });
  state.map = { remove() { el.innerHTML = ''; }, invalidateSize() {} };
}
function addMarker(lat, lng) {
  const latitude = Number(lat);
  const longitude = Number(lng);
  if (!state.map || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
  if (state.marker) state.marker.remove();
  state.marker = L.marker([latitude, longitude]).addTo(state.map);
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
}
bootstrap();
