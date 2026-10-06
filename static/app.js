'use strict';

const $ = (selector) => document.querySelector(selector);
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {view:'discover', results:[], library:[], current:null, tab:'paper', messages:[],
  search:null, total:0, source:null, warning:null, searchBusy:false, detailLoading:false,
  detailRequest:0, jobs:new Map(), drafts:new Map(), config:null, uploadTarget:null,
  editingId:null, deletingId:null, toastTimer:null};

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  const data = await response.json().catch(() => ({error:'The server returned an unreadable response.'}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function toast(message, error = false) {
  clearTimeout(state.toastTimer);
  $('#toast').textContent = message;
  $('#toast').className = `toast${error ? ' error' : ''}`;
  state.toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), error ? 9000 : 4500);
}

const safe = (handler) => async (event) => {
  try { await handler(event); } catch (error) { toast(error.message || 'Something went wrong.', true); }
};
function toggle(element, visible) { element.classList.toggle('hidden', !visible); }
function language() { return 'en'; }
function savedId(paper) { return paper?.id || paper?.saved_id; }
function currentList() { return state.view === 'library' ? filteredLibrary() : state.results; }
function filteredLibrary() {
  const query = $('#library-filter').value.trim().toLowerCase();
  return state.library.filter((p) => `${p.title} ${p.authors.join(' ')} ${p.abstract}`.toLowerCase().includes(query));
}
function authorsLabel(paper) {
  return paper.authors?.length ? paper.authors.join(', ') : 'Authors not provided';
}
function cleanURL(url) {
  try { const parsed = new URL(url); return ['http:','https:'].includes(parsed.protocol) ? parsed.href : ''; }
  catch { return ''; }
}

function inlineMarkdown(value) {
  return esc(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[p\.\s*(\d+)(?:\s*[-–]\s*(\d+))?\]/g, (_, page, endPage) => {
      const citation = (n) => `<button class="page-citation" data-page="${n}" title="Read source page ${n}">p. ${n}</button>`;
      return citation(page) + (endPage ? '–' + citation(endPage) : '');
    });
}
function markdown(value) {
  // Render a safe, small Markdown subset. Raw HTML and external links stay text.
  const lines = String(value || '').split('\n');
  let html = '', list = null, paragraph = [];
  function flushParagraph() { if (paragraph.length) { html += `<p>${inlineMarkdown(paragraph.join(' '))}</p>`; paragraph = []; } }
  function closeList() { if (list) { html += `</${list}>`; list = null; } }
  for (const line of lines) {
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (!line.trim()) { flushParagraph(); closeList(); }
    else if (heading) { flushParagraph(); closeList(); html += `<h3>${inlineMarkdown(heading[1])}</h3>`; }
    else if (bullet || ordered) {
      flushParagraph();
      const type = ordered ? 'ol' : 'ul';
      if (list !== type) { closeList(); html += `<${type}>`; list = type; }
      html += `<li>${inlineMarkdown((bullet || ordered)[1])}</li>`;
    } else { closeList(); paragraph.push(line.trim()); }
  }
  flushParagraph(); closeList();
  return html;
}

function renderStart() {
  const topics = ['Attention is all you need','Retrieval augmented generation','Graph neural networks','Diffusion models'];
  return `<div class="topics">${topics.map((t) => `<button class="topic" data-topic="${esc(t)}">${icon('search')}${esc(t)}</button>`).join('')}</div>
    <div class="discover-intro"><span class="eyebrow">LESS NOISE, MORE INSIGHT</span><h3>Good research starts<br>with a good question.</h3><p>Explore the literature, build your reading list,<br>and let a little AI help you go deeper.</p><span class="intro-art">${icon('book')}</span></div>
    <div class="workflow"><div class="workflow-step"><span>01 / DISCOVER</span><h4>Follow a thread</h4><p>Search real papers by<br>topic, title, or arXiv ID.</p></div><div class="workflow-step"><span>02 / COLLECT</span><h4>Make it yours</h4><p>Save a paper or upload<br>a PDF to your library.</p></div><div class="workflow-step"><span>03 / UNDERSTAND</span><h4>Go a little deeper</h4><p>Get a grounded summary<br>and ask better questions.</p></div></div>
    <div class="text-tools"><button data-action="upload">${icon('upload')}Or start with your own PDF</button><button data-action="settings">${icon('settings')}Connect your AI</button></div>`;
}

