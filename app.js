const state = {
  news: [],
  type: 'all',
  country: 'all',
  category: 'all',
  period: 7,
  query: '',
  sort: 'importance',
  visible: 18,
};

const $ = selector => document.querySelector(selector);
const elements = {
  grid: $('#newsGrid'),
  template: $('#newsCardTemplate'),
  empty: $('#emptyState'),
  loadMore: $('#loadMoreButton'),
  updatedAt: $('#updatedAt'),
  totalCount: $('#totalCount'),
  dailyCount: $('#dailyCount'),
  officialCount: $('#officialCount'),
  youtubeCount: $('#youtubeCount'),
  search: $('#searchInput'),
  sort: $('#sortSelect'),
  refresh: $('#refreshButton'),
  reset: $('#resetButton'),
  resultInfo: $('#resultInfo'),
  brief: $('#briefText'),
};

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

function hoursSince(value) {
  return (Date.now() - new Date(value).getTime()) / 3600000;
}

function filteredNews() {
  const maxHours = state.period * 24;
  const query = state.query.trim().toLowerCase();

  return state.news
    .filter(item => {
      const type = item.type || 'news';
      const text = `${item.title} ${item.description || ''} ${item.source || ''} ${item.institution || ''}`.toLowerCase();
      return (
        (state.type === 'all' || type === state.type) &&
        (state.country === 'all' || item.country === state.country) &&
        (state.category === 'all' || item.category === state.category) &&
        hoursSince(item.published_at) <= maxHours &&
        (!query || text.includes(query))
      );
    })
    .sort((a, b) => {
      if (state.sort === 'latest') {
        return new Date(b.published_at) - new Date(a.published_at);
      }
      return (
        Number(Boolean(b.official)) - Number(Boolean(a.official)) ||
        (b.importance_score || 0) - (a.importance_score || 0) ||
        new Date(b.published_at) - new Date(a.published_at)
      );
    });
}

function renderSummary() {
  elements.totalCount.textContent = state.news.length.toLocaleString('ko-KR');
  elements.dailyCount.textContent = state.news
    .filter(item => hoursSince(item.published_at) <= 24)
    .length.toLocaleString('ko-KR');
  elements.officialCount.textContent = state.news
    .filter(item => item.official)
    .length.toLocaleString('ko-KR');
  elements.youtubeCount.textContent = state.news
    .filter(item => (item.type || 'news') === 'youtube')
    .length.toLocaleString('ko-KR');
  elements.brief.textContent = `최근 24시간 ${elements.dailyCount.textContent}건 · 공식기관 ${elements.officialCount.textContent}건 · YouTube ${elements.youtubeCount.textContent}건을 수집했습니다.`;
}

function renderNews() {
  const result = filteredNews();
  const visible = result.slice(0, state.visible);
  elements.grid.innerHTML = '';

  for (const item of visible) {
    const fragment = elements.template.content.cloneNode(true);
    const card = fragment.querySelector('.news-card');
    const type = item.type || 'news';
    card.classList.add(type);

    const thumbnailLink = fragment.querySelector('.thumbnail-link');
    const thumbnail = fragment.querySelector('.news-thumbnail');
    thumbnailLink.href = item.url;
    if (item.thumbnail) {
      thumbnail.src = item.thumbnail;
      thumbnail.alt = `${item.title} 썸네일`;
    }

    const typeBadge = fragment.querySelector('.type-badge');
    typeBadge.textContent = type === 'youtube'
      ? (item.summary_source === 'transcript' ? 'YouTube 요약' : 'YouTube 설명')
      : (item.official ? '공식기관' : 'NEWS');

    fragment.querySelector('.country-badge').textContent = item.country || '지역 미상';
    fragment.querySelector('.category-badge').textContent = item.category || '기타';

    const importance = fragment.querySelector('.importance-badge');
    importance.textContent = item.importance_score >= 80 ? '핵심' : item.importance_score >= 60 ? '주요' : '일반';
    if (item.importance_score < 60 && !item.official) importance.hidden = true;

    const link = fragment.querySelector('.news-link');
    link.textContent = item.title;
    link.href = item.url;

    const description = fragment.querySelector('.news-description');
    description.textContent = item.description || '원문에서 자세한 내용을 확인하세요.';
    if (type === 'youtube' && item.summary_source === 'transcript') {
      description.setAttribute('aria-label', '영상 자막 기반 자동 요약');
    }

    const sourceText = item.institution && item.institution !== item.source
      ? `${item.institution} · ${item.source || '출처 미상'}`
      : (item.source || item.institution || '출처 미상');
    fragment.querySelector('.news-source').textContent = sourceText;

    const time = fragment.querySelector('.news-date');
    time.dateTime = item.published_at;
    time.textContent = dateFormatter.format(new Date(item.published_at));
    elements.grid.appendChild(fragment);
  }

  elements.empty.hidden = result.length > 0;
  elements.loadMore.hidden = result.length <= state.visible;
  const countryLabel = state.country === 'all' ? '전체 지역' : state.country;
  elements.resultInfo.textContent = `${countryLabel} · ${state.period === 1 ? '24시간' : `${state.period}일`} 기준 · ${result.length.toLocaleString('ko-KR')}건 표시`;
}

function resetAndRender() {
  state.visible = 18;
  renderNews();
}

function bind(container, key, dataKey, transform = value => value) {
  $(container).addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    document.querySelectorAll(`${container} button`).forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    state[key] = transform(button.dataset[dataKey]);
    resetAndRender();
  });
}

function setPeriod(period) {
  state.period = period;
  document.querySelectorAll('#periodFilters button').forEach(button => {
    button.classList.toggle('active', Number(button.dataset.period) === period);
  });
  document.querySelectorAll('#quickPeriods button').forEach(button => {
    button.classList.toggle('active', Number(button.dataset.quickPeriod) === period);
  });
  resetAndRender();
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

bind('#typeFilters', 'type', 'type');
bind('#countryFilters', 'country', 'country');
bind('#categoryFilters', 'category', 'category');
bind('#periodFilters', 'period', 'period', Number);

$('#quickPeriods').addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button) setPeriod(Number(button.dataset.quickPeriod));
});

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
elements.reset.addEventListener('click', () => {
  state.type = 'all';
  state.country = 'all';
  state.category = 'all';
  state.query = '';
  state.sort = 'importance';
  elements.search.value = '';
  elements.sort.value = 'importance';
  ['#typeFilters', '#countryFilters', '#categoryFilters'].forEach(selector => {
    document.querySelectorAll(`${selector} button`).forEach((button, index) => {
      button.classList.toggle('active', index === 0);
    });
  });
  setPeriod(7);
});

loadNews();
