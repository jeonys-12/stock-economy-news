const URGENT_KEYWORDS=[
  '긴급','속보','긴급공시','거래정지','상장폐지','부도','파산','회생절차','채무불이행','디폴트','압수수색','영업정지'
];

function isUrgentNews(item){
  if(item.urgent===true||item.priority==='urgent'||item.importance_level==='urgent')return true;
  const text=`${item.title||''} ${item.description||''}`.toLowerCase();
  return URGENT_KEYWORDS.some(keyword=>text.includes(keyword.toLowerCase()));
}

function isOfficialDisclosure(item){
  if(item.category!=='기업공시')return false;
  const method=(item.collection_method||'').toLowerCase();
  const source=`${item.source||''} ${item.source_domain||''}`.toLowerCase();
  return method==='official_api'||method==='official_rss'||source.includes('dart')||source.includes('kind')||source.includes('금융감독원')||source.includes('한국거래소');
}

function monitoringPriorityCompare(a,b){
  const urgentDiff=Number(isUrgentNews(b))-Number(isUrgentNews(a));
  if(urgentDiff)return urgentDiff;

  const disclosureDiff=Number(isOfficialDisclosure(b))-Number(isOfficialDisclosure(a));
  if(disclosureDiff)return disclosureDiff;

  const importanceDiff=Number(b.importance_score||0)-Number(a.importance_score||0);
  if(importanceDiff)return importanceDiff;

  const latestDiff=new Date(b.published_at||0)-new Date(a.published_at||0);
  if(latestDiff)return latestDiff;

  return String(a.title||'').localeCompare(String(b.title||''),'ko');
}

filteredNews=function(){
  const max=state.period*24;
  const q=state.query.trim().toLowerCase();
  const result=state.news.filter(item=>{
    const type=item.type||'news';
    const text=`${item.title} ${item.description||''} ${item.source||''}`.toLowerCase();
    return item.country==='대한민국'
      &&(state.type==='all'||type===state.type)
      &&(state.category==='all'||item.category===state.category)
      &&hoursSince(item.published_at)<=max
      &&(!q||text.includes(q));
  });

  return result.sort(state.sort==='latest'
    ?(a,b)=>new Date(b.published_at||0)-new Date(a.published_at||0)
    :monitoringPriorityCompare);
};
