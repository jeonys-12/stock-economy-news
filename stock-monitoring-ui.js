const stockMonitoringState={payload:null,filter:'all',query:''};

function diagnosticEscape(value){
  return String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function diagnosticStatus(value){
  const status=String(value?.status||'unavailable').toLowerCase();
  if(status==='ok'||status==='cached')return {key:'ok',label:status==='cached'?'캐시':'정상'};
  if(status==='failed')return {key:'failed',label:'실패'};
  return {key:'unavailable',label:'자료 없음'};
}

function diagnosticReason(value,fallback){
  if(!value||typeof value!=='object')return fallback;
  return String(value.reason||value.error||value.message||fallback||'세부 원인이 기록되지 않았습니다.');
}

function metricAvailable(value){
  return value!==null&&value!==undefined&&value!=='';
}

function stockDiagnosticRow(name,row){
  const market=row.market&&typeof row.market==='object'?row.market:{};
  const financials=row.financials&&typeof row.financials==='object'?row.financials:{};
  const consensus=row.consensus&&typeof row.consensus==='object'?row.consensus:{};
  const quantitative=row.quantitative&&typeof row.quantitative==='object'?row.quantitative:{};
  const valuation=market.valuation&&typeof market.valuation==='object'?market.valuation:{};
  const flow=market.investor_flow&&typeof market.investor_flow==='object'?market.investor_flow:{};
  const marketStatus=diagnosticStatus(market);
  const dartStatus=diagnosticStatus(financials);
  const consensusStatus=diagnosticStatus(consensus);
  const valuationOk=metricAvailable(valuation.per)||metricAvailable(valuation.pbr)||metricAvailable(valuation.eps)||metricAvailable(valuation.bps);
  const flowOk=metricAvailable(flow.foreign_net_buy_10d_krw)||metricAvailable(flow.institution_net_buy_10d_krw)||metricAvailable(flow.individual_net_buy_10d_krw);
  const dimensions=Number(quantitative.available_dimensions||0);
  const score=Number(quantitative.score||0);
  const problems=[];
  if(marketStatus.key!=='ok')problems.push(`가격: ${diagnosticReason(market,'가격 데이터를 수집하지 못했습니다.')}`);
  if(dartStatus.key!=='ok')problems.push(`OpenDART: ${diagnosticReason(financials,'재무 데이터를 수집하지 못했습니다.')}`);
  if(consensusStatus.key!=='ok')problems.push(`FnGuide: ${diagnosticReason(consensus,'컨센서스 데이터를 수집하지 못했습니다.')}`);
  if(!valuationOk)problems.push(`밸류에이션: ${diagnosticReason(valuation,'PER·PBR 등 유효 값이 없습니다.')}`);
  if(!flowOk)problems.push(`수급: ${diagnosticReason(flow,'외국인·기관 순매수 유효 값이 없습니다.')}`);
  if(dimensions<2)problems.push(`추천조건: 유효 데이터 축이 ${dimensions}개로 최소 2개에 미달합니다.`);
  const overall=problems.length===0?'ok':marketStatus.key==='failed'||dartStatus.key==='failed'||consensusStatus.key==='failed'?'failed':'warning';
  return {name,row,market,financials,consensus,quantitative,valuation,flow,marketStatus,dartStatus,consensusStatus,valuationOk,flowOk,dimensions,score,problems,overall};
}

function diagnosticBadge(status,label){
  return `<span class="diagnostic-badge ${status}">${diagnosticEscape(label)}</span>`;
}

function compactNumber(value,suffix=''){
  const number=Number(value);
  if(!Number.isFinite(number))return '-';
  return `${number.toLocaleString('ko-KR')}${suffix}`;
}

function renderGlobalDiagnostics(payload){
  const container=document.querySelector('#stockGlobalStatus');
  if(!container)return;
  const status=payload?.source_status&&typeof payload.source_status==='object'?payload.source_status:{};
  const entries=[
    ['OpenDART',status.opendart],
    ['FnGuide',status.fnguide],
    ['KRX 공식 API',status.krx_open_api],
    ['PER·PBR·수급 보완',status.public_market_fallback],
    ['데이터 보정',status.data_quality_repair],
    ['종목 목록',status.stock_universe],
  ];
  container.innerHTML=entries.map(([name,value])=>{
    const info=value&&typeof value==='object'?value:{};
    let state='unavailable';
    let label='확인 불가';
    if(info.status==='ok'||(name==='FnGuide'&&info.mode)){state='ok';label='정상';}
    else if(info.status==='failed'){state='failed';label='실패';}
    else if(name==='종목 목록'&&Number(info.total_stocks)>0){state='ok';label=`${info.total_stocks}개`;}
    let fallback=name==='FnGuide'?`정상 캐시 재사용 ${Number(info.cached_stocks_reused||0)}개`:'세부 상태 없음';
    if(name==='PER·PBR·수급 보완')fallback=`PER·PBR ${Number(info.valuation_fresh||0)}개, 수급 ${Number(info.investor_flow_fresh||0)}개 신규 수집`;
    const reason=diagnosticReason(info,fallback);
    return `<article class="global-diagnostic-card"><div><strong>${diagnosticEscape(name)}</strong>${diagnosticBadge(state,label)}</div><p>${diagnosticEscape(reason)}</p></article>`;
  }).join('');
}

function renderStockDiagnostics(){
  const list=document.querySelector('#stockDiagnosticsList');
  const summary=document.querySelector('#stockDiagnosticsSummary');
  if(!list||!summary)return;
  const stocks=stockMonitoringState.payload?.stocks&&typeof stockMonitoringState.payload.stocks==='object'?stockMonitoringState.payload.stocks:{};
  const diagnostics=Object.entries(stocks)
    .map(([name,row])=>stockDiagnosticRow(name,row))
    .sort((a,b)=>b.score-a.score||b.dimensions-a.dimensions||a.name.localeCompare(b.name,'ko'));
  const query=stockMonitoringState.query.trim().toLowerCase();
  const filtered=diagnostics.filter(item=>{
    const text=`${item.name} ${item.row.code||''} ${item.row.sector||''}`.toLowerCase();
    const filterOk=stockMonitoringState.filter==='all'||item.overall===stockMonitoringState.filter;
    return filterOk&&(!query||text.includes(query));
  });
  const normal=diagnostics.filter(item=>item.overall==='ok').length;
  const warning=diagnostics.filter(item=>item.overall==='warning').length;
  const failed=diagnostics.filter(item=>item.overall==='failed').length;
  summary.innerHTML=`전체 <b>${diagnostics.length}</b>개 · 정상 <b>${normal}</b> · 일부 누락 <b>${warning}</b> · 실패 <b>${failed}</b> · 현재 표시 <b>${filtered.length}</b> · 점수 높은 순`;
  if(!filtered.length){list.innerHTML='<p class="result-info">조건에 맞는 종목 진단 결과가 없습니다.</p>';return;}
  list.innerHTML=filtered.map(item=>{
    const code=diagnosticEscape(item.row.code||'');
    const sector=diagnosticEscape(item.row.business_sector||item.row.sector||'업종 미분류');
    const scoreClass=item.score>=15?'positive':item.score<=-15?'negative':'neutral';
    const overallLabel=item.overall==='ok'?'정상':item.overall==='failed'?'수집 실패':'일부 누락';
    const overallClass=item.overall==='warning'?'unavailable':item.overall;
    const problemMarkup=item.problems.length?`<ul>${item.problems.map(reason=>`<li>${diagnosticEscape(reason)}</li>`).join('')}</ul>`:'<p class="diagnostic-success">모든 필수 데이터 항목이 정상적으로 확보됐습니다.</p>';
    const valuationSource=item.valuation.source?` · ${diagnosticEscape(item.valuation.source)}`:'';
    const flowSource=item.flow.source?` · ${diagnosticEscape(item.flow.source)}`:'';
    const flowUnit=item.flow.unit==='shares'?'주':'원';
    return `<details class="stock-diagnostic-item ${item.overall}" ${item.overall==='failed'?'open':''}>
      <summary>
        <div class="diagnostic-stock-name"><strong>${diagnosticEscape(item.name)}</strong><span>${code} · ${sector}</span></div>
        <div class="diagnostic-summary-badges">
          ${diagnosticBadge(overallClass,overallLabel)}
          <span class="score-chip ${scoreClass}">점수 ${compactNumber(item.score)}</span>
          <span class="dimension-chip">데이터 ${item.dimensions}개 축</span>
        </div>
      </summary>
      <div class="diagnostic-detail">
        <div class="diagnostic-source-grid">
          <div><span>가격</span>${diagnosticBadge(item.marketStatus.key,item.marketStatus.label)}<small>${item.marketStatus.key==='ok'?`현재가 ${compactNumber(item.market.current_price,'원')} · 기준 ${diagnosticEscape(item.market.as_of||'-')}`:diagnosticEscape(diagnosticReason(item.market,'가격 수집 실패'))}</small></div>
          <div><span>OpenDART 재무</span>${diagnosticBadge(item.dartStatus.key,item.dartStatus.label)}<small>${item.dartStatus.key==='ok'?`${diagnosticEscape(item.financials.business_year||'')} ${diagnosticEscape(item.financials.report_name||'')} · 영업이익 증감 ${compactNumber(item.financials.operating_profit_growth_pct,'%')}`:diagnosticEscape(diagnosticReason(item.financials,'재무 수집 실패'))}</small></div>
          <div><span>FnGuide 컨센서스</span>${diagnosticBadge(item.consensusStatus.key,item.consensusStatus.label)}<small>${item.consensusStatus.key==='ok'?`목표주가 ${compactNumber(item.consensus.target_price,'원')} · 의견 ${diagnosticEscape(item.consensus.opinion||'-')}`:diagnosticEscape(diagnosticReason(item.consensus,'컨센서스 수집 실패'))}</small></div>
          <div><span>PER·PBR</span>${diagnosticBadge(item.valuationOk?'ok':'unavailable',item.valuationOk?'정상':'자료 없음')}<small>PER ${compactNumber(item.valuation.per,'배')} · PBR ${compactNumber(item.valuation.pbr,'배')}${valuationSource}</small></div>
          <div><span>외국인·기관 수급</span>${diagnosticBadge(item.flowOk?'ok':'unavailable',item.flowOk?'정상':'자료 없음')}<small>외국인 ${compactNumber(item.flow.foreign_net_buy_10d_krw,flowUnit)} · 기관 ${compactNumber(item.flow.institution_net_buy_10d_krw,flowUnit)}${flowSource}</small></div>
          <div><span>정량 추천 조건</span>${diagnosticBadge(item.dimensions>=2?'ok':'unavailable',item.dimensions>=2?'충족':'미충족')}<small>종합점수 ${compactNumber(item.score)} · 유효 축 ${item.dimensions}개</small></div>
        </div>
        <div class="diagnostic-problems"><strong>문제점 및 수정 단서</strong>${problemMarkup}</div>
      </div>
    </details>`;
  }).join('');
}

async function loadStockDiagnostics(){
  const list=document.querySelector('#stockDiagnosticsList');
  if(list)list.innerHTML='<p class="result-info">종목별 수집 결과를 불러오는 중입니다.</p>';
  try{
    const response=await fetch(`data/stock_data.json?t=${Date.now()}`,{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    stockMonitoringState.payload=await response.json();
    const updated=document.querySelector('#stockDiagnosticsUpdatedAt');
    if(updated){
      updated.textContent=stockMonitoringState.payload.updated_at?new Intl.DateTimeFormat('ko-KR',{dateStyle:'medium',timeStyle:'short'}).format(new Date(stockMonitoringState.payload.updated_at)):'업데이트 정보 없음';
    }
    renderGlobalDiagnostics(stockMonitoringState.payload);
    renderStockDiagnostics();
  }catch(error){
    if(list)list.innerHTML=`<p class="result-info">stock_data.json을 불러오지 못했습니다: ${diagnosticEscape(error.message)}</p>`;
    const global=document.querySelector('#stockGlobalStatus');
    if(global)global.innerHTML='<article class="global-diagnostic-card"><div><strong>종목 데이터</strong><span class="diagnostic-badge failed">로드 실패</span></div><p>GitHub Actions와 GitHub Pages 배포 상태를 확인하십시오.</p></article>';
  }
}

document.addEventListener('DOMContentLoaded',()=>{
  const filters=document.querySelector('#stockDiagnosticFilters');
  if(filters)filters.addEventListener('click',event=>{
    const button=event.target.closest('button');
    if(!button)return;
    filters.querySelectorAll('button').forEach(item=>item.classList.toggle('active',item===button));
    stockMonitoringState.filter=button.dataset.stockStatus||'all';
    renderStockDiagnostics();
  });
  const search=document.querySelector('#stockDiagnosticSearch');
  if(search)search.addEventListener('input',event=>{
    stockMonitoringState.query=event.target.value;
    renderStockDiagnostics();
  });
  const refresh=document.querySelector('#stockDiagnosticRefresh');
  if(refresh)refresh.addEventListener('click',loadStockDiagnostics);
  loadStockDiagnostics();
});