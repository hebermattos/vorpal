'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const views = {overview: 'Overview', generate: 'Ask / generate', train: 'Training', dataset: 'Dataset', evaluate: 'Evaluation', tools: 'Tools', settings: 'Settings'};
const actions = {generate: 'Model response', train: 'Training', evaluate: 'Model evaluation', benchmark: 'Code benchmark', prepare: 'Dataset preparation', import: 'Source import', tool_plan: 'Command planning', tool_run: 'Tool execution'};
const statuses = {running: 'Running', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Server stopped'};
const actionIcons = {generate: 'code', train: 'train', evaluate: 'check', benchmark: 'check', prepare: 'data', import: 'folder'};
let state = null;
let selectedJob = null;
let currentJob = null;
let currentConfig = null;
let configRequest = 0;
let refreshing = false;
let submitting = false;
let pendingJob = null;
let toastTimer;
let pdfKey = '';
let chartKey = '';
let configListKey = '';
let toolPlanId = null;
let toolPlanStale = false;
const drafts = new Map();
const number = new Intl.NumberFormat('en-US');
const decimals = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
const timestamp = new Intl.DateTimeFormat('en-US', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}
function text(id, value) { $(id).textContent = value; }
function bytes(value) {
  if (typeof value !== 'number') return '—';
  if (value < 1000) return `${number.format(value)} B`;
  if (value < 1e6) return `${decimals.format(value / 1000)} KB`;
  return `${decimals.format(value / 1e6)} MB`;
}
function date(value) { return value ? timestamp.format(new Date(value)) : '—'; }
function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.hidden = false;
  toastTimer = setTimeout(() => { node.hidden = true; }, 5000);
}
async function api(path, options = {}) {
  const headers = {...options.headers};
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers['X-Vorpal-Token'] = state?.token || '';
  }
  const response = await fetch(path, {cache: 'no-store', ...options, headers});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Unable to complete the operation.');
  return result;
}
function tag(label, kind = 'neutral') { return element('span', `mini-pill ${kind}`, label); }
function currentView() {
  const name = location.hash.slice(1);
  return Object.hasOwn(views, name) ? name : 'overview';
}
function navigate() {
  const view = currentView();
  for (const [name, label] of Object.entries(views)) {
    $(`#view-${name}`).hidden = name !== view;
    const link = $(`[data-view="${name}"]`);
    if (name === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
    if (name === view) text('#current-view-label', label);
  }
  document.title = `${views[view]} · Vorpal`;
  if (view === 'settings' && state && !currentConfig) loadConfiguration('train');
  if (view === 'dataset') renderDataset();
}
function setDefault(id, value, maximum) {
  const input = $(id);
  if (!input.dataset.dirty) input.value = value;
  if (maximum !== undefined) input.max = maximum;
}
function selectOptions(select, entries) {
  const signature = JSON.stringify(entries);
  if (select.dataset.options === signature) return;
  const previous = select.value;
  select.replaceChildren(...entries.map(({id, label, disabled}) => {
    const option = element('option', null, label);
    option.value = id;
    option.disabled = Boolean(disabled);
    return option;
  }));
  if (entries.some(entry => entry.id === previous && !entry.disabled)) select.value = previous;
  else {
    const available = entries.find(entry => !entry.disabled);
    if (available) select.value = available.id;
  }
  select.dataset.options = signature;
}
function initializeForms() {
  const defaults = state.defaults;
  setDefault('#generate-prompt', defaults.generate.prompt);
  for (const [input, value, maximum] of [
    ['#generate-max-new', defaults.generate.max_new, state.limits.max_new],
    ['#generate-temperature', defaults.generate.temperature, 2],
    ['#generate-threads', defaults.generate.threads, state.limits.threads],
    ['#train-steps', defaults.train.steps, state.limits.steps],
    ['#train-batch', defaults.train.batch, state.limits.batch],
    ['#train-threads', defaults.train.threads, state.limits.threads],
    ['#evaluate-samples', defaults.evaluate.samples, state.limits.samples],
    ['#evaluate-threads', defaults.evaluate.threads, state.limits.threads],
    ['#benchmark-threads', defaults.benchmark.threads, state.limits.threads],
    ['#tool-threads', defaults.security_tools.threads, state.limits.threads],
  ]) setDefault(input, value, maximum);
  $('#generate-prompt').maxLength = state.limits.prompt_chars;
  for (const select of $$('.profile-select')) selectOptions(select, state.profiles.map(profile => ({id: profile.id, label: profile.label})));
  for (const select of $$('.checkpoint-select')) selectOptions(select, state.checkpoints.map(checkpoint => ({id: checkpoint.id, label: checkpoint.id === 'latest' ? 'Latest · latest.keras' : 'Best · best.keras', disabled: !checkpoint.exists})));
  const latest = state.checkpoints.find(item => item.id === 'latest');
  $('input[name="mode"][value="replay"]').disabled = !latest?.exists;
}
function renderOverview() {
  const manifest = state.profiles[0]?.manifest;
  const trainDocuments = manifest?.train_documents?.length || 0;
  const validDocuments = manifest?.valid_documents?.length || 0;
  text('#stat-steps', state.metadata ? number.format(state.metadata.step) : '—');
  text('#stat-documents', manifest ? number.format(trainDocuments + validDocuments) : '—');
  text('#stat-split', manifest ? `${trainDocuments} training · ${validDocuments} validation` : 'Prepare your first dataset');
  text('#stat-bytes', manifest ? bytes((manifest.train_bytes || 0) + (manifest.valid_bytes || 0)) : '—');
  text('#stat-syntax', state.benchmark ? `${state.benchmark.syntax_passes}/${state.benchmark.total}` : '—');
  const architecture = state.metadata?.config;
  text('#model-context', architecture ? `${architecture.context} bytes` : 'No checkpoint');
  text('#model-architecture', architecture ? `${architecture.layers} blocks · ${architecture.d_model} dim` : '—');
  text('#model-checkpoint', state.metadata ? `step ${number.format(state.metadata.step)}` : 'Not trained');
  text('#train-step-label', state.metadata ? `Checkpoint · step ${number.format(state.metadata.step)}` : 'First training run');
  renderChart();
  renderRecentJobs();
  const last = state.metrics.at(-1);
  const metrics = [
    ['Total steps', state.metadata ? number.format(state.metadata.step) : '—'],
    ['Latest training loss', last ? decimals.format(last.train_loss) : '—'],
    ['Latest validation loss', last ? decimals.format(last.validation_loss) : '—'],
    ['Best validation loss', typeof state.best?.validation_loss === 'number' ? decimals.format(state.best.validation_loss) : '—'],
  ];
  $('#training-metrics').replaceChildren(...metrics.map(([label, value]) => {
    const row = element('div', 'metric-row'); row.append(element('span', null, label), element('strong', null, value)); return row;
  }));
}
function renderChart() {
  const data = state.metrics;
  const last = data.at(-1);
  const signature = item => JSON.stringify([item.validation_sha256, item.config, item.samples, item.seed]);
  let points = last ? data.filter(item => signature(item) === signature(last)) : [];
  for (let i = points.length - 1; i > 0; i--) {
    if (points[i].step <= points[i - 1].step) { points = points.slice(i); break; }
  }
  const key = JSON.stringify(points);
  if (chartKey === key) return;
  chartKey = key;
  const container = $('#learning-chart');
  if (!points.length) {
    const empty = element('div', 'chart-empty');
    const holder = element('div', 'chart-empty-icon'); holder.append(icon('train'));
    empty.append(holder, element('strong', null, 'Your learning curve starts with the next cycle.'), element('p', null, 'Train with validation to track progress.'));
    container.replaceChildren(empty);
    text('#chart-caption', 'Metrics appear after each validation cycle.');
    return;
  }
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 600 224');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Training and validation loss by training step');
  const add = (name, attributes, content) => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (content !== undefined) node.textContent = content;
    svg.append(node); return node;
  };
  const values = points.flatMap(item => [item.train_loss, item.validation_loss]);
  const low = Math.max(0, Math.min(...values) * .9);
  const high = Math.max(low + .1, Math.max(...values) * 1.1);
  const firstStep = points[0].step, lastStep = points.at(-1).step;
  const x = step => lastStep === firstStep ? 320 : 45 + (step - firstStep) / (lastStep - firstStep) * 530;
  const y = value => 185 - (value - low) / (high - low) * 158;
  for (let i = 0; i <= 4; i++) {
    const value = low + (high - low) * i / 4;
    add('line', {x1: 42, x2: 580, y1: y(value), y2: y(value), stroke: '#edf1f3'});
    add('text', {x: 28, y: y(value) + 3, 'text-anchor': 'end', class: 'chart-axis'}, decimals.format(value));
  }
  for (const [field, color] of [['train_loss', '#3d8e72'], ['validation_loss', '#baa06b']]) {
    const coordinates = points.map(item => `${x(item.step)},${y(item[field])}`).join(' ');
    add('polyline', {points: coordinates, fill: 'none', stroke: color, 'stroke-width': 2});
    for (const item of points) {
      const circle = add('circle', {cx: x(item.step), cy: y(item[field]), r: points.length > 40 ? 1.5 : 3, fill: color, stroke: 'white', 'stroke-width': 1});
      const title = document.createElementNS(ns, 'title'); title.textContent = `Step ${item.step}: ${decimals.format(item[field])}`; circle.append(title);
    }
  }
  add('text', {x: x(firstStep), y: 211, 'text-anchor': 'middle', class: 'chart-axis'}, number.format(firstStep));
  if (lastStep !== firstStep) add('text', {x: x(lastStep), y: 211, 'text-anchor': 'middle', class: 'chart-axis'}, number.format(lastStep));
  container.replaceChildren(svg);
  text('#chart-caption', `Latest validation · ${points.length} ${points.length === 1 ? 'measurement' : 'measurements'}`);
}
function jobColor(status) { return status === 'completed' ? 'green' : status === 'running' ? 'amber' : 'red'; }
function renderRecentJobs() {
  text('#activity-count', `${state.jobs.length} ${state.jobs.length === 1 ? 'job' : 'jobs'}`);
  const container = $('#recent-jobs');
  if (!state.jobs.length) {
    const empty = element('div', 'empty-activity');
    empty.append(icon('clock'), element('strong', null, 'Room for new experiments.'), element('p', null, 'Your runs and results will appear here. Start by generating a continuation.'));
    container.replaceChildren(empty); return;
  }
  container.replaceChildren(...state.jobs.slice(0, 4).map(job => {
    const button = element('button', 'job-row'); button.type = 'button';
    const type = element('span', 'job-type-icon'); type.append(icon(actionIcons[job.action] || 'clock'));
    const info = element('div', 'job-info'); info.append(element('strong', null, actions[job.action] || job.action), element('small', null, date(job.created_at)));
    button.append(type, info, tag(statuses[job.status] || job.status, jobColor(job.status)));
    button.addEventListener('click', () => {
      selectedJob = job.id;
      location.hash = job.action.startsWith('tool_') ? 'tools' : job.action === 'generate' ? 'generate' : job.action === 'train' ? 'train' : ['benchmark', 'evaluate'].includes(job.action) ? 'evaluate' : 'dataset';
      updateJob(true);
    });
    return button;
  }));
}
function profileManifest() { return state.profiles.find(profile => profile.id === $('#dataset-profile').value)?.manifest; }
function renderDataset() {
  if (!state) return;
  text('#pdf-count-label', `${number.format(state.pdf_count)} PDFs in the corpus`);
  const manifest = profileManifest();
  const items = [
    ['Training documents', manifest ? number.format(manifest.train_documents.length) : '—', 'Unique documents'],
    ['Validation documents', manifest ? number.format(manifest.valid_documents.length) : '—', 'Persistent split'],
    ['Dataset size', manifest ? bytes((manifest.train_bytes || 0) + (manifest.valid_bytes || 0)) : '—', 'Binary byte format'],
    ['Excluded duplicates', manifest ? number.format(manifest.skipped_duplicates?.length || 0) : '—', 'In the latest preparation'],
  ];
  $('#dataset-stats').replaceChildren(...items.map(([label, value, footnote]) => {
    const card = element('article', 'stat-card'); card.append(element('div', 'stat-top', label), element('strong', null, value), element('div', 'stat-bottom', footnote)); return card;
  }));
  const query = $('#pdf-search').value.trim().toLowerCase();
  const key = JSON.stringify([state.pdfs, query]);
  if (pdfKey === key) return;
  pdfKey = key;
  const files = state.pdfs.filter(file => file.name.toLowerCase().includes(query));
  if (!files.length) {
    const row = element('tr'); const cell = element('td', 'empty-row', query ? 'No documents found.' : 'No PDFs in the corpus. Add files to the selected subject\'s pdfs folder.'); cell.colSpan = 4; row.append(cell); $('#pdf-table').replaceChildren(row); return;
  }
  $('#pdf-table').replaceChildren(...files.map(file => {
    const row = element('tr');
    const name = element('td'); const holder = element('div', 'pdf-name'); const info = element('div');
    info.append(element('strong', null, file.name), element('small', null, file.path));
    holder.append(element('span', 'pdf-file-icon', 'PDF'), info); name.append(holder);
    const type = element('td'); type.append(tag('DOCUMENTATION'));
    const size = element('td', null, bytes(file.bytes));
    const linkCell = element('td'); const link = element('a', 'pdf-link', 'Open');
    link.href = `/files?path=${encodeURIComponent(file.path)}`; link.target = '_blank'; link.rel = 'noopener'; link.setAttribute('aria-label', `Open ${file.name}`); link.append(icon('external')); linkCell.append(link);
    row.append(name, type, size, linkCell); return row;
  }));
}
function score(value, label) {
  const node = element('div', 'benchmark-score'); node.append(element('strong', null, value), element('span', null, label)); return node;
}
function renderEvaluation() {
  const benchmark = state.benchmark;
  $('#benchmark-summary').replaceChildren(score(benchmark ? `${benchmark.syntax_passes}/${benchmark.total}` : '—', 'Valid syntax'), score(benchmark ? `${benchmark.behavior_passes}/${benchmark.total}` : '—', 'Correct behavior'));
  const container = $('#benchmark-results');
  const signature = JSON.stringify(benchmark);
  if (container.dataset.result !== signature) {
    container.dataset.result = signature;
    if (!benchmark?.results?.length) {
      const empty = element('div', 'empty-activity'); empty.append(icon('check'), element('strong', null, 'No benchmark yet.'), element('p', null, 'Run the exercises to establish a baseline.')); container.replaceChildren(empty);
    } else container.replaceChildren(...benchmark.results.map(result => {
      const details = element('details', 'benchmark-item');
      const summary = element('summary');
      summary.append(icon('code'), element('code', null, `${result.name}()`), tag(result.syntax_valid ? 'VALID SYNTAX' : 'INVALID SYNTAX', result.syntax_valid ? 'green' : 'amber'), element('span', null, result.behavior_status === 'passed' ? `${result.passed}/${result.total} tests` : result.behavior_status === 'failed' ? `${result.passed}/${result.total} tests` : result.behavior_status === 'unsupported' ? 'Outside the interpreter\'s scope' : 'Behavior not checked'));
      details.append(summary, element('pre', null, result.source)); return details;
    }));
  }
  const evaluation = state.evaluation;
  if (evaluation) {
    const items = [];
    for (const [label, value] of [['VALIDATION LOSS', evaluation.loss], ['PERPLEXITY', evaluation.perplexity]]) {
      const node = element('div', 'evaluation-value'); node.append(element('span', null, label), element('strong', null, decimals.format(value))); items.push(node);
    }
    $('#evaluation-result').replaceChildren(...items);
    $('#evaluation-caption').hidden = false;
    text('#evaluation-caption', `Latest run · ${evaluation.samples ? `${evaluation.samples} samples · ` : ''}${date(evaluation.finished_at)} · ${evaluation.dataset}`);
    $('#evaluation-result').title = `Latest evaluation completed at ${date(evaluation.finished_at)} · ${evaluation.dataset}`;
  }
}
function renderConfigurationList() {
  const key = JSON.stringify([state.configurations, currentConfig]);
  if (configListKey === key) return;
  configListKey = key;
  $('#config-list').replaceChildren(...state.configurations.map(name => {
    const button = element('button', `config-file${currentConfig === name ? ' active' : ''}`); button.type = 'button';
    button.append(icon('file'), document.createTextNode(`${name}.json`));
    if (currentConfig === name) button.setAttribute('aria-current', 'true');
    button.addEventListener('click', () => loadConfiguration(name)); return button;
  }));
}

