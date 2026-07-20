const GROUP_RELATED_RULES={
  '한화':['한화','한화에어로스페이스','한화오션','한화시스템','한화솔루션','한화생명','한화손해보험','한화투자증권','한화갤러리아','한화엔진'],
  'GS':['GS건설','GS리테일','GS칼텍스','GS에너지','GS EPS','GS글로벌','GS홈쇼핑','GS그룹'],
  'LG':['LG','LG전자','LG화학','LG에너지솔루션','LG이노텍','LG디스플레이','LG유플러스','LG생활건강','LG씨엔에스','LG CNS'],
  'LX':['LX','LX하우시스','LX인터내셔널','LX세미콘','LX판토스','LX홀딩스']
};
const GROUP_CATEGORY_ORDER=['기업공시','경제정책','금융시장','건설·부동산','경제 유튜브'];
const groupRelatedState={news:[]};

function groupEscape(value){return String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));}
function groupSafeUrl(value){try{const url=new URL(String(value));return ['http:','https:'].includes(url.protocol)?url.href:'#';}catch{return '#';}}
function groupHoursSince(value){return(Date.now()-new Date(value).getTime())/3600000;}
function selectedGroupPeriod(){const active=document.querySelector('#quickPeriods .period-tab.active');return Number(active?.dataset.quickPeriod||1);}
function selectedGroupCategory(){return document.querySelector('#categoryFilters .filter-button.active')?.dataset.category||'all';}
function groupMatches(item,keywords){const text=`${item.title||''} ${item.description||''} ${item.source||''}`.toLowerCase();return keywords.some(keyword=>text.includes(keyword.toLowerCase()));}
function groupDate(value){try{return new Intl.DateTimeFormat('ko-KR',{month:'numeric',day:'numeric'}).format(new Date(value));}catch{return '';}}

function renderGroupRelatedSidebar(){
  const target=document.querySelector('#groupRelatedList');
  const summary=document.querySelector('#groupRelatedSummary');
  if(!target||!summary)return;
  const period=selectedGroupPeriod();
  const category=selectedGroupCategory();
  const periodItems=groupRelatedState.news.filter(item=>item.country==='대한민국'&&groupHoursSince(item.published_at)<=period*24&&(category==='all'||item.category===category));
  let total=0;
  const blocks=Object.entries(GROUP_RELATED_RULES).map(([group,keywords])=>{
    const matched=periodItems.filter(item=>groupMatches(item,keywords)).sort((a,b)=>new Date(b.published_at)-new Date(a.published_at));
    total+=matched.length;
    const grouped=GROUP_CATEGORY_ORDER.map(categoryName=>[categoryName,matched.filter(item=>item.category===categoryName)]).filter(([,items])=>items.length);
    const content=grouped.length?grouped.map(([categoryName,items])=>`<div class="group-category"><span class="group-category-label">${groupEscape(categoryName)}</span>${items.slice(0,4).map(item=>`<a class="group-related-item" href="${groupSafeUrl(item.url)}" target="_blank" rel="noopener noreferrer"><strong>${groupEscape(item.title)}</strong><span>${groupEscape(item.source||'출처 미상')} · ${groupDate(item.published_at)}</span></a>`).join('')}</div>`).join(''):'<p class="group-related-empty">선택 기간·카테고리에 해당하는 내용이 없습니다.</p>';
    return `<section class="group-related-block"><div class="group-related-title"><strong>${groupEscape(group)} 관련</strong><span class="group-related-count">${matched.length}</span></div>${content}</section>`;
  });
  summary.textContent=`${period===1?'최근 24시간':`최근 ${period}일`} · ${category==='all'?'전체 카테고리':category} · ${total}건`;
  target.innerHTML=blocks.join('');
}

async function loadGroupRelatedNews(){
  try{
    const response=await fetch(`data/news.json?t=${Date.now()}`,{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const payload=await response.json();
    groupRelatedState.news=Array.isArray(payload.news)?payload.news:[];
    renderGroupRelatedSidebar();
  }catch(error){
    const target=document.querySelector('#groupRelatedList');
    if(target)target.innerHTML=`<p class="group-related-empty">그룹 관련 목록을 불러오지 못했습니다: ${groupEscape(error.message)}</p>`;
  }
}

document.addEventListener('DOMContentLoaded',()=>{
  document.querySelector('#quickPeriods')?.addEventListener('click',()=>setTimeout(renderGroupRelatedSidebar,0));
  document.querySelector('#categoryFilters')?.addEventListener('click',()=>setTimeout(renderGroupRelatedSidebar,0));
  document.querySelector('#resetButton')?.addEventListener('click',()=>setTimeout(renderGroupRelatedSidebar,0));
  document.querySelector('#refreshButton')?.addEventListener('click',loadGroupRelatedNews);
  loadGroupRelatedNews();
});