function empty(iconName, title, description, extra = '') {
  return `<div class="empty-state">${icon(iconName)}<h3>${esc(title)}</h3><p>${esc(description)}</p>${extra}</div>`;
}

function renderCards() {
  const papers = currentList();
  const selected = state.current;
  if (state.view === 'discover' && state.searchBusy) {
    $('#paper-list').innerHTML = '<div class="loading-state"><span class="spinner"></span>Searching the literature…</div>';
    return;
  }
  if (!papers.length) {
    $('#paper-list').innerHTML = state.view === 'library'
      ? empty('library', state.library.length ? 'Nothing matches yet.' : 'A library of possibilities.',
        state.library.length ? 'Try a different title, author, or keyword.' : 'Save a paper from Discover, or bring one of your own.',
        '<button class="button button-primary" data-action="upload">Upload a PDF</button>')
      : state.search ? empty('search','A different direction?','No papers matched this search. Try broader keywords or another source.') : renderStart();
    return;
  }
  $('#paper-list').innerHTML = papers.map((paper, index) => {
    const id = savedId(paper);
    const isSelected = selected && ((id && id === savedId(selected)) || (paper.external_id && paper.external_id === selected.external_id));
    const url = cleanURL(paper.url);
    return `<article class="paper-card${isSelected ? ' selected' : ''}" data-index="${index}" tabindex="0" aria-label="Read ${esc(paper.title)}">
      <div class="card-topline"><span class="source-tag">${esc(paper.source === 'upload' ? 'PDF upload' : paper.source)}</span><span class="card-year">${esc(paper.year || 'Year unknown')}</span>${paper.has_content ? `<span class="card-content-label">${icon('file')}${paper.page_count} pages</span>` : ''}</div>
      <h3>${esc(paper.title)}</h3><p class="authors">${esc(authorsLabel(paper))}</p>
      <p class="card-abstract">${esc(paper.abstract || 'An abstract is not available from this source. Open the paper or attach its PDF for more detail.')}</p>
      <div class="card-bottom">${url ? `<a class="paper-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open paper ${icon('link')}</a>` : `<span class="paper-link">${icon('file')}Your uploaded paper</span>`}
      ${id ? `<button class="saved-button" data-action="select">${icon('check')}${state.view === 'library' ? 'Read paper' : 'In your library'}</button>` : `<button class="button button-outline button-small" data-action="save">${icon('plus')}Save paper</button>`}</div></article>`;
  }).join('');
}

function renderWorkspace() {
  const library = state.view === 'library';
  $('#nav-library').classList.toggle('active', library);
  $('#nav-discover').classList.toggle('active', !library);
  $('#breadcrumb-current').textContent = library ? 'My library' : 'Discover';
  $('#page-eyebrow').textContent = library ? 'THE IDEAS YOU KEEP' : 'FOLLOW YOUR CURIOSITY';
  $('#page-title').textContent = library ? 'Your growing collection.' : 'Find your next idea.';
  $('#page-description').textContent = library ? 'A reading list today. A new perspective tomorrow.' : 'Discover papers. Connect the dots. Understand more.';
  toggle($('#search-section'), !library);
  toggle($('#library-filter-form'), library);
  toggle($('#search-warning'), !library && Boolean(state.warning));
  $('#search-warning').textContent = state.warning || '';
  $('#library-count').textContent = state.library.length;
  $('#results-title').textContent = library ? 'Saved papers' : state.search ? 'Search results' : 'Start somewhere interesting';
  $('#results-count').textContent = library ? `${filteredLibrary().length} ${filteredLibrary().length === 1 ? 'paper' : 'papers'}`
    : state.search ? `${state.total.toLocaleString()} matches · ${state.source || 'searching'}` : 'A few ideas to get you going';
  const paging = !library && state.search && state.results.length > 0 && !state.searchBusy;
  toggle($('#pagination'), paging);
  if (paging) {
    $('#previous-page').disabled = state.search.start === 0;
    $('#next-page').disabled = state.search.start + 12 >= state.total || state.search.start >= 9888;
    $('#page-label').textContent = `${state.search.start + 1}–${state.search.start + state.results.length}`;
  }
  renderCards();
}

function renderReaderWelcome() {
  return `<div class="reader-welcome"><div class="welcome-art"><div class="welcome-sheet back"></div><div class="welcome-sheet"></div><span class="welcome-star">✦</span><span class="welcome-dot">✧</span></div><span class="eyebrow">A FRESH PERSPECTIVE</span><h2>Every paper has<br>a story to tell.</h2><p>Select a paper to explore its ideas,<br>get a clear summary, and ask the<br>questions that move your work forward.</p><div class="reader-capabilities"><span>${icon('file')}Full-text paper summaries</span><span>${icon('chat')}Questions grounded in the paper</span><span>${icon('check')}Page citations you can check</span></div></div>`;
}

function suggestions() {
  const questions = ['What problem does this paper address?','What is the main idea of the approach?','What datasets and baselines are used?','What are the major limitations?'];
  return `<div class="question-suggestions">${questions.map((q) => `<button data-question="${esc(q)}">${esc(q)}${icon('arrow')}</button>`).join('')}</div>`;
}

function renderMessage(message) {
  return `<div class="message"><p class="user-question">${esc(message.question)}</p><div class="markdown">${markdown(message.answer)}</div>
    <div class="message-meta">${icon('spark')} ${esc(message.model)}</div><details class="source-list"><summary>${message.sources.length} source passages</summary>
    ${message.sources.map((s) => `<div class="source-excerpt"><button class="page-citation" data-page="${s.page}">Page ${s.page}</button><p>${esc(s.text)}</p></div>`).join('')}</details></div>`;
}

function renderReader() {
  const paper = state.current;
  if (!paper) { $('#reader-content').innerHTML = renderReaderWelcome(); return; }
  if (state.detailLoading) { $('#reader-content').innerHTML = '<div class="loading-state"><span class="spinner"></span>Opening your paper…</div>'; return; }
  const id = savedId(paper), url = cleanURL(paper.url);
  const job = [...state.jobs.values()].find((j) => j.paper_id === id);
  const progress = job ? `<div class="job-progress" role="status"><span class="spinner"></span><span>${esc(job.progress)}</span></div>` : '';
  let body = '';
  if (!id) {
    body = `<div class="section-label">Abstract</div><p class="detail-abstract">${esc(paper.abstract || 'Abstract not provided by this source.')}</p><div class="ai-empty">${icon('library')}<h3>An idea worth keeping?</h3><p>Save this paper to your library to generate a full-text summary and ask questions.</p><button class="button button-primary" data-reader-action="save">${icon('plus')}Save to my library</button></div>`;
  } else if (state.tab === 'paper') {
    body = `<div class="section-label">Abstract</div><p class="detail-abstract">${esc(paper.abstract || 'No abstract was detected. You can add one in Paper details.')}</p>
      <div class="content-status${paper.has_content ? '' : ' missing'}">${icon('file')}<span>${paper.has_content ? `${paper.page_count} pages of full text are ready for analysis.` : paper.source === 'arxiv' ? 'The full arXiv PDF will be fetched when you start an analysis.' : 'Attach the full paper PDF to enable AI analysis.'}</span></div>
      <div class="text-tools">${paper.has_content ? `<button data-reader-action="text">${icon('file')}Read extracted text</button><a href="/api/papers/${id}/pdf" target="_blank" rel="noopener noreferrer">Original PDF ${icon('link')}</a>` : `<button data-reader-action="attach">${icon('upload')}Attach PDF</button>`}</div>${progress}`;
  } else if (state.tab === 'summary') {
    body = progress + (paper.summary && paper.summary_language === language()
      ? `<div class="summary-toolbar"><span>Generated with ${esc(paper.summary_model)} · English</span><button class="button button-outline button-small" data-reader-action="summarize"${job ? ' disabled' : ''}>Regenerate</button></div><div class="markdown">${markdown(paper.summary)}</div><p class="ai-fineprint">AI-generated from the paper's extracted text. Click a page citation to check the source.</p>`
      : `<div class="ai-empty">${icon('spark')}<h3>The big picture,<br>without losing the details.</h3><p>Get the problem, approach, evidence, and limitations — grounded in the full paper.</p><button class="button button-primary" data-reader-action="summarize"${job ? ' disabled' : ''}>${icon('spark')}Generate summary</button><p class="ai-fineprint">${state.config?.llm_configured ? 'Longer papers may take a few minutes.' : 'Connect a model to get started.'}</p></div>`);
  } else {
    body = state.messages.map(renderMessage).join('') + progress;
    if (!state.messages.length) body += `<div class="section-label">A starting point for your questions</div>${suggestions()}`;
    body += `<form class="question-form" id="question-form"><label class="sr-only" for="question-input">Ask about this paper</label><textarea id="question-input" placeholder="What would you like to understand?" maxlength="2000" required rows="3"${job ? ' disabled' : ''}>${esc(state.drafts.get(id) || '')}</textarea><div class="question-form-footer"><span>Ctrl / ⌘ + Enter to send</span><button class="button button-primary button-small" type="submit"${job ? ' disabled' : ''}>Ask AI ${icon('arrow')}</button></div></form><p class="ai-fineprint">Answers use relevant passages from this paper. Verify claims using the page citations.</p>`;
  }
  $('#reader-content').innerHTML = `<div class="reader-body"><div class="paper-detail-top"><span class="source-tag">${esc(paper.source === 'upload' ? 'PDF upload' : paper.source)}</span><span>${esc(paper.year || 'Year unknown')}</span></div><h2 class="detail-title">${esc(paper.title)}</h2><p class="detail-authors">${esc(authorsLabel(paper))}</p>
    <div class="detail-actions">${url ? `<a class="button button-outline button-small" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open paper ${icon('link')}</a>` : ''}${id ? `<button class="button button-outline button-small" data-reader-action="edit">${icon('edit')}Details</button><button class="icon-button" data-reader-action="attach" title="Attach or replace PDF" aria-label="Attach or replace PDF">${icon('upload')}</button><button class="icon-button" data-reader-action="delete" title="Remove paper" aria-label="Remove paper">${icon('trash')}</button>` : ''}</div>
    ${id ? `<div class="paper-tabs" role="tablist" aria-label="Paper tools">${[['paper','Paper'],['summary','Summary'],['chat','Ask AI']].map(([tab, label]) => `<button role="tab" aria-selected="${state.tab === tab}" class="paper-tab${state.tab === tab ? ' active' : ''}" data-tab="${tab}">${label}</button>`).join('')}</div>` : ''}${body}</div>`;
}