function renderTools() {
  selectOptions($('#tool-select'), state.tools.map(tool => ({id: tool.id, label: tool.id === 'nmap' ? 'Nmap' : 'Nuclei', disabled: !tool.installed})));
  $('#tool-availability').replaceChildren(...state.tools.map(tool => {
    const card = element('article', 'tool-status-card');
    const info = element('div');
    info.append(element('strong', null, tool.id === 'nmap' ? 'Nmap' : 'Nuclei'), element('small', null, tool.executable || 'Not found on PATH'));
    card.append(icon('check'), info, tag(tool.installed ? 'INSTALLED' : 'UNAVAILABLE', tool.installed ? 'green' : 'amber'));
    return card;
  }));
  const plan = state.tool_plan;
  $('#tool-plan-empty').hidden = Boolean(plan);
  $('#tool-plan-details').hidden = !plan;
  if (!plan) return;
  if (plan.id !== toolPlanId) {
    toolPlanId = plan.id;
    toolPlanStale = false;
    $('#tool-authorized').checked = false;
  }
  text('#tool-plan-context', `${plan.tool} · ${plan.target} · ${date(plan.created_at)}`);
  text('#tool-plan-badge', plan.status === 'ready' ? 'READY FOR REVIEW' : 'PROPOSAL REJECTED');
  text('#tool-plan-goal', `Goal: ${plan.goal}`);
  text('#tool-command', plan.command || 'No executable command was produced.');
  text('#tool-reason', plan.reason || 'The model must produce valid arguments in the expected format.');
  $('#tool-plan-error').hidden = plan.status === 'ready';
  text('#tool-plan-error', plan.error || '');
  $('#tool-authorization-label').hidden = plan.status !== 'ready';
  text('#tool-authorized-target', plan.target);
  $('#tool-goal-note').hidden = !plan.goal_truncated;
  text('#tool-goal-note', plan.goal_truncated ? `Goal shortened to fit the model context: ${plan.goal_used}` : '');
  $('#tool-raw-details').hidden = !plan.model_response;
  text('#tool-raw-response', plan.model_response || '');
  $('#tool-plan-stale').hidden = !toolPlanStale;
}
async function loadConfiguration(name) {
  const request = ++configRequest;
  currentConfig = name;
  renderConfigurationList();
  text('#config-filename', `${name}.json`);
  $('#config-content').disabled = true;
  $('#save-config-button').disabled = true;
  try {
    const result = await api(`/api/config?name=${encodeURIComponent(name)}`);
    if (request !== configRequest) return;
    $('#config-content').value = drafts.get(name) ?? JSON.stringify(result.value, null, 2);
    $('#config-content').disabled = false;
    renderBusy();
  } catch (error) { toast(error.message, true); }
}
function renderBusy() {
  const active = state?.jobs.find(job => job.status === 'running');
  for (const button of $$('.job-trigger')) button.disabled = Boolean(active) || submitting || !state;
  for (const selector of ['#generate-form', '#evaluate-form', '#benchmark-form', '#tool-plan-form']) {
    const select = $(`${selector} .checkpoint-select`);
    if (select && ![...select.options].some(option => !option.disabled)) $(`${selector} .job-trigger`).disabled = true;
  }
  $('#save-config-button').disabled = Boolean(active) || submitting || !currentConfig || $('#config-content').disabled;
  $('#training-dot').hidden = active?.action !== 'train';
  $('#cancel-job-button').hidden = !currentJob || currentJob.status !== 'running';
  $('#cancel-job-button').disabled = Boolean(currentJob?.cancel_requested);
  const toolAvailable = state?.tools.find(tool => tool.id === $('#tool-select').value)?.installed;
  if (!toolAvailable) $('#tool-plan-form .job-trigger').disabled = true;
  const executionToolAvailable = state?.tools.find(tool => tool.id === state?.tool_plan?.tool)?.installed;
  $('#execute-tool-button').disabled = Boolean(active) || submitting || toolPlanStale || !executionToolAvailable || state?.tool_plan?.status !== 'ready' || !$('#tool-authorized').checked;
}
async function updateJob(scroll = false) {
  if (!selectedJob) return;
  const identifier = selectedJob;
  try {
    const job = await api(`/api/jobs/${identifier}`);
    if (selectedJob !== identifier) return;
    currentJob = job;
    const panel = $('#task-panel'); panel.hidden = false;
    text('#task-title', actions[job.action] || job.action);
    text('#task-status', statuses[job.status] || job.status);
    text('#task-command', job.command.slice(2).join(' '));
    const output = $('#task-output');
    const atBottom = output.scrollTop + output.clientHeight >= output.scrollHeight - 50;
    output.textContent = job.output || 'Starting the local process…';
    if (atBottom || scroll) output.scrollTop = output.scrollHeight;
    text('#task-time', `Started ${date(job.created_at)}${job.finished_at ? ` · finished ${date(job.finished_at)}` : ' · in progress'}`);
    $('#task-dot').classList.toggle('offline', ['failed', 'cancelled', 'interrupted'].includes(job.status));
    if (job.action === 'generate') {
      const hasOutput = Boolean(job.output);
      $('#generation-empty').hidden = hasOutput;
      $('#generation-output').hidden = !hasOutput;
      $('#generation-output').textContent = job.output;
      text('#generation-status', job.status === 'running' ? 'Generating response…' : statuses[job.status] || job.status);
      $('#copy-output').disabled = !hasOutput;
    }
    renderBusy();
    if (scroll && job.action !== 'generate') panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (error) { toast(error.message, true); }
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    state = await api('/api/state');
    if (pendingJob) {
      if (state.jobs.some(job => job.id === pendingJob.id)) pendingJob = null;
      else state.jobs.unshift(pendingJob);
    }
    $('#error-banner').hidden = true;
    $('#connection-dot').classList.remove('offline');
    text('#connection-label', 'Server connected');
    initializeForms();
    renderOverview(); renderDataset(); renderEvaluation(); renderConfigurationList(); renderTools();
    if (!selectedJob && state.jobs.length) selectedJob = state.jobs[0].id;
    if (selectedJob && !state.jobs.some(job => job.id === selectedJob)) selectedJob = state.jobs[0]?.id || null;
    if (selectedJob) await updateJob();
    renderBusy();
    if (currentView() === 'settings' && !currentConfig) await loadConfiguration('train');
  } catch (error) {
    $('#error-banner').hidden = false;
    text('#error-banner', `Unable to refresh the workspace. Check that the local server is running.\n${error.message}`);
    $('#connection-dot').classList.add('offline');
    text('#connection-label', 'Server unavailable');
    $$('.job-trigger').forEach(button => { button.disabled = true; });
  } finally { refreshing = false; }
}
async function startJob(action, params) {
  if (submitting) return;
  submitting = true;
  renderBusy();
  try {
    const job = await api('/api/jobs', {method: 'POST', body: JSON.stringify({action, params})});
    selectedJob = job.id;
    currentJob = job;
    pendingJob = job;
    state.jobs = [job, ...state.jobs.filter(previous => previous.id !== job.id)];
    toast(`${actions[action]} started.`);
    await refresh();
    await updateJob(action !== 'generate');
    renderBusy();
  } catch (error) { toast(error.message, true); await refresh(); }
  finally { submitting = false; renderBusy(); }
}
function formParameters(form) {
  const numbers = new Set(['threads', 'steps', 'batch', 'max_new', 'temperature', 'samples']);
  return Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, numbers.has(key) ? Number(value) : value]));
}

