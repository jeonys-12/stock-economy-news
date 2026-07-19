function formatMetric(value,suffix=''){
  if(value===null||value===undefined||value==='')return null;
  const number=Number(value);
  return Number.isFinite(number)?`${number.toLocaleString('ko-KR')}${suffix}`:null;
}

aiCandidateMarkup=function(item,type){
  const action=type==='buy'?'관심·분할매수 검토':'비중 축소·매도 검토';
  const evidence=(item.evidence||[])[0];
  const metrics=item.metrics||{};
  const metricParts=[
    item.quantitative_score!==undefined?`종합점수 ${formatMetric(item.quantitative_score)}`:null,
    metrics.current_price?`현재가 ${formatMetric(metrics.current_price,'원')}`:null,
    metrics.return_20d_pct!==null&&metrics.return_20d_pct!==undefined?`20일 ${formatMetric(metrics.return_20d_pct,'%')}`:null,
    metrics.per?`PER ${formatMetric(metrics.per)}배`:null,
    metrics.pbr?`PBR ${formatMetric(metrics.pbr)}배`:null,
    item.data_dimensions?`데이터 ${formatMetric(item.data_dimensions)}개 축`:null,
  ].filter(Boolean);
  return `<article class="recommendation-item"><div><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.code||'')} ${item.sector?'· '+escapeHtml(item.sector):''}</span></div><span class="recommendation-action ${type}">${action}</span>${metricParts.length?`<div class="factor-metrics">${metricParts.map(x=>`<span>${escapeHtml(x)}</span>`).join('')}</div>`:''}<p>${escapeHtml(item.reason)}${item.risk?`<br><b>반대 시나리오:</b> ${escapeHtml(item.risk)}`:''}${evidence?`<br><b>뉴스 근거:</b> <a href="${safeUrl(evidence.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(evidence.title)}</a>`:''}</p></article>`;
};

renderInvestmentBriefing=function(){
  if(!elements.marketSignal)return;
  elements.briefingPeriod.textContent=state.period===1?'최근 24시간 기준':`최근 ${state.period}일 기준`;
  const brief=state.period===1?state.aiBriefings?.daily:state.aiBriefings?.weekly;
  const analysisType=String(state.aiBriefings?.analysis_type||'');
  if(brief&&analysisType.startsWith('openai'))renderAiBriefing(brief);
  else renderRuleBriefing();
};