async function refreshLibrary() {
  state.library = (await api('/api/papers')).papers;
  const lookup = new Map(state.library.filter((p) => p.external_id).map((p) => [p.external_id,p.id]));
  state.results.forEach((p) => { p.saved_id = lookup.get(p.external_id) || null; });
  renderWorkspace();
}
async function refreshConfig() {
  state.config = await api('/api/config');
  $('#status-dot').classList.toggle('connected', state.config.llm_configured);
  $('#connection-label').textContent = state.config.llm_configured ? 'AI configured' : 'Connect your AI';
  $('#connection-model').textContent = state.config.llm_configured ? state.config.model : 'Summaries & questions';
  $('#model-details').innerHTML = `<strong>${state.config.llm_configured ? 'Configured' : 'Not configured'}</strong><br>Model: ${esc(state.config.model)} · ${esc(state.config.provider)}<br><span class="muted">Connection is verified when an analysis succeeds.</span>`;
}
async function loadDetail(id) {
  const requestId = ++state.detailRequest;
  const data = await api(`/api/papers/${id}`);
  if (requestId !== state.detailRequest || savedId(state.current) !== id) return;
  state.current = data.paper; state.messages = data.messages.filter((message) => message.language === language()); state.detailLoading = false;
  renderReader(); renderCards();
  if (data.active_job && !state.jobs.has(data.active_job.id)) watchJob(data.active_job.id, id);
}
async function openPaper(paper) {
  state.detailRequest++;
  state.current = paper; state.tab = 'paper'; state.messages = [];
  const id = savedId(paper); state.detailLoading = Boolean(id);
  renderCards(); renderReader();
  if (window.innerWidth <= 960) $('.reader-panel').scrollIntoView({behavior:'smooth', block:'start'});
  if (id) {
    try { await loadDetail(id); }
    catch (error) { state.detailLoading = false; renderReader(); throw error; }
  }
}
async function savePaper(paper, button) {
  if (button) button.disabled = true;
  try {
    const data = await api('/api/papers',{method:'POST',body:JSON.stringify(paper)});
    await refreshLibrary();
    state.current = data.paper; state.tab = 'paper';
    await loadDetail(data.paper.id);
    toast(data.duplicate ? 'Already in your library.' : 'Saved to your library.');
  } finally { if (button?.isConnected) button.disabled = false; }
}