window.addEventListener('hashchange', navigate);
for (const action of ['generate', 'train', 'evaluate', 'benchmark']) {
  $(`#${action}-form`).addEventListener('submit', event => { event.preventDefault(); startJob(action, formParameters(event.currentTarget)); });
}
$('#tool-plan-form').addEventListener('submit', event => {
  event.preventDefault();
  startJob('tool_plan', formParameters(event.currentTarget));
});
$('#execute-tool-button').addEventListener('click', () => {
  if (!state?.tool_plan || !$('#tool-authorized').checked || toolPlanStale) return;
  startJob('tool_run', {plan_id: state.tool_plan.id, digest: state.tool_plan.digest, authorized: true});
});
$('#tool-authorized').addEventListener('change', renderBusy);
$('#tool-plan-form').addEventListener('input', () => {
  toolPlanStale = true;
  $('#tool-authorized').checked = false;
  $('#tool-plan-stale').hidden = false;
  renderBusy();
});
$('#tool-select').addEventListener('change', () => {
  const nuclei = $('#tool-select').value === 'nuclei';
  $('#tool-target').placeholder = nuclei ? 'http://192.168.1.10:8080' : '192.168.1.10';
  text('#tool-target-hint', nuclei ? 'Nuclei accepts a local IPv4 HTTP(S) origin without paths or parameters.' : 'Nmap accepts a single private or loopback IPv4 address.');
});
$$('input, textarea, select').forEach(input => {
  input.addEventListener('input', () => { input.dataset.dirty = 'true'; });
});
$$('[data-prompt]').forEach(button => button.addEventListener('click', () => {
  $('#generate-mode').value = button.dataset.mode || 'continuation';
  $('#generate-prompt').value = button.dataset.prompt;
  $('#generate-prompt').dataset.dirty = 'true';
  $('#generate-prompt').focus();
}));
$('#copy-output').disabled = true;
$('#copy-output').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('#generation-output').textContent); toast('Output copied.'); }
  catch { toast('Select the output text to copy it.', true); }
});
$('#refresh-button').addEventListener('click', async event => {
  event.currentTarget.classList.add('busy');
  await refresh();
  $('#refresh-button').classList.remove('busy');
});
$('#dataset-profile').addEventListener('change', renderDataset);
$('#pdf-search').addEventListener('input', renderDataset);
$('#prepare-button').addEventListener('click', () => startJob('prepare', {profile: $('#dataset-profile').value}));
$('#import-button').addEventListener('click', () => startJob('import', {profile: $('#dataset-profile').value}));
$('#cancel-job-button').addEventListener('click', async () => {
  if (!currentJob || currentJob.status !== 'running') return;
  try {
    await api(`/api/jobs/${currentJob.id}/cancel`, {method: 'POST', body: '{}'});
    toast('Cancellation requested. Waiting for the process to stop.');
    await refresh();
  } catch (error) { toast(error.message, true); }
});
$('#config-content').addEventListener('input', event => { if (currentConfig) drafts.set(currentConfig, event.target.value); });
$('#config-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!currentConfig) return;
  try {
    const value = JSON.parse($('#config-content').value);
    const result = await api('/api/config', {method: 'POST', body: JSON.stringify({name: currentConfig, value})});
    drafts.delete(currentConfig);
    toast(result.restart_required ? 'File saved. Restart the server to apply these settings.' : 'Configuration saved and validated.');
    await refresh();
  } catch (error) { toast(error.message, true); }
});
async function poll() {
  await refresh();
  setTimeout(poll, state?.poll_ms || 1500);
}
navigate();
renderBusy();
poll();
