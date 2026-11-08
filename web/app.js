'use strict';
const $ = selector => document.querySelector(selector);
let state = null, selectedJob = null, chatSession = null, chatReport = null;
let completedAnalysis = null, chatBusy = false, submitting = false, currentConfig = null;
let devRevision = null;
let pendingPlan = null, shownPlan = null;
function text(selector, value) { $(selector).textContent = value; }
function error(message = '') { text('#error-banner', message); $('#error-banner').hidden = !message; }
async function api(path, options = {}) {
  const headers = {...options.headers};
  if (options.body !== undefined) Object.assign(headers, {'Content-Type': 'application/json', 'X-Vorpal-Token': state?.token || ''});
  const response = await fetch(path, {cache: 'no-store', ...options, headers});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
function navigate() {
  const view = location.hash === '#settings' ? 'settings' : 'assistant';
  for (const name of ['assistant', 'settings']) {
    $(`#view-${name}`).hidden = name !== view;
    const link = $(`[data-view="${name}"]`);
    if (name === view) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
}
function busy() {
  const active = state?.jobs.find(job => job.status === 'running');
  document.querySelectorAll('.job-trigger').forEach(button => { button.disabled = !state || Boolean(active) || submitting || chatBusy; });
  $('#send-security-message').disabled = !state || Boolean(active) || chatBusy || submitting;
  $('#new-conversation').disabled = chatBusy;
  $('#discuss-analysis').disabled = chatBusy || !completedAnalysis;
  $('#cancel-job-button').disabled = !active;
  $('#run-reviewed-analysis').disabled = !state || Boolean(active) || submitting || chatBusy || !pendingPlan || !$('#command-reviewed').checked || !$('#analysis-authorized').checked;
  const nmap = ($('#analysis-tool').value || state?.default_tool) === 'nmap';
  text('#analyze-ip-button', nmap ? 'Generate Nmap command for review' : 'Analyze TCP ports with Ollama + RAG');
  $('#analysis-goal').disabled = !nmap;
}
function sources(items) { return items.map(s => `[${s.id}] ${s.source}:${s.line}`).join('\n'); }
async function updateJob() {
  if (!selectedJob) return;
  const job = await api(`/api/jobs/${selectedJob}`);
  text('#task-status', `${job.action} · ${job.status}`);
  text('#task-output', job.output || 'Starting…');
  if (job.action === 'analyze_ip') {
    completedAnalysis = null;
    text('#ip-analysis-result', job.output || 'Collecting evidence and waiting for Ollama…');
    if (job.status === 'completed') {
      try {
        const report = JSON.parse(job.output);
        if (report.status === 'command_review_required') {
          if (shownPlan !== report.plan_id) {
            shownPlan = report.plan_id; pendingPlan = report;
            $('#analysis-tool').value = 'nmap';
            $('#analysis-target').value = report.target;
            $('#analysis-ports').value = report.selected_ports.join(',');
            $('#analysis-goal').value = report.goal;
            $('#analysis-authorized').checked = false;
            $('#command-reviewed').checked = false;
            $('#command-review').hidden = false;
            text('#proposed-command', report.command);
            text('#proposed-reason', report.reason);
            text('#proposed-sources', report.sources.map(s => `${s.source}:${s.line}`).join('\n'));
            text('#command-review-status', 'No scan has run. Review the command and confirm authorization to execute it.');
          }
          text('#ip-analysis-result', 'Command generated. Awaiting review before evidence collection.');
          return;
        }
        completedAnalysis = report.report_id;
        const ports = (report.port_analyses || []).map(p => {
          const service = p.evidence?.service || p.evidence?.service_hint || '';
          const version = [p.evidence?.product, p.evidence?.version].filter(Boolean).join(' ');
          return `TCP ${p.port}${service ? ` · ${service}` : ''} · ${p.status}${version ? `\nReported product/version (unverified): ${version}` : ''}\n${p.answer || p.error || ''}\n${sources(p.sources || [])}`;
        }).join('\n\n────────────────\n\n');
        text('#ip-analysis-result', `${report.answer}\n\n${ports || sources(report.sources || [])}\n\nReport ID: ${report.report_id}`);
      } catch { /* Raw job output remains visible. */ }
    }
  }
}
async function loadConfiguration(name) {
  try {
    const result = await api(`/api/config?name=${encodeURIComponent(name)}`);
    currentConfig = name;
    $('#config-content').value = JSON.stringify(result.value, null, 2);
    text('#config-status', '');
  } catch (e) { error(e.message); }
}
async function refresh() {
  state = await api('/api/state');
  if (state.dev_revision) {
    const revision = JSON.stringify(state.dev_revision);
    if (devRevision && revision !== devRevision && !chatBusy && !submitting && !state.jobs.some(job => job.status === 'running')) {
      location.reload(); return;
    }
    if (!devRevision) devRevision = revision;
  }
  text('#connection-label', 'Connected locally');
  text('#inference-endpoint', `Ollama: http://${state.inference.host}:${state.inference.port} · Model: ${state.inference.model}`);
  const list = $('#recent-jobs'); list.replaceChildren();
  for (const job of state.jobs) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary';
    button.textContent = `${job.action} · ${job.status}`;
    button.addEventListener('click', async () => { selectedJob = job.id; try { await updateJob(); busy(); } catch (e) { error(e.message); } });
    list.append(button);
  }
  if (!currentConfig && state.configurations.length) {
    const select = $('#config-select'); select.replaceChildren();
    for (const name of state.configurations) { const option = document.createElement('option'); option.value = name; option.textContent = `${name}.json`; select.append(option); }
    select.value = 'scan_intelligence';
    await loadConfiguration(select.value);
  }
  await updateJob(); busy();
}
async function startJob(action, params) {
  if (submitting) return;
  submitting = true; error(); busy();
  try {
    const job = await api('/api/jobs', {method: 'POST', body: JSON.stringify({action, params})});
    selectedJob = job.id;
    await refresh();
  } catch (e) { error(e.message); }
  finally { submitting = false; busy(); }
}
$('#ip-analysis-form').addEventListener('submit', event => {
  event.preventDefault(); completedAnalysis = null; pendingPlan = null; $('#command-review').hidden = true;
  const params = {target: $('#analysis-target').value.trim(), ports: $('#analysis-ports').value.trim(), authorized: $('#analysis-authorized').checked};
  if ($('#analysis-tool').value) params.tool = $('#analysis-tool').value;
  if (!$('#analysis-goal').disabled && $('#analysis-goal').value.trim()) params.goal = $('#analysis-goal').value.trim();
  startJob('analyze_ip', params);
});
for (const id of ['analysis-tool', 'analysis-target', 'analysis-ports', 'analysis-goal']) {
  $(`#${id}`).addEventListener('input', () => {
    if (pendingPlan) text('#command-review-status', 'Inputs changed. Generate and review a new command.');
    pendingPlan = null; $('#command-reviewed').checked = false; busy();
  });
}
$('#command-reviewed').addEventListener('change', busy);
$('#analysis-authorized').addEventListener('change', busy);
$('#run-reviewed-analysis').addEventListener('click', () => {
  if (!pendingPlan || !$('#command-reviewed').checked || !$('#analysis-authorized').checked) return;
  const params = {target: $('#analysis-target').value.trim(), ports: $('#analysis-ports').value.trim(),
    tool: 'nmap', authorized: true, plan_id: pendingPlan.plan_id, digest: pendingPlan.digest};
  pendingPlan = null; $('#command-review').hidden = true;
  startJob('analyze_ip', params);
});
$('#assistant-update-knowledge').addEventListener('click', () => startJob('knowledge_update', {import_sources: $('#assistant-import-sources').checked}));
function newConversation(report = null) {
  if (chatBusy) return;
  chatSession = null; chatReport = report;
  $('#security-conversation').replaceChildren(); text('#chat-status', '');
  text('#chat-context', report ? `Discussing report ${report}. Chat makes no target connections.` : 'General security advice. Chat makes no target connections.');
}
$('#new-conversation').addEventListener('click', () => newConversation());
$('#discuss-analysis').addEventListener('click', () => { newConversation(completedAnalysis); $('#security-message').focus(); });
$('#security-chat-form').addEventListener('submit', async event => {
  event.preventDefault(); if (chatBusy) return;
  const message = $('#security-message').value.trim(); if (!message) return;
  chatBusy = true; busy(); text('#chat-status', 'Retrieving documentation and waiting for Ollama…');
  try {
    const result = await api('/api/chat', {method: 'POST', body: JSON.stringify({message, session_id: chatSession, report_id: chatReport})});
    chatSession = result.session_id;
    const turn = document.createElement('article'); turn.className = 'assistant-turn';
    turn.textContent = `You: ${message}\n\nAssistant: ${result.answer}\n\n${sources(result.sources)}`;
    $('#security-conversation').append(turn); $('#security-message').value = '';
    text('#chat-status', `Conversation ${chatSession}. Saved locally.`);
  } catch (e) { text('#chat-status', e.message); }
  finally { chatBusy = false; busy(); }
});
$('#config-select').addEventListener('change', event => loadConfiguration(event.target.value));
$('#config-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!currentConfig) return;
  try {
    const result = await api('/api/config', {method: 'POST', body: JSON.stringify({name: currentConfig, value: JSON.parse($('#config-content').value)})});
    text('#config-status', result.restart_required ? 'Saved. Restart the dashboard to apply web settings.' : 'Configuration saved and validated.');
  } catch (e) { error(e.message); }
});
$('#cancel-job-button').addEventListener('click', async () => {
  const active = state?.jobs.find(job => job.status === 'running'); if (!active) return;
  try { await api(`/api/jobs/${active.id}/cancel`, {method: 'POST', body: '{}'}); await refresh(); } catch (e) { error(e.message); }
});
window.addEventListener('hashchange', navigate);
async function poll() {
  try { await refresh(); } catch (e) { text('#connection-label', 'Server unavailable'); error(e.message); }
  setTimeout(poll, state?.poll_ms || 1500);
}
navigate(); busy(); poll();