async function runSearch(searchOptions = null) {
  if (state.searchBusy) return;
  state.view = 'discover';
  state.search = searchOptions || {q:$('#search-input').value.trim(), source:$('#search-source').value, sort:$('#search-sort').value,start:0};
  state.searchBusy = true; state.warning = null;
  $('#search-submit').disabled = true; renderWorkspace();
  try {
    const params = new URLSearchParams(state.search);
    const data = await api(`/api/search?${params}`);
    state.results = data.papers; state.total = data.total; state.source = data.source; state.warning = data.warning;
    // Keep pagination on the same provider if automatic fallback was used.
    state.search.source = data.source;
  } catch (error) {
    state.results = []; state.total = 0; state.warning = error.message;
    throw error;
  } finally {
    state.searchBusy = false; $('#search-submit').disabled = false; renderWorkspace();
  }
}

function chooseUpload(target = null) {
  state.uploadTarget = target; $('#upload-file').value = ''; $('#upload-file').click();
}
async function uploadFile(file, target = null) {
  if (!file || !file.name.toLowerCase().endsWith('.pdf')) throw new Error('Choose a PDF file.');
  if (file.size > 20 * 1024 * 1024) throw new Error('The PDF limit is 20 MB.');
  const data = new FormData(); data.append('file',file); if (target) data.append('paper_id',target);
  $('#sidebar-upload').disabled = true; toast('Uploading and extracting your PDF…');
  try {
    const response = await api('/api/upload',{method:'POST',body:data});
    state.view = 'library'; $('#library-filter').value = '';
    await refreshLibrary(); await openPaper(response.paper);
    toast(response.message);
    if (!target && !response.duplicate) openMetadata();
  } finally { $('#sidebar-upload').disabled = false; }
}

