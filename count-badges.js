function updateCountBadges(){
  const items=(Array.isArray(state?.news)?state.news:[]).filter(item=>item.country==='대한민국');
  const withinDays=days=>items.filter(item=>hoursSince(item.published_at)<=days*24);
  const daily=withinDays(1);
  const weekly=withinDays(7);
  const dailyBadge=document.querySelector('#dailyBestCount');
  const weeklyBadge=document.querySelector('#weeklyBestCount');
  if(dailyBadge)dailyBadge.textContent=daily.length.toLocaleString('ko-KR');
  if(weeklyBadge)weeklyBadge.textContent=weekly.length.toLocaleString('ko-KR');

  const currentItems=state.period===1?daily:weekly;
  document.querySelectorAll('[data-count-category]').forEach(badge=>{
    const category=badge.dataset.countCategory;
    const count=category==='all'?currentItems.length:currentItems.filter(item=>item.category===category).length;
    badge.textContent=count.toLocaleString('ko-KR');
  });
}

document.addEventListener('DOMContentLoaded',()=>{
  updateCountBadges();
  const updatedAt=document.querySelector('#updatedAt');
  if(updatedAt)new MutationObserver(()=>updateCountBadges()).observe(updatedAt,{childList:true,subtree:true});
  document.addEventListener('click',event=>{
    if(event.target.closest('#quickPeriods button')||event.target.closest('#resetButton')){
      setTimeout(updateCountBadges,0);
    }
  });
});
