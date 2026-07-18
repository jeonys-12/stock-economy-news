const state = {
  news: [],
  country: 'all',
  category: 'all',
  period: 7,
  query: '',
  sort: 'importance',
  visible: 18,
};

const elements = {
  grid: document.querySelector('#newsGrid'),
  template: document.querySelector('#newsCardTemplate'),
  empty: document.querySelector('#emptyState'),
  loadMore: document.querySelector('#loadMoreButton'),
  updatedAt: document.querySelector('#updatedAt'),
  totalCount: document.querySelector('#totalCount'),
  dailyCount: document.querySelector('#dailyCount'),
  koreaCount: document.querySelector('#koreaCount'),
  usaCount: document.querySelector('#usaCount'),
  search: document.querySelector('#searchInput'),
  sort: document.querySelector('#sortSelect'),
  refresh: document.querySelector('#refreshButton'),
};

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
});

function hoursSince(dateValue) {
  const timestamp = new Date(dateValue).getTime();
  return (Date.now() - timestamp) / 3600000;
}

function filteredNews() {
  const maxHours = state.period * 24;
  const query = state.query.trim().toLowerCase();
  const result = state.news.filter(item => {
    const countryMatch = state.country === 'all' || item.country === state.country;
    const categoryMatch = state.category === 'all' || item.category === state.category;
    const periodMatch = hoursSince(item.published_at) <= maxHours;
    const text = `${item.title} ${item.description || ''} ${item.source || ''}`.toLowerCase();
    const queryMatch = !query || text.includes(query);
    return countryMatch && categoryMatch && periodMatch && queryMatch;
  });

  return result.sort((a, b) => {
    if (state.sort === 'latest') {
      return new Date(b.published_at) - new Date(a.published_at);
    }
    return (b.importance_score - a.importance_score) ||
      (new Date(b.published_at) - new Date(a.published_at));
  });
}

function renderSummary() {
  elements.totalCount.textContent = state.news.length.toLocaleString('ko-KR');
  elements.dailyCount.textContent = state.news.filter(n => hoursSince(n.published_at) <= 24).length.toLocaleString('ko-KR');
  elements.koreaCount.textContent = state.news.filter(n => n.country === '대한민국').length.toLocaleString('ko-KR');
  elements.usaCount.textContent = state.news.filter(n => n.country === '미국').length.toLocaleString('ko-KR');
}

function renderNews() {
  const result = filteredNews();
  const visibleItems = result.slice(0, state.visible);
  elements.grid.innerHTML = '';

  for (const item of visibleItems) {
    const fragment = elements.template.content.cloneNode(true);
    fragment.querySelector('.country-badge').textContent = item.country;
    fragment.querySelector('.category-badge').textContent = item.category;
    const importance = fragment.querySelector('.importance-badge');
    importance.textContent = item.importance_score >= 80 ? '핵심' : item.importance_score >= 60 ? '주요' : '일반';
    if (item.importance_score < 60) importance.hidden = true;

    const link = fragment.querySelector('.news-link');
    link.textContent = item.title;
    link.href = item.url;
    fragment.querySelector('.news-description').textContent = item.description || '기사 원문에서 자세한 내용을 확인하세요.';
    fragment.querySelector('.news-source').textContent = item.source || '출처 미상';
    const time = fragment.querySelector('.news-date');
    time.dateTime = item.published_at;
    time.textContent = dateFormatter.format(new Date(item.published_at));
    elements.grid.appendChild(fragment);
  }

  elements.empty.hidden = result.length > 0;
  elements.loadMore.hidden = result.length <= state.visible;
}

function resetAndRender() {
  state.visible = 18;
  renderNews();
}

function bindFilterButtons(containerId, key, dataKey, transform = value => value) {
  document.querySelector(`#${containerId}`).addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    document.querySelectorAll(`#${containerId} button`).forEach(btn => btn.classList.remove('active'));
    button.classList.add('active');
    state[key] = transform(button.dataset[dataKey]);
    resetAndRender();
  });
}

async function loadNews() {
  elements.updatedAt.textContent = '불러오는 중';
  try {
    const response = await fetch(`data/news.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.news = Array.isArray(payload.news) ? payload.news : [];
    elements.updatedAt.textContent = payload.updated_at
      ? new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(payload.updated_at))
      : '업데이트 정보 없음';
    renderSummary();
    renderNews();
  } catch (error) {
    console.error(error);
    state.news = [];
    elements.updatedAt.textContent = '데이터 로드 실패';
    elements.empty.hidden = false;
    elements.empty.textContent = '뉴스 데이터를 불러오지 못했습니다. GitHub Actions 실행 상태를 확인하세요.';
  }
}

bindFilterButtons('countryFilters', 'country', 'country');
bindFilterButtons('categoryFilters', 'category', 'category');
bindFilterButtons('periodFilters', 'period', 'period', Number);

elements.search.addEventListener('input', event => {
  state.query = event.target.value;
  resetAndRender();
});

elements.sort.addEventListener('change', event => {
  state.sort = event.target.value;
  resetAndRender();
});

elements.loadMore.addEventListener('click', () => {
  state.visible += 18;
  renderNews();
});

elements.refresh.addEventListener('click', loadNews);

loadNews();