function openMetadata() {
  const p = state.current; if (!p?.id) return;
  state.editingId = p.id;
  $('#edit-title').value = p.title; $('#edit-authors').value = p.authors.join('\n');
  $('#edit-year').value = p.year || ''; $('#edit-abstract').value = p.abstract || '';
  $('#metadata-dialog').showModal();
}
function showSource(pageNumber = null) {
  const pages = state.current?.pages || [];
  const chosen = pageNumber ? pages.filter((p) => p.page === Number(pageNumber)) : pages;
  if (!chosen.length) { toast('This page is not available in the extracted text.',true); return; }
  $('#source-title').textContent = pageNumber ? `Source · Page ${pageNumber}` : 'Extracted paper text';
  $('#source-content').innerHTML = chosen.map((p) => `<section class="source-page"><h3>Page ${p.page}</h3><pre>${esc(p.text || 'No text detected on this page.')}</pre></section>`).join('');
  $('#source-dialog').showModal();
}

async function watchJob(jobId, paperId) {
  if (state.jobs.has(jobId)) return;
  state.jobs.set(jobId,{paper_id:paperId,progress:'Preparing the paper…'}); renderReader();
  try {
    while (true) {
      const job = await api(`/api/jobs/${jobId}`);
      state.jobs.set(jobId,job);
      if (job.status === 'completed' || job.status === 'failed') {
        state.jobs.delete(jobId);
        await refreshLibrary();
        if (savedId(state.current) === paperId) await loadDetail(paperId);
        if (job.status === 'failed') toast(job.error,true);
        else toast(job.kind === 'summary' ? 'Your paper summary is ready.' : 'Your answer is ready.');
        return;
      }
      if (savedId(state.current) === paperId) {
        const box = $('.job-progress span:last-child');
        if (box) box.textContent = job.progress;
        else renderReader();
      }
      await new Promise((resolve) => setTimeout(resolve,1500));
    }
  } catch (error) {
    state.jobs.delete(jobId); renderReader();
    toast(`${error.message} Reopen the paper to reconnect to its analysis.`,true);
  }
}
async function startAnalysis(kind, question = '') {
  if (!state.config?.llm_configured) { $('#settings-dialog').showModal(); return; }
  const id = savedId(state.current); if (!id) return;
  const endpoint = kind === 'summary' ? 'summary' : 'questions';
  const data = await api(`/api/papers/${id}/${endpoint}`,{method:'POST',body:JSON.stringify({question,language:language(),refresh:kind === 'summary' && Boolean(state.current.summary)})});
  if (data.cached) { await loadDetail(id); return; }
  if (kind === 'question') state.drafts.delete(id);
  await watchJob(data.job_id,id);
}

document.addEventListener('click',safe(async (event) => {
  const close = event.target.closest('[data-close-dialog]');
  if (close) { document.getElementById(close.dataset.closeDialog).close(); return; }
  const view = event.target.closest('[data-view]');
  if (view) { state.view = view.dataset.view; renderWorkspace(); if (state.view === 'library') await refreshLibrary(); return; }
  const topic = event.target.closest('[data-topic]');
  if (topic) { $('#search-input').value = topic.dataset.topic; await runSearch(); return; }
  const action = event.target.closest('[data-action]');
  if (action?.dataset.action === 'upload') { chooseUpload(); return; }
  if (action?.dataset.action === 'settings') { $('#settings-dialog').showModal(); return; }
  const card = event.target.closest('.paper-card');
  if (card && !event.target.closest('a')) {
    const paper = currentList()[Number(card.dataset.index)];
    if (action?.dataset.action === 'save') await savePaper(paper,action);
    else await openPaper(paper);
    return;
  }
  const tab = event.target.closest('[data-tab]');
  if (tab) { state.tab = tab.dataset.tab; renderReader(); return; }
  const page = event.target.closest('[data-page]');
  if (page) { showSource(page.dataset.page); return; }
  const suggestion = event.target.closest('[data-question]');
  if (suggestion) {
    state.drafts.set(savedId(state.current),suggestion.dataset.question);
    $('#question-input').value = suggestion.dataset.question; $('#question-input').focus(); return;
  }
  const readerAction = event.target.closest('[data-reader-action]');
  if (!readerAction) return;
  switch (readerAction.dataset.readerAction) {
    case 'save': await savePaper(state.current,readerAction); break;
    case 'edit': openMetadata(); break;
    case 'attach': chooseUpload(savedId(state.current)); break;
    case 'text': showSource(); break;
    case 'summarize': readerAction.disabled = true; try { await startAnalysis('summary'); } finally { if (readerAction.isConnected) readerAction.disabled = false; } break;
    case 'delete': state.deletingId = state.current.id; $('#delete-description').textContent = state.current.title; $('#delete-dialog').showModal(); break;
  }
}));

$('#search-form').addEventListener('submit',safe(async (event) => { event.preventDefault(); await runSearch(); }));
$('#library-filter-form').addEventListener('submit',(event) => event.preventDefault());
$('#library-filter').addEventListener('input',renderWorkspace);
$('#previous-page').addEventListener('click',safe(() => runSearch({...state.search,start:Math.max(0,state.search.start - 12)})));
$('#next-page').addEventListener('click',safe(() => runSearch({...state.search,start:state.search.start + 12})));
$('#sidebar-upload').addEventListener('click',() => chooseUpload());
$('#upload-file').addEventListener('change',safe(() => uploadFile($('#upload-file').files[0],state.uploadTarget)));
$('#connection-button').addEventListener('click',() => $('#settings-dialog').showModal());
$('#help-button').addEventListener('click',() => $('#help-dialog').showModal());
$('#refresh-connection').addEventListener('click',safe(async () => { await refreshConfig(); renderReader(); toast(state.config.llm_configured ? 'Model configuration detected.' : 'No API key found. Configure .env and restart the server.'); }));

$('#metadata-form').addEventListener('submit',safe(async (event) => {
  event.preventDefault(); const id = state.editingId; $('#metadata-submit').disabled = true;
  try {
    await api(`/api/papers/${id}`,{method:'PATCH',body:JSON.stringify({title:$('#edit-title').value,
      authors:$('#edit-authors').value.split('\n').map((s) => s.trim()).filter(Boolean),
      year:$('#edit-year').value ? Number($('#edit-year').value) : null,abstract:$('#edit-abstract').value})});
    $('#metadata-dialog').close(); await refreshLibrary();
    if (savedId(state.current) === id) await loadDetail(id);
    toast('Paper details updated.');
  } finally { $('#metadata-submit').disabled = false; }
}));
$('#confirm-delete').addEventListener('click',safe(async () => {
  const id = state.deletingId; $('#confirm-delete').disabled = true;
  try {
    await api(`/api/papers/${id}`,{method:'DELETE'}); $('#delete-dialog').close();
    if (savedId(state.current) === id) { state.current = null; state.detailRequest++; renderReader(); }
    state.drafts.delete(id); await refreshLibrary(); toast('Paper removed from your library.');
  } finally { $('#confirm-delete').disabled = false; }
}));

$('#reader-content').addEventListener('input',(event) => {
  if (event.target.id === 'question-input') state.drafts.set(savedId(state.current),event.target.value);
});
$('#reader-content').addEventListener('submit',safe(async (event) => {
  if (event.target.id !== 'question-form') return;
  event.preventDefault(); const question = $('#question-input').value.trim();
  if (!question) return;
  const button = event.target.querySelector('button[type="submit"]'); button.disabled = true;
  try { await startAnalysis('question',question); }
  finally { if (button.isConnected) button.disabled = false; }
}));
document.addEventListener('keydown',safe(async (event) => {
  if (event.target.classList.contains('paper-card') && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault(); await openPaper(currentList()[Number(event.target.dataset.index)]);
  }
  if (event.target.id === 'question-input' && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault(); $('#question-form').requestSubmit();
  }
}));
for (const dialog of document.querySelectorAll('dialog')) {
  dialog.addEventListener('click',(event) => {
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    }
  });
}
document.addEventListener('dragover',(event) => {
  if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); $('#dropzone').classList.add('dragging'); }
});
document.addEventListener('dragleave',(event) => { if (!event.relatedTarget) $('#dropzone').classList.remove('dragging'); });
document.addEventListener('drop',safe(async (event) => {
  event.preventDefault(); $('#dropzone').classList.remove('dragging');
  if (event.dataTransfer.files.length) await uploadFile(event.dataTransfer.files[0]);
}));

renderWorkspace(); renderReader();
Promise.all([refreshLibrary(),refreshConfig()]).catch((error) => toast(error.message,true));
