import { drafts } from './storage.js';
import { decodeVoucherImage, decodeVoucherSvg } from './voucher-qr.js';

const app = document.querySelector('#app');
const root = document.querySelector('#toast');
const sessionKey = 'madadpass-session';
const roleNames = { citizen: 'Citizen', helper: 'Community helper', officer: 'Review officer', merchant: 'Merchant', admin: 'Administrator', auditor: 'Auditor' };
const roleIcons = { citizen: '⌂', helper: '♧', officer: '✓', merchant: '▣', admin: '⚙', auditor: '≡' };
const roleDescriptions = { citizen: 'Apply and track', helper: 'Assist a household', officer: 'Review applications', merchant: 'Redeem vouchers', admin: 'Manage the program', auditor: 'Inspect activity' };
const labels = {
  submitted: 'Submitted', in_review: 'In review', needs_information: 'Needs information', escalated: 'Escalated', approved: 'Approved',
  rejected: 'Closed · declined', eligible: 'Ready for review', human_review: 'Manual review', outside_policy: 'Outside policy',
  approve: 'Approved', reject: 'Declined', request_information: 'Information requested', escalate: 'Escalated',
  issued: 'Ready to use', redeemed: 'Redeemed', expired: 'Expired', unverified: 'Unverified message', officer_review: 'Suspicious signals found',
};
const scopeLabels = { case: 'Application status and details', documents: 'Application and documents', assist: 'Apply and edit on my behalf' };
const menus = {
  citizen: [['home', '⌂', 'Overview'], ['apply', '+', 'Apply for help'], ['cases', '▤', 'Track applications'], ['consent', '♧', 'Sharing access'], ['safety', '◇', 'Check a message']],
  helper: [['home', '⌂', 'Overview'], ['apply', '+', 'Start an application'], ['cases', '▤', 'Shared applications'], ['consent', '♧', 'Household access'], ['safety', '◇', 'Check a message']],
  officer: [['home', '▤', 'Review queue'], ['policy', '≡', 'Program rules'], ['safety', '◇', 'Check a message']],
  merchant: [['home', '▣', 'Redeem voucher'], ['history', '≡', 'Redemption history'], ['safety', '◇', 'Check a message']],
  admin: [['home', '⚙', 'Program rules'], ['users', '♧', 'People and roles'], ['keys', '◇', 'Mock API keys'], ['audit', '≡', 'Audit events']],
  auditor: [['home', '≡', 'Audit events'], ['policy', '▤', 'Policy versions']],
};

let session = null;
let health = {};
let page = 'home';
let entry = null;
let cases = [];
let consent = null;
let formContext = null;
let toastTimer;
let navigation = 0;
let cameraStop;
let voiceStop;
let recorderStop;
let loadedBlobs = [];
let syncTask;

function escape(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}

const $ = (selector, from = document) => from.querySelector(selector);
const e = escape;
const pk = value => value === null || value === undefined ? '—' : `PKR ${Number(value).toLocaleString('en-PK')}`;
const stamp = value => value ? new Intl.DateTimeFormat('en-PK', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—';
const ref = value => value ? value.slice(0, 8).toUpperCase() : '—';
const nameOf = status => labels[status] || String(status ?? '').replaceAll('_', ' ');
const glyph = value => `<span class="glyph" aria-hidden="true">${value}</span>`;
const brand = () => '<span class="brand"><img src="/static/mark.svg" width="40" height="40" alt=""><span>Madad<span class="brand-accent">Pass</span></span></span>';
const pill = value => `<span class="pill pill-${e(value)}">${e(nameOf(value))}</span>`;
const empty = (title, detail) => `<div class="empty"><strong>${e(title)}</strong><p>${e(detail)}</p></div>`;
const alert = (text, style = 'note') => `<div class="notice ${style}" role="${style === 'error' ? 'alert' : 'status'}">${e(text)}</div>`;
const button = (title, action, style = 'secondary', extra = '') => `<button type="button" class="button ${style}" data-action="${action}" ${extra}>${title}</button>`;
const top = (tag, title, detail, action = '') => `<header class="page-head"><div><p class="eyebrow">${tag}</p><h1>${title}</h1><p class="lead">${detail}</p></div>${action}</header>`;

function toast(message, error = false) {
  clearTimeout(toastTimer);
  root.textContent = message;
  root.classList.toggle('is-error', error);
  root.hidden = false;
  toastTimer = setTimeout(() => { root.hidden = true; }, 5500);
}

async function api(path, { method = 'GET', body, binary = false, token = session?.token } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store' });
  } catch {
    throw Object.assign(new Error('The connection dropped. Try again when you are online.'), { offline: true });
  }
  if (!response.ok) {
    let reason;
    try { reason = (await response.json()).detail; } catch { reason = ''; }
    throw Object.assign(new Error(reason || `Request failed (${response.status}).`), { status: response.status });
  }
  return binary ? response.blob() : response.json();
}

function accountCache(value) {
  try {
    if (value) sessionStorage.setItem(sessionKey, JSON.stringify(value));
    else sessionStorage.removeItem(sessionKey);
  } catch { /* Private browsing may disable storage. */ }
}

function readAccount() {
  try { return JSON.parse(sessionStorage.getItem(sessionKey)); } catch { return null; }
}

function updateConnection() {
  const display = $('#connection');
  if (display) {
    display.classList.toggle('is-offline', !navigator.onLine);
    display.textContent = navigator.onLine ? 'Online' : 'Offline';
  }
}

function loginView(register = false) {
  const roles = Object.entries(roleNames).map(([value, title]) => `<button type="button" class="role-choice" data-action="demo-login" data-role="${value}">
    ${glyph(roleIcons[value])}<span><strong>${title}</strong><small>${roleDescriptions[value]}</small></span><span aria-hidden="true">→</span></button>`).join('');
  app.innerHTML = `<div class="signin"><section class="signin-story">
    ${brand()}<div class="signin-copy"><span class="eyebrow">COMMUNITY GROCERY RELIEF</span>
      <h1>A helping hand,<br>closer to home.</h1><p>Apply for grocery assistance, follow your progress, and use your approved voucher at a participating shop.</p>
      <p class="urdu" lang="ur" dir="rtl">راشن کی مدد کے لیے درخواست دیں۔</p></div>
    <div class="story-bottom">${glyph('◇')}<span><strong>Fictional prototype</strong><small>Use synthetic names and records. Identity checks and payments are simulated.</small></span></div>
    </section><main id="main" class="signin-panel" tabindex="-1"><section class="signin-body">
      <p class="eyebrow">${register ? 'CREATE AN ACCOUNT' : 'WELCOME TO MADADPASS'}</p>
      <h2>${register ? 'Register a citizen' : 'Sign in'}</h2>
      <p class="lead">${register ? 'Choose a fictional name and username for this demo.' : 'Use a username and password or open a demo role below.'}</p>
      <form id="${register ? 'register' : 'sign-in'}" class="stack">
        ${register ? '<label>Fictional name<input name="name" maxlength="100" required autocomplete="off"></label>' : ''}
        <label>Username<input name="login" maxlength="40" autocomplete="username" required ${register ? 'pattern="[a-z0-9][a-z0-9_-]+" minlength="3"' : ''}></label>
        <label>Password<input name="password" type="password" maxlength="160" autocomplete="${register ? 'new-password' : 'current-password'}" ${register ? 'minlength="10"' : ''} required></label>
        ${register ? '<label class="check"><input type="checkbox" name="synthetic" required>I am using fictional information.</label>' : ''}
        <button class="button primary">${register ? 'Create account' : 'Sign in'} <span aria-hidden="true">→</span></button>
        <div class="form-message" aria-live="polite"></div>
      </form>
      ${register ? button('Back to sign in', 'login-view', 'text') : `${button('Register a citizen account', 'register-view', 'text')}
        <div class="role-heading"><h3>Explore a demo role</h3></div><div class="role-grid">${roles}</div>`}
      <footer class="signin-footer"><a href="/docs">API documentation</a><span>Synthetic data only</span></footer>
    </section></main></div>`;
}

function shell(content) {
  const user = session.user;
  const links = menus[user.role].map(([id, icon, title]) => `<button class="nav-link ${page === id || (page === 'detail' && id === (user.role === 'officer' ? 'home' : 'cases')) ? 'selected' : ''}"
    ${page === id ? 'aria-current="page"' : ''} data-action="navigate" data-page="${id}">${glyph(icon)}<span>${title}</span></button>`).join('');
  app.innerHTML = `<div class="workspace"><aside class="sidebar" aria-label="Workspace navigation">
    <button class="plain brand-button" data-action="navigate" data-page="home" aria-label="MadadPass home">${brand()}</button>
    <div class="workspace-name">${glyph('⌂')}<span><strong>${e(user.organization || 'Community Relief')}</strong><small>Demo workspace</small></span></div>
    <nav>${links}</nav><div class="sidebar-bottom"><a href="/docs">API documentation <span aria-hidden="true">↗</span></a><p>Fictional prototype</p></div>
  </aside><div class="workspace-main"><header class="topbar"><span id="connection" class="connection"></span>
    <div class="account"><span class="avatar">${e(user.name.slice(0, 2).toUpperCase())}</span><span><strong>${e(user.name)}</strong><small>${roleNames[user.role]}</small></span></div>
    ${button('Sign out', 'logout', 'text')}
  </header><main id="main" tabindex="-1" class="main-content">${content}</main>
  <footer class="workspace-footer">MadadPass <span>·</span> Synthetic identities and simulated payments</footer></div></div>`;
  updateConnection();
}

function releaseMedia() {
  cameraStop?.();
  voiceStop?.();
  recorderStop?.();
  loadedBlobs.forEach(URL.revokeObjectURL);
  loadedBlobs = [];
}

async function navigate(target, resource, { focus = true } = {}) {
  if (!session) return;
  const revision = ++navigation;
  releaseMedia();
  page = target;
  if (target !== 'detail') entry = null;
  shell('<p class="loading" role="status">Loading your workspace…</p>');
  const role = session.user.role;
  try {
    let content;
    if (target === 'apply') {
      const sharing = role === 'helper' ? await api('/api/consents').catch(error => { if (error.offline && consent) return consent; throw error; }) : null;
      if (revision !== navigation) return;
      if (sharing) consent = sharing;
      await renderApplication(resource || {});
    } else if (target === 'detail') {
      const sharing = role === 'helper' ? await api('/api/consents') : null;
      const result = await api(`/api/cases/${resource}`);
      if (revision !== navigation) return;
      if (sharing) consent = sharing;
      entry = result.case;
      content = caseDetail(entry);
    } else if (target === 'cases' || (target === 'home' && ['citizen', 'helper', 'officer'].includes(role))) {
      let offline = false;
      let items;
      try { items = (await api('/api/cases')).cases; } catch (error) { if (!error.offline) throw error; items = []; offline = true; }
      if (revision !== navigation) return;
      cases = items;
      content = role === 'officer' ? queuePage(offline) : citizenPage(target, offline);
    } else if (target === 'consent') {
      const sharing = await api('/api/consents');
      if (revision !== navigation) return;
      consent = sharing;
      content = sharingPage();
    } else if (target === 'safety') content = safetyPage();
    else if (target === 'merchant' || (target === 'home' && role === 'merchant')) content = merchantPage();
    else if (target === 'history') content = historyPage((await api('/api/vouchers/history')).redemptions);
    else if (target === 'policy' || (target === 'home' && role === 'admin')) content = policyPage((await api('/api/admin/rules')).policies);
    else if (target === 'users') content = usersPage((await api('/api/admin/users')).users);
    else if (target === 'keys') content = keysPage((await api('/api/admin/keys')).keys);
    else if (target === 'audit' || (target === 'home' && role === 'auditor')) content = auditPage((await api('/api/audit')).events);
    else if (target === 'trace') content = tracePage(await api(`/api/audit/${resource}`));
    if (revision !== navigation) return;
    if (content !== undefined) shell(content);
    if (focus) $('#main')?.focus();
  } catch (error) {
    if (revision !== navigation) return;
    if (error.status === 401) {
      clearSession();
      toast('Your session has expired. Sign in again.', true);
      return;
    }
    shell(`${top('WORKSPACE', 'Page unavailable', '')}${alert(error.message, 'error')}${button('Back to overview', 'navigate', 'secondary', 'data-page="home"')}`);
  }
}

async function signIn(login, password) {
  const result = await api('/api/login', { method: 'POST', body: { login, password }, token: null });
  session = result;
  try { session.user = await api('/api/me'); } catch { /* The account returned at login is sufficient to continue. */ }
  accountCache(session);
  await navigate('home');
  syncPending();
}

function clearSession() {
  releaseMedia();
  navigation += 1;
  clearTimeout(toastTimer);
  root.hidden = true;
  root.textContent = '';
  session = null;
  entry = null;
  consent = null;
  cases = [];
  formContext = null;
  accountCache(null);
  loginView();
}

function caseRows(items, officer = false) {
  if (!items.length) return empty('No applications to show', officer ? 'Applications appear here when a household submits one.' : 'Submitted applications and their progress will appear here.');
  return `<div class="responsive-table"><table><thead><tr><th>Application</th><th>Status</th>${officer ? '<th>Assessment</th>' : ''}<th>Updated</th><th><span class="sr-only">Open application</span></th></tr></thead><tbody>
    ${items.map(item => `<tr><td><strong>${e(item.applicant)}</strong><small>MP-${ref(item.id)}</small></td><td>${pill(item.status)}</td>
    ${officer ? `<td>${item.assessment ? pill(item.assessment.route) : '—'}</td>` : ''}
    <td>${e(stamp(item.updated_at))}</td><td>${button('Open <span aria-hidden="true">→</span>', 'open-case', 'text', `data-id="${e(item.id)}" aria-label="Open ${e(item.applicant)} application"`)}</td></tr>`).join('')}
    </tbody></table></div>`;
}

function citizenPage(target, offline) {
  const helper = session.user.role === 'helper';
  if (target === 'cases') return `${top('APPLICATIONS', helper ? 'Shared applications' : 'Track applications', helper ? 'Open a case shared with your account.' : 'See the latest decision, requests for information, and approved vouchers.', button(helper ? 'Start application' : 'Apply for help', 'navigate', 'primary', 'data-page="apply"'))}
    <section class="panel">${offline ? alert('Application details are unavailable offline. You can still edit a local draft.') : caseRows(cases)}</section>`;
  const actions = [
    ['+', helper ? 'Start an application' : 'Apply for help', helper ? 'Work on behalf of a household that has granted access.' : 'Request a grocery voucher for your household.', 'apply', ''],
    ['◉', 'Speak in Urdu', 'Dictate your request or try a fictional Urdu example.', 'apply', 'data-mode="voice"'],
    ['▤', helper ? 'Shared applications' : 'Track applications', helper ? 'See applications shared by each household.' : 'Check your status and open an approved voucher.', 'cases', ''],
  ];
  return `${top('RELIEF WORKSPACE', `Welcome, ${e(session.user.name.split(' ')[0])}`, helper ? 'Support households that have chosen to share access with you.' : 'Get grocery assistance and follow your application in one place.')}
    <section class="hero"><div><p class="eyebrow">GROCERY ASSISTANCE</p><h2>${helper ? 'Help a household apply.' : 'Apply for help with groceries.'}</h2>
    <p>${helper ? 'Choose an authorized household and check the details together before submitting.' : 'Share a few household details. A review officer will check your application and let you know what happens next.'}</p>
    ${button(`${helper ? 'Start an application' : 'Start application'} <span aria-hidden="true">→</span>`, 'navigate', 'hero-button', 'data-page="apply"')}</div>
    <div class="hero-art" aria-hidden="true"><span class="hero-ticket"><b>✦</b><i></i><i></i><i></i></span></div></section>
    <div class="quick-actions">${actions.map(([icon, title, desc, dest, extra]) => `<button class="action-card" data-action="navigate" data-page="${dest}" ${extra}>${glyph(icon)}<span><strong>${title}</strong><small>${desc}</small></span><span class="arrow" aria-hidden="true">↗</span></button>`).join('')}</div>
    <section class="panel"><div class="section-head"><div><h2>${helper ? 'Recently shared' : 'Recent applications'}</h2><p>${helper ? 'Households with active consent' : 'Check progress and review requests'}</p></div>${button('View all →', 'navigate', 'text', 'data-page="cases"')}</div>
    ${offline ? alert('You are offline. Apply for help to continue a local draft.') : caseRows(cases.slice(0, 4))}</section>`;
}

function queuePage(offline) {
  const open = cases.filter(item => !['approved', 'rejected'].includes(item.status));
  return `${top('OFFICER WORKSPACE', 'Review queue', 'Review requests and evidence, resolve flagged matches, and record a decision.', button('Refresh queue', 'navigate', 'secondary', 'data-page="home"'))}
    <div class="stats"><div><small>OPEN APPLICATIONS</small><strong>${open.length}</strong></div><div><small>MANUAL REVIEW</small><strong>${open.filter(item => item.assessment?.route === 'human_review' || item.status === 'escalated').length}</strong></div>
    <div><small>NEEDS INFORMATION</small><strong>${open.filter(item => item.assessment?.route === 'needs_information').length}</strong></div><div><small>APPROVED</small><strong>${cases.filter(item => item.status === 'approved').length}</strong></div></div>
    <section class="panel"><div class="section-head"><div><h2>Applications</h2><p>Only cases in your workspace are shown.</p></div>
    <label class="inline-label">Filter<select id="queue-filter"><option value="open">Open cases</option><option value="all">All cases</option><option value="human_review">Manual review</option><option value="needs_information">Needs information</option><option value="eligible">Ready for review</option><option value="approved">Approved</option><option value="rejected">Closed · declined</option></select></label></div>
    <div id="queue-rows">${offline ? alert('The queue requires a server connection.', 'error') : caseRows(sortQueue(open), true)}</div></section>`;
}

function sortQueue(items) {
  const priority = item => item.status === 'escalated' ? 0 : ['human_review', 'needs_information', 'eligible', 'outside_policy'].indexOf(item.assessment?.route) + 1;
  return [...items].sort((a, b) => priority(a) - priority(b) || b.updated_at.localeCompare(a.updated_at));
}

function applicantOptions(selected) {
  return (consent?.consents || []).filter(grant => grant.scope === 'assist').map(grant =>
    `<option value="${e(grant.citizen)}" ${grant.citizen === selected ? 'selected' : ''}>${e(grant.citizen_name)}</option>`).join('');
}

function draftKey(account, citizen) {
  return `${account}:${citizen}:new`;
}

function appFields(saved = {}) {
  return `<div class="form-grid"><label>Fictional applicant name<input name="applicant" value="${e(saved.applicant)}" maxlength="100" required></label>
    <label>Synthetic household reference<input name="identity_ref" value="${e(saved.identity_ref)}" maxlength="30" pattern="HH-[A-Za-z0-9]{1,20}" placeholder="HH-201"></label>
    <label>Area or fictional address<input name="address" value="${e(saved.address)}" maxlength="180" placeholder="Example: 17 Harbor Walk"></label>
    <label>People in your household<input name="family_members" type="number" value="${e(saved.family_members)}" min="1" max="40" step="1" inputmode="numeric"></label>
    <label>Monthly household income (PKR)<input name="monthly_income" type="number" value="${e(saved.monthly_income)}" min="0" max="10000000" step="1" inputmode="numeric"></label></div>`;
}

const urduExample = 'میرے گھر میں ۵ افراد ہیں۔ ماہانہ آمدنی: ۳۰۰۰۰ روپے۔ ہمیں راشن کی مدد چاہیے۔';

async function renderApplication(options) {
  const current = navigation;
  const account = session.user;
  const isHelper = account.role === 'helper';
  const editing = options.edit || null;
  const citizen = editing?.citizen || (isHelper ? options.citizen || (consent?.consents || []).find(item => item.scope === 'assist')?.citizen : account.id);
  if (!citizen) {
    shell(`${top('APPLICATION', 'Start an application', 'Choose a household that granted you permission to apply.')}
      <section class="panel">${empty('No households available', 'A citizen can give you application access from Sharing access.')}</section>`);
    return;
  }
  const key = draftKey(account.id, citizen);
  let draft;
  if (!editing) try { draft = await drafts.get(key); } catch { /* The form can still be used online. */ }
  if (navigation !== current) return;
  const applicant = isHelper ? consent?.consents.find(item => item.citizen === citizen)?.citizen_name : account.name;
  const saved = editing || draft?.fields || { applicant };
  formContext = { citizen, key, editing, requestId: draft?.requestId || crypto.randomUUID(), queued: !!draft?.pending, account: account.id };
  const voice = options.mode === 'voice';
  shell(`${top('GROCERY ASSISTANCE', editing ? 'Correct application details' : 'Apply for help', 'Enter what you know. You can leave uncertain details blank and finish them with a reviewer.', button('Listen in Urdu', 'speak-help', 'secondary'))}
    ${isHelper && !editing ? `<section class="panel family-picker"><label>Household you are assisting<select id="family-select">${applicantOptions(citizen)}</select></label></section>` : ''}
    <div class="form-layout"><form class="panel stack" id="application-form"><div class="section-head"><div><h2>Household information</h2><p>Use only fictional information.</p></div><span class="draft-state" id="draft-state" aria-live="polite">${draft?.pending ? 'Waiting to sync' : draft ? 'Draft restored' : ''}</span></div>
      <fieldset ${draft?.pending ? 'disabled' : ''} id="application-fields">${appFields(saved)}
      <label class="check"><input name="confirmed" type="checkbox" required>I checked these details and used fictional information.</label>
      <button class="button primary">${editing ? 'Save corrections' : 'Submit application'} <span aria-hidden="true">→</span></button></fieldset>
      ${draft?.pending ? '<div id="pending-help" class="notice">This confirmed application will be sent when the connection returns.</div>' : ''}
      <div class="form-message" aria-live="polite"></div>
    </form><section class="panel stack" id="assist-panel"><div class="section-head"><div><h2>${voice ? 'Speak in Urdu' : 'Get help filling the form'}</h2><p>Describe your household in English or Urdu.</p></div></div>
      <label for="statement">Your request</label><textarea id="statement" rows="5" maxlength="3500" dir="auto" placeholder="My household has 5 people. Monthly income: PKR 30,000.">${e(draft?.statement || '')}</textarea>
      <div class="button-row">${button('Suggest details', 'extract', 'secondary')}${button('Try Urdu example', 'urdu-sample', 'text')}</div>
      <details ${voice ? 'open' : ''}><summary>Urdu voice options</summary>
        <label class="check"><input type="checkbox" id="voice-consent">Allow a speech service to process fictional audio.</label>
        <div class="button-row">${button('Dictate in Urdu', 'dictate', 'secondary')}${health.groq ? button('Record for transcription', 'record', 'secondary') : ''}</div>
        ${health.groq ? '<label>Or upload a fictional recording<input id="audio-file" type="file" accept="audio/*"></label>' : ''}
        <small>Voice availability depends on your browser and settings. You can always type.</small>
      </details>
      ${health.gemini ? '<label class="check"><input id="cloud-extract" type="checkbox">Use Gemini for this fictional request.</label>' : ''}
      <div id="assist-message" aria-live="polite"></div>
    </section></div>`);
  if (voice) $('#statement')?.focus();
}

function fieldsFrom(form) {
  const data = new FormData(form);
  const number = key => data.get(key) === '' ? null : Number(data.get(key));
  return { applicant: String(data.get('applicant') || '').trim(), identity_ref: String(data.get('identity_ref') || '').trim().toUpperCase(),
    address: String(data.get('address') || '').trim(), family_members: number('family_members'), monthly_income: number('monthly_income') };
}

async function persistDraft(pending = false) {
  const context = formContext;
  const form = $('#application-form');
  if (!form || !context || context.editing || (context.queued && !pending)) return;
  const fields = fieldsFrom(form);
  const statement = $('#statement')?.value || '';
  try {
    await drafts.put(context.key, { ...context, fields, statement, pending });
    if ($('#draft-state') && context === formContext) $('#draft-state').textContent = pending ? 'Waiting to sync' : 'Saved on this device';
    return true;
  } catch {
    if ($('#draft-state') && context === formContext) $('#draft-state').textContent = 'Local storage unavailable';
    return false;
  }
}

function submission(fields, context) {
  return { ...fields, citizen: context.citizen, request_id: context.requestId, confirmed: true };
}

async function syncPending() {
  if (!session || !navigator.onLine || syncTask) return;
  const account = session.user.id;
  const token = session.token;
  syncTask = (async () => {
    let records;
    try { records = await drafts.all(); } catch { return; }
    for (const draft of records.filter(item => item.account === account && item.pending)) {
      if (session?.token !== token) break;
      try {
        await api('/api/cases', { method: 'POST', body: submission(draft.fields, draft), token });
        await drafts.remove(draft.key);
        if (session?.token !== token) break;
        toast('Your offline application was submitted.');
        if (page === 'apply' && formContext?.key === draft.key && formContext.queued) await navigate('cases');
      } catch (error) {
        if (error.offline || error.status === 401) break;
        if (error.status >= 500 || error.status === 429) continue;
        await drafts.put(draft.key, { ...draft, pending: false });
        if (session?.token === token) toast(`Offline application needs attention. ${error.message}`, true);
        if (page === 'apply' && formContext?.key === draft.key && formContext.queued) await navigate('apply', { citizen: draft.citizen });
      }
    }
  })();
  try { await syncTask; } finally { syncTask = null; }
}

function caseDetail(item) {
  const role = session.user.role;
  const isOfficer = role === 'officer';
  const closed = ['approved', 'rejected'].includes(item.status);
  const helperCanEdit = role === 'helper' && consent?.consents.some(grant => grant.citizen === item.citizen && grant.scope === 'assist');
  const canEdit = !closed && (role === 'citizen' || helperCanEdit);
  const findings = item.assessment;
  const stale = findings && findings.version !== item.current_policy && !closed;
  const decisionButtons = `<div class="button-row">${button('Assign to me', 'assign', 'secondary')}${button('Reassess case', 'reassess', 'secondary')}</div>`;
  return `${button('← Back to applications', 'navigate', 'text breadcrumb', `data-page="${isOfficer ? 'home' : 'cases'}"`)}
    ${top(`APPLICATION MP-${ref(item.id)}`, e(item.applicant), `Updated ${e(stamp(item.updated_at))}`, pill(item.status))}
    <div class="detail-layout"><div class="stack"><section class="panel stack"><div class="section-head"><div><h2>Household details</h2><p>Grocery assistance</p></div>${canEdit ? button('Correct details', 'edit-case', 'secondary') : ''}</div>
    <dl class="facts"><div><dt>Household reference</dt><dd>${e(item.identity_ref) || '—'}</dd></div><div><dt>Area or address</dt><dd>${e(item.address) || '—'}</dd></div><div><dt>People in household</dt><dd>${e(item.family_members ?? '—')}</dd></div><div><dt>Monthly income</dt><dd>${pk(item.monthly_income)}</dd></div></dl></section>
    ${findings ? `<section class="panel stack"><div class="section-head"><div><h2>Assessment</h2><p>Grocery policy · version ${findings.version}</p></div>${pill(findings.route)}</div>
      <p>${e(findings.explanation)}</p>${stale ? alert(`Policy v${item.current_policy} is now active. Reassessment is needed before a new decision.`, 'warning') : ''}
      ${findings.warnings.map(warning => alert(warning, 'warning')).join('')}
      ${isOfficer && findings.matches?.length ? `<details><summary>${findings.match_count} possible household match${findings.match_count === 1 ? '' : 'es'}</summary>
        <p>These are similarity measures. Review the records before drawing a conclusion.</p><ul class="details-list">${findings.matches.map(match =>
          `<li>MP-${ref(match.case_id)} · ${match.same_reference ? 'Same household reference · ' : ''}Name ${Math.round(match.name_similarity * 100)}% · Address ${Math.round(match.address_similarity * 100)}% ${button('Open case', 'open-case', 'text', `data-id="${e(match.case_id)}"`)}</li>`).join('')}</ul></details>` : ''}
      ${findings.references?.length ? `<details><summary>Policy sources</summary><div class="source-list">${sourceCards(findings.references)}</div></details>` : ''}</section>` : ''}
    <section class="panel stack"><div class="section-head"><div><h2>Evidence</h2><p>Fictional income statements and supporting records</p></div><a class="button text" href="/sample/income.txt">Sample income statement ↓</a></div>
    ${item.documents.length ? `<div class="document-list">${item.documents.map(doc => `<div class="document"><div><strong>${e(doc.filename)}</strong><small>${e(doc.extraction.document_type === 'income_statement' ? 'Income statement' : 'Document type uncertain')} · ${e(doc.extraction.model)}</small>
        <small>Extracted income: ${pk(doc.extraction.monthly_income)} · Members: ${e(doc.extraction.family_members ?? '—')}</small></div>
        ${button('View original', 'view-document', 'text', `data-id="${e(doc.id)}"`)}</div>`).join('')}</div>` : empty('No evidence available', canEdit ? 'Add a fictional document so an officer can review it.' : 'Documents appear here when they are available to your role.')}
    <div id="document-preview" aria-live="polite"></div>
    ${canEdit ? `<form id="upload-evidence" class="stack inset"><label>Attach evidence<input name="file" type="file" accept=".txt,.png,.jpg,.jpeg,text/plain,image/png,image/jpeg" required></label>
      <label class="check"><input name="synthetic" type="checkbox" required>This document contains fictional information.</label>
      ${health.gemini ? '<label class="check"><input name="provider" type="checkbox">Allow Gemini to inspect this fictional image.</label>' : ''}
      <div class="button-row"><button class="button secondary">Upload evidence</button><div class="form-message" aria-live="polite"></div></div></form>` : ''}</section></div>
    <div class="stack">${isOfficer && !closed ? `<section class="panel stack"><div class="section-head"><div><h2>Officer action</h2><p>${item.assigned_to === session.user.id ? 'Assigned to you' : item.assigned_to ? 'Assigned to another officer' : 'Unassigned'}</p></div></div>
      ${decisionButtons}<form id="case-decision" class="stack"><label>Decision<select name="action" required><option value="">Choose a decision</option>
      <option value="approve" ${stale || findings?.missing.length || findings?.failed.length ? 'disabled' : ''}>Approve and issue voucher</option><option value="request_information">Request more information</option>
      <option value="escalate">Escalate for manual review</option><option value="reject">Decline and close</option></select></label>
      <label>Reason visible to the citizen<textarea name="reason" rows="4" minlength="8" maxlength="1200" required placeholder="Explain what you checked or what is needed."></textarea></label>
      <label class="check"><input name="verified" type="checkbox">I verified the details, reviewed the evidence, and resolved any flagged matches.</label>
      <button class="button primary">Record decision</button><div class="form-message" aria-live="polite"></div></form></section>` : ''}
    ${item.voucher ? voucherPanel(item.voucher) : ''}<section class="panel"><div class="section-head"><div><h2>Decision history</h2><p>Officer actions and policy versions</p></div></div>
    ${item.decisions.length ? `<ol class="timeline">${item.decisions.map(decision => `<li><strong>${e(nameOf(decision.action))}</strong><small>${e(stamp(decision.created_at))} · Policy v${decision.version}</small><p>${e(decision.reason)}</p></li>`).join('')}</ol>` : `<ol class="timeline"><li><strong>Application submitted</strong><small>${e(stamp(item.created_at))}</small><p>Waiting for an officer's decision.</p></li></ol>`}</section></div></div>`;
}

function sourceCards(references) {
  return references.map(source => `<blockquote><p>${e(source.text)}</p><cite>${e(source.rule)} · Policy v${source.version}</cite></blockquote>`).join('');
}

function voucherPanel(voucher) {
  return `<section class="panel voucher stack"><div class="section-head"><div><h2>Grocery voucher</h2><p>One simulated redemption</p></div>${pill(voucher.status)}</div>
    <p class="voucher-amount">${pk(voucher.value)}</p><small>Expires ${e(stamp(voucher.expires * 1000))}</small>
    ${voucher.status === 'issued' ? `${button('Show voucher QR', 'show-voucher', 'primary', `data-id="${e(voucher.id)}"`)}<div id="voucher-code" aria-live="polite"></div>` : ''}</section>`;
}

function sharingPage() {
  const helper = session.user.role === 'helper';
  const list = consent.consents.map(item => `<article class="share-row">${glyph('♧')}<div><strong>${e(helper ? item.citizen_name : item.helper_name)}</strong>
    <small>${e(scopeLabels[item.scope])} · ${e(stamp(item.granted_at))}</small></div>${item.revoked_at ? '<span class="pill">Withdrawn</span>' : helper ? '<span class="pill pill-approved">Active</span>' :
    button('Withdraw access', 'revoke-consent', 'danger', `data-id="${e(item.id)}"`)}</article>`).join('');
  return `${top('CONSENT', helper ? 'Household access' : 'Sharing access', helper ? 'See who has shared access and what you may do for each household.' : 'Choose who can see or work on your applications. You can withdraw access here.')}
    <div class="detail-layout"><section class="panel stack"><div class="section-head"><div><h2>${helper ? 'Shared with you' : 'Your sharing history'}</h2><p>Permissions are checked each time a helper opens a case.</p></div></div>
    ${list || empty('No sharing records', helper ? 'A household must share access first.' : 'Your applications are currently private to the review process.')}</section>
    ${!helper ? `<form class="panel stack" id="share-consent"><h2>Give a helper access</h2><label>Helper<select name="helper" required><option value="">Choose a helper</option>
      ${consent.helpers.map(person => `<option value="${e(person.id)}">${e(person.name)}</option>`).join('')}</select></label>
      <label>Permission<select name="scope">${Object.entries(scopeLabels).map(([id, title]) => `<option value="${id}">${title}</option>`).join('')}</select></label>
      <button class="button primary">Save permission</button><div class="form-message" aria-live="polite"></div></form>` : ''}</div>`;
}

function safetyPage() {
  return `${top('RELIEF SAFETY', 'Check a message', 'Paste a fictional relief message to look for requests for fees, codes, or suspicious payment links.')}
    <div class="detail-layout"><form id="check-message" class="panel stack"><label>Message<textarea name="text" rows="6" maxlength="3000" required placeholder="Paste a fictional message"></textarea></label>
    <div class="button-row"><button class="button primary">Check message</button>${button('Try an example', 'scam-sample', 'text')}</div><div class="form-message" aria-live="polite"></div></form>
    <section class="panel stack"><h2>Screening result</h2><div id="message-result" aria-live="polite">${empty('No message checked', 'Signals and suggested next steps will appear here.')}</div></section></div>`;
}

function merchantPage() {
  return `${top('MERCHANT WORKSPACE', 'Redeem a voucher', 'Scan the QR code or enter the token shown by the citizen. Each voucher can be used once.', button('View redemption history', 'navigate', 'secondary', 'data-page="history"'))}
    <div class="detail-layout"><form class="panel stack" id="redeem-form"><div class="section-head"><div><h2>Voucher verification</h2><p>Use only a MadadPass voucher from this workspace.</p></div>${glyph('▣')}</div>
      <div class="button-row">${button('Open camera scanner', 'camera', 'secondary')}</div>
      <label class="file-compact">Scan a QR image<input id="qr-file" type="file" accept="image/*"></label>
      <video id="scan-video" muted playsinline hidden aria-label="QR camera preview"></video>
      <label>Voucher token<input name="token" maxlength="80" spellcheck="false" autocomplete="off" placeholder="mp_…" required></label>
      <button class="button primary">Redeem voucher</button><div class="form-message" aria-live="polite"></div></form>
    <section class="panel stack"><div class="section-head"><div><h2>Redemption result</h2><p>Simulated settlement</p></div></div>
      <div id="redeem-result" aria-live="polite">${empty('Waiting for a voucher', 'After verification, the receipt or reason for failure will appear here.')}</div></section></div>`;
}

function historyPage(items) {
  return `${top('MERCHANT WORKSPACE', 'Redemption history', 'Review your store’s simulated redemption receipts.', button('Redeem a voucher', 'navigate', 'primary', 'data-page="home"'))}
    <section class="panel">${items.length ? `<div class="responsive-table"><table><thead><tr><th>Receipt</th><th>Value</th><th>Redeemed</th><th>Settlement</th></tr></thead><tbody>
    ${items.map(item => `<tr><td><strong>MP-${ref(item.id)}</strong></td><td>${pk(item.value)}</td><td>${e(stamp(item.created_at))}</td><td>${ref(item.settlement)}</td></tr>`).join('')}</tbody></table></div>` : empty('No redemptions yet', 'Successful redemptions and receipts are listed here.')}</section>`;
}

function policyPage(versions) {
  const admin = session.user.role === 'admin';
  const policy = versions[0];
  const versionList = versions.map((item, index) => `<details ${index === 0 ? 'open' : ''}><summary>Version ${item.version} ${index === 0 ? '· Active' : ''}</summary>
    <small>Published ${e(stamp(item.published_at))}</small><div class="source-list">${sourceCards(Object.entries(item.excerpts).map(([rule, text]) => ({ rule: `GROCERY-${rule}`, text, version: item.version })))}</div></details>`).join('');
  return `${top('GROCERY PROGRAM', 'Policy rules', admin ? 'Preview a change against open cases and publish a new version.' : 'Read eligibility rules and the policy versions used in assessments.')}
    <div class="detail-layout"><div class="stack">${admin ? `<form id="policy-form" class="panel stack" data-version="${policy.version}"><div class="section-head"><div><h2>Edit grocery rules</h2><p>This workspace is currently using policy v${policy.version}.</p></div></div>
      <div class="form-grid"><label>Minimum household size<input type="number" name="min_members" value="${policy.min_members}" min="1" max="40" required></label>
      <label>Maximum monthly income (PKR)<input type="number" name="max_income" value="${policy.max_income}" min="0" max="10000000" required></label>
      <label>Voucher amount (PKR)<input type="number" name="voucher_value" value="${policy.voucher_value}" min="100" max="100000" required></label></div>
      <label class="check"><input type="checkbox" name="evidence_required" ${policy.evidence_required ? 'checked' : ''}>Require an income statement.</label>
      <div class="button-row">${button('Preview impact', 'preview-policy', 'secondary')}<button class="button primary">Publish version ${policy.version + 1}</button></div>
      <div class="form-message" aria-live="polite"></div><div id="preview-result" aria-live="polite"></div></form>` : ''}
      <form id="policy-search" class="panel stack"><h2>Find a rule</h2><label>Search policy text<input name="query" maxlength="250" placeholder="Example: income limit" required></label>
      <button class="button secondary">Find policy passages</button><div class="form-message" aria-live="polite"></div><div id="policy-results" class="source-list" aria-live="polite"></div></form></div>
    <section class="panel stack"><h2>Policy versions</h2>${versionList}</section></div>`;
}

function usersPage(items) {
  return `${top('ADMINISTRATION', 'People and roles', 'Review workspace accounts and edit staff access.')}
    <section class="panel stack">${items.map(person => `<form class="person-form" data-id="${e(person.id)}"><div><strong>${e(person.name)}</strong><small>${e(person.login)}</small></div>
    <label><span class="sr-only">Role for ${e(person.name)}</span><select name="role" ${person.id === session.user.id || person.role === 'citizen' ? 'disabled' : ''}>
    ${Object.entries(roleNames).filter(([id]) => person.role === 'citizen' ? id === 'citizen' : id !== 'citizen').map(([id, text]) => `<option value="${id}" ${id === person.role ? 'selected' : ''}>${text}</option>`).join('')}</select></label>
    <label class="check"><input type="checkbox" name="active" ${person.active ? 'checked' : ''} ${person.id === session.user.id ? 'disabled' : ''}>Active</label>
    <button class="button secondary" ${person.id === session.user.id ? 'disabled' : ''}>Save</button><div class="form-message" aria-live="polite"></div></form>`).join('')}</section>`;
}

function keysPage(items) {
  return `${top('ADMINISTRATION', 'Mock API keys', 'Create keys for simulated identity, data, and payment endpoints.')}
    <div class="detail-layout"><section class="panel stack"><h2>Workspace keys</h2>${items.map(item => `<div class="share-row"><div><strong>${e(item.label)}</strong><small>${e(stamp(item.created_at))}</small></div>
    ${item.revoked_at ? '<span class="pill">Revoked</span>' : button('Revoke key', 'revoke-key', 'danger', `data-id="${e(item.id)}"`)}</div>`).join('') || empty('No integration keys', 'Create a key to try a mock API.')}</section>
    <form id="create-key" class="panel stack"><h2>Create a key</h2><label>Key label<input name="label" maxlength="60" required placeholder="Local demo integration"></label>
    <button class="button primary">Create key</button><div class="form-message" aria-live="polite"></div></form></div>`;
}

function auditPage(events) {
  const auditor = session.user.role === 'auditor';
  return `${top('WORKSPACE OVERSIGHT', 'Audit events', 'Read a redacted trail of decisions, consent changes, and simulated service actions.', button('Refresh events', 'navigate', 'secondary', `data-page="${auditor ? 'home' : 'audit'}"`))}
    <section class="panel"><div class="responsive-table"><table><thead><tr><th>Event</th><th>Role</th><th>Application</th><th>Time</th></tr></thead><tbody>
    ${events.map(item => `<tr><td><strong>${e(item.action.replaceAll('.', ' · ').replaceAll('_', ' '))}</strong><details><summary>Event details</summary><pre>${e(JSON.stringify(item.details, null, 2))}</pre></details></td>
    <td>${e(roleNames[item.role] || item.role)}</td><td>${item.case_id ? auditor ? button(`MP-${ref(item.case_id)} →`, 'open-trace', 'text', `data-id="${e(item.case_id)}"`) : `MP-${ref(item.case_id)}` : '—'}</td>
    <td>${e(stamp(item.created_at))}</td></tr>`).join('')}</tbody></table></div></section>`;
}

function tracePage(trace) {
  return `${button('← Back to audit events', 'navigate', 'text breadcrumb', 'data-page="home"')}
    ${top(`APPLICATION MP-${ref(trace.case)}`, 'Assessment trace', 'Review versioned model findings and the officer decisions they supported.')}
    <div class="detail-layout"><section class="panel stack"><h2>Assessments</h2>${trace.assessments.map(item => `<details><summary>Policy v${item.version} · ${e(nameOf(item.findings.route))}</summary>
    <small>${ref(item.id)} · ${e(stamp(item.created_at))}</small><pre>${e(JSON.stringify(item.findings, null, 2))}</pre></details>`).join('')}</section>
    <section class="panel stack"><h2>Officer decisions</h2>${trace.decisions.map(item => `<div class="share-row"><div><strong>${e(nameOf(item.action))}</strong><small>Assessment ${ref(item.assessment)} · ${e(stamp(item.created_at))}</small></div></div>`).join('') || empty('No decisions yet', 'The case is waiting for a human decision.')}</section></div>`;
}

function formMessage(form, message, style = 'error') {
  const target = $('.form-message', form);
  if (target) target.innerHTML = message ? alert(message, style) : '';
}

async function handleSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const fields = new FormData(form);
  const submitter = event.submitter;
  submitter && (submitter.disabled = true);
  formMessage(form, '');
  try {
    if (form.id === 'sign-in') await signIn(fields.get('login'), fields.get('password'));
    else if (form.id === 'register') {
      const login = fields.get('login');
      const password = fields.get('password');
      await api('/api/register', { method: 'POST', token: null, body: { login, name: fields.get('name'), password, synthetic: fields.has('synthetic') } });
      await signIn(login, password);
    } else if (form.id === 'application-form') {
      const context = formContext;
      const data = fieldsFrom(form);
      if (context.editing) {
        await api(`/api/cases/${context.editing.id}`, { method: 'PATCH', body: { ...data, confirmed: fields.has('confirmed') } });
        toast('Application details updated.');
        await navigate('detail', context.editing.id);
      } else {
        await persistDraft();
        try {
          const result = await api('/api/cases', { method: 'POST', body: submission(data, context) });
          await drafts.remove(context.key).catch(() => {});
          toast('Application submitted. You can attach evidence below.');
          await navigate('detail', result.case.id);
        } catch (error) {
          if (!error.offline) throw error;
          if (!(await persistDraft(true))) throw new Error('No connection or local storage is available. Keep this page open to retry.');
          context.queued = true;
          $('#application-fields').disabled = true;
          formMessage(form, 'Application saved for automatic submission when you reconnect.', 'note');
          syncPending();
        }
      }
    } else if (form.id === 'upload-evidence') {
      const file = fields.get('file');
      if (!file.size || file.size > 3000000) throw new Error('Choose a document under 3 MB.');
      const data = { filename: file.name, mime: file.type || 'text/plain', data: await fileBase64(file), synthetic: fields.has('synthetic'), provider: fields.has('provider') };
      const caseId = entry.id;
      await api(`/api/cases/${caseId}/documents`, { method: 'POST', body: data });
      toast('Evidence uploaded and the application reassessed.');
      await navigate('detail', caseId);
    } else if (form.id === 'case-decision') {
      const caseId = entry.id;
      await api(`/api/cases/${caseId}/decision`, { method: 'PATCH', body: { assessment: entry.assessment.id, action: fields.get('action'), reason: fields.get('reason'), verified: fields.has('verified') } });
      toast(fields.get('action') === 'approve' ? 'Application approved. Voucher issued.' : 'Officer decision recorded.');
      await navigate('detail', caseId);
    } else if (form.id === 'share-consent') {
      await api('/api/consents', { method: 'POST', body: Object.fromEntries(fields) });
      toast('Sharing permission saved.');
      await navigate('consent');
    } else if (form.id === 'check-message') {
      const result = await api('/api/ai/message', { method: 'POST', body: Object.fromEntries(fields) });
      $('#message-result').innerHTML = `<div class="stack">${pill(result.route)}${result.signals.length ? `<ul class="details-list">${result.signals.map(item => `<li>${e(item)}</li>`).join('')}</ul>` : '<p>No configured warning patterns matched. Verify the message with your relief provider.</p>'}<p>${e(result.message)}</p></div>`;
    } else if (form.id === 'redeem-form') {
      const token = fields.get('token').trim();
      const pending = form.pendingRedemption;
      const requestId = pending?.token === token ? pending.id : crypto.randomUUID();
      form.pendingRedemption = { token, id: requestId };
      try {
        const receipt = await api('/api/vouchers/redeem', { method: 'POST', body: { token, request_id: requestId } });
        $('#redeem-result').innerHTML = `<div class="result good"><span aria-hidden="true">✓</span><h3>${receipt.replayed ? 'Receipt recovered' : 'Voucher redeemed'}</h3>
          <p class="voucher-amount">${pk(receipt.value)}</p><p>Receipt MP-${ref(receipt.id)} · Mock settlement ${ref(receipt.settlement)}</p></div>`;
        form.pendingRedemption = null;
      } catch (error) {
        if (!error.offline && error.status < 500) form.pendingRedemption = null;
        $('#redeem-result').innerHTML = `<div class="result bad"><span aria-hidden="true">×</span><h3>Redemption unavailable</h3><p>${e(error.message)}</p></div>`;
      }
    } else if (form.id === 'policy-form') {
      const result = await api('/api/admin/rules', { method: 'PATCH', body: { ...ruleFields(form), base_version: Number(form.dataset.version) } });
      toast(`Policy v${result.version} published.`);
      await navigate('home');
    } else if (form.id === 'policy-search') {
      const result = await api('/api/ai/policy', { method: 'POST', body: Object.fromEntries(fields) });
      $('#policy-results').innerHTML = result.references.length ? sourceCards(result.references) : alert('No policy passage matched those words.');
    } else if (form.classList.contains('person-form')) {
      await api(`/api/admin/users/${form.dataset.id}`, { method: 'PATCH', body: { role: form.elements.role.value, active: fields.has('active') } });
      formMessage(form, 'Access settings saved.', 'success');
    } else if (form.id === 'create-key') {
      const result = await api('/api/admin/keys', { method: 'POST', body: Object.fromEntries(fields) });
      await navigate('keys');
      const message = $('#create-key .form-message');
      if (message) message.innerHTML = `<div class="notice success"><p>Save this key now. It is shown only once.</p><code class="break-all">${e(result.key)}</code></div>`;
    }
  } catch (error) {
    formMessage(form, error.message);
  } finally {
    submitter && (submitter.disabled = false);
  }
}

function ruleFields(form) {
  const fields = new FormData(form);
  return { min_members: Number(fields.get('min_members')), max_income: Number(fields.get('max_income')),
    voucher_value: Number(fields.get('voucher_value')), evidence_required: fields.has('evidence_required') };
}

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => reject(new Error('The selected file could not be read.'));
    reader.readAsDataURL(file);
  });
}

function textOutput(selector, text, kind = 'note') {
  const target = $(selector);
  if (target) target.innerHTML = alert(text, kind);
}

async function useTranscript(file) {
  if (!$('#voice-consent')?.checked) throw new Error('Allow speech processing before submitting fictional audio.');
  if (!file.size || file.size > 5500000) throw new Error('Use a short audio recording under 5.5 MB.');
  const target = $('#statement');
  const result = await api('/api/ai/transcribe', { method: 'POST', body: { filename: file.name || 'speech.webm', mime: file.type, data: await fileBase64(file), provider: true } });
  if (target !== $('#statement')) return;
  target.value = result.text;
  await persistDraft();
  textOutput('#assist-message', 'Transcript added. Check the words, then choose Suggest details.');
}

function dictate() {
  if (!$('#voice-consent')?.checked) throw new Error('Allow speech processing before starting Urdu dictation.');
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) throw new Error('Urdu browser dictation is unavailable here. Use the text box or the Urdu example.');
  voiceStop?.();
  const speech = new Recognition();
  const target = $('#statement');
  speech.lang = 'ur-PK';
  speech.interimResults = false;
  speech.onresult = async event => {
    if (target !== $('#statement')) return;
    target.value = event.results[0][0].transcript.slice(0, 3500);
    await persistDraft();
    textOutput('#assist-message', 'Check the transcript before using it.');
  };
  speech.onerror = () => {
    if (target === $('#statement')) textOutput('#assist-message', 'The browser could not transcribe this recording. You can type or try the Urdu example.', 'error');
  };
  speech.onend = () => { if (voiceStop === stop) voiceStop = null; };
  const stop = () => { speech.onresult = null; speech.onerror = null; speech.abort(); voiceStop = null; };
  voiceStop = stop;
  speech.start();
  textOutput('#assist-message', 'Listening in Urdu…');
}

async function record(buttonNode) {
  if (recorderStop) { recorderStop(true); return; }
  if (!$('#voice-consent')?.checked) throw new Error('Allow speech processing before recording.');
  if (!navigator.mediaDevices || !window.MediaRecorder) throw new Error('This browser cannot record audio here.');
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  if (!buttonNode.isConnected) { stream.getTracks().forEach(track => track.stop()); return; }
  const recorder = new MediaRecorder(stream);
  const parts = [];
  let upload = false;
  const stop = shouldUpload => {
    upload = shouldUpload;
    clearTimeout(limit);
    if (recorder.state !== 'inactive') recorder.stop();
    stream.getTracks().forEach(track => track.stop());
    recorderStop = null;
    buttonNode.textContent = 'Record for transcription';
  };
  const limit = setTimeout(() => stop(true), 30000);
  recorder.ondataavailable = event => { if (event.data.size) parts.push(event.data); };
  recorder.onstop = async () => {
    if (!upload || !buttonNode.isConnected) return;
    textOutput('#assist-message', 'Transcribing your fictional recording…');
    try { await useTranscript(new Blob(parts, { type: recorder.mimeType })); }
    catch (error) { textOutput('#assist-message', error.message, 'error'); }
  };
  recorderStop = stop;
  recorder.start();
  buttonNode.textContent = 'Stop and transcribe';
  textOutput('#assist-message', 'Recording for up to 30 seconds.');
}

function speakHelp() {
  if (!('speechSynthesis' in window)) throw new Error('Urdu playback is unavailable in this browser.');
  const voice = speechSynthesis.getVoices().find(item => item.lang.toLowerCase().startsWith('ur'));
  if (!voice) throw new Error('No Urdu speech voice is installed on this device.');
  const speech = new SpeechSynthesisUtterance('اپنے گھر کے افراد کی تعداد اور ماہانہ آمدنی درج کریں۔ درخواست بھیجنے سے پہلے معلومات چیک کریں۔');
  speech.voice = voice;
  speech.lang = voice.lang;
  speech.rate = .9;
  speechSynthesis.cancel();
  speechSynthesis.speak(speech);
}

async function qrReader() {
  if ('BarcodeDetector' in window && (await BarcodeDetector.getSupportedFormats()).includes('qr_code')) {
    const detector = new BarcodeDetector({ formats: ['qr_code'] });
    return async source => {
      const found = (await detector.detect(source).catch(() => [])).map(result => result.rawValue);
      return found.length ? found : [decodeVoucherImage(source)].filter(Boolean);
    };
  }
  return async source => [decodeVoucherImage(source)].filter(Boolean);
}

function placeToken(raw) {
  if (!/^mp_[A-Za-z0-9_-]{43}$/.test(raw)) return false;
  const form = $('#redeem-form');
  if (!form) return false;
  form.elements.token.value = raw;
  formMessage(form, 'Voucher QR read. Choose Redeem voucher to confirm.', 'success');
  form.elements.token.focus();
  return true;
}

async function startCamera() {
  if (cameraStop) { cameraStop(); return; }
  const detect = await qrReader();
  if (!navigator.mediaDevices) throw new Error('Camera access is unavailable. Use a QR image or paste the voucher token.');
  const video = $('#scan-video');
  const buttonNode = $('[data-action="camera"]');
  const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } } });
  if (!video?.isConnected) { stream.getTracks().forEach(track => track.stop()); return; }
  video.srcObject = stream;
  video.hidden = false;
  buttonNode.textContent = 'Close camera';
  let active = true;
  let timer;
  cameraStop = () => {
    active = false;
    clearTimeout(timer);
    stream.getTracks().forEach(track => track.stop());
    video.hidden = true;
    video.srcObject = null;
    buttonNode.textContent = 'Open camera scanner';
    cameraStop = null;
  };
  async function scan() {
    if (!active) return;
    let results = [];
    try { if (video.readyState >= 2) results = await detect(video); } catch { /* Wait for the next camera frame. */ }
    if (active && results.some(placeToken)) cameraStop();
    else if (active) timer = setTimeout(scan, 340);
  }
  try { await video.play(); } catch { cameraStop(); throw new Error('Camera access failed. Try a QR image or enter a token.'); }
  scan();
}

async function scanImage(file) {
  if (file.size > 8000000) throw new Error('Choose a QR image under 8 MB.');
  if (file.type === 'image/svg+xml' || file.name.toLowerCase().endsWith('.svg')) {
    if (!placeToken(decodeVoucherSvg(await file.text()))) throw new Error('No MadadPass voucher was found in that SVG file.');
    return;
  }
  const detect = await qrReader();
  const bitmap = await createImageBitmap(file);
  try {
    if (!(await detect(bitmap)).some(placeToken)) throw new Error('No MadadPass QR code was detected in that image.');
  } finally { bitmap.close(); }
}

async function handleClick(event) {
  const chip = event.target.closest('[data-action]');
  if (!chip) return;
  const { action, id, role, page: target, mode } = chip.dataset;
  if (action === 'register-view') { loginView(true); return; }
  if (action === 'login-view') { loginView(); return; }
  if (action === 'navigate') { await navigate(target, { mode }); return; }
  if (action === 'open-case') { await navigate('detail', id); return; }
  if (action === 'open-trace') { await navigate('trace', id); return; }
  chip.disabled = true;
  try {
    if (action === 'demo-login') await signIn(role, 'MadadDemo26!');
    else if (action === 'logout') {
      try { await api('/api/logout', { method: 'POST' }); } finally { clearSession(); }
    } else if (action === 'edit-case') await navigate('apply', { edit: entry });
    else if (action === 'extract') {
      const text = $('#statement').value.trim();
      if (!text) throw new Error('Describe your household or try the Urdu example first.');
      const result = await api('/api/ai/extract', { method: 'POST', body: { text, provider: !!$('#cloud-extract')?.checked } });
      if (!chip.isConnected) return;
      const form = $('#application-form');
      const populated = Object.entries(result.fields).filter(([, value]) => value !== null);
      populated.forEach(([key, value]) => { form.elements[key].value = value; });
      form.elements.confirmed.checked = false;
      await persistDraft();
      textOutput('#assist-message', `${result.notice ? `${result.notice} ` : ''}${populated.length ? 'Suggestions added. Check the fields before submitting.' : 'No clear household size or income was found. Enter the details you know.'}`);
    } else if (action === 'urdu-sample') {
      $('#statement').value = urduExample;
      await persistDraft();
      $('#statement').focus();
    } else if (action === 'speak-help') speakHelp();
    else if (action === 'dictate') dictate();
    else if (action === 'record') await record(chip);
    else if (action === 'assign' || action === 'reassess') {
      const caseId = entry.id;
      if (action === 'assign') await api(`/api/cases/${caseId}/assignment`, { method: 'PATCH', body: {} });
      else await api(`/api/ai/assess/${caseId}`, { method: 'POST' });
      toast(action === 'assign' ? 'Application assigned to you.' : 'Assessment refreshed using the current policy.');
      await navigate('detail', caseId);
    } else if (action === 'view-document') {
      const document = entry.documents.find(item => item.id === id);
      const file = await api(`/api/cases/${entry.id}/documents/${id}`, { binary: true });
      if (!chip.isConnected) return;
      const preview = $('#document-preview');
      const extracted = `<dl class="facts"><div><dt>Extracted monthly income</dt><dd>${pk(document.extraction.monthly_income)}</dd></div>
        <div><dt>Extracted household size</dt><dd>${e(document.extraction.family_members ?? 'Needs confirmation')}</dd></div></dl>`;
      let original;
      if (document.mime === 'text/plain') original = `<pre>${e((await file.text()).slice(0, 12000))}</pre>`;
      else {
        const url = URL.createObjectURL(file);
        loadedBlobs.push(url);
        original = `<img class="evidence-preview" src="${e(url)}" alt="Original fictional evidence">`;
      }
      preview.innerHTML = `<div class="preview-grid"><div><h3>Original document</h3>${original}</div><div><h3>Extracted fields</h3>${extracted}<small>${e(document.extraction.notice || 'Check the extracted fields against the original.')}</small></div></div>`;
    } else if (action === 'show-voucher') {
      const [result, blob] = await Promise.all([api(`/api/vouchers/${id}`), api(`/api/vouchers/${id}/qr`, { binary: true })]);
      if (!chip.isConnected) return;
      const qr = URL.createObjectURL(blob);
      loadedBlobs.push(qr);
      $('#voucher-code').innerHTML = `<div class="qr-code"><img src="${e(qr)}" width="205" height="205" alt="One-time grocery voucher QR code">
        <p>Let the merchant scan this QR code or enter the token.</p><code id="token-value" class="break-all">${e(result.token)}</code>
        <div class="button-row">${button('Copy token', 'copy-token', 'secondary')}<a class="button text" href="${e(qr)}" download="madadpass-voucher.svg">Download QR</a></div></div>`;
    } else if (action === 'copy-token') {
      await navigator.clipboard.writeText($('#token-value').textContent);
      toast('Voucher token copied.');
    } else if (action === 'revoke-consent') {
      await api(`/api/consents/${id}/revoke`, { method: 'POST' });
      toast('Sharing access withdrawn.');
      await navigate('consent');
    } else if (action === 'scam-sample') $('#check-message').elements.text.value = 'Act now! Pay the processing fee and send your OTP to receive guaranteed relief. http://bit.ly/demo-relief';
    else if (action === 'preview-policy') {
      const form = $('#policy-form');
      if (!form.reportValidity()) return;
      const result = await api('/api/admin/rules/preview', { method: 'POST', body: ruleFields(form) });
      if (!chip.isConnected) return;
      $('#preview-result').innerHTML = `<div class="notice"><strong>${result.changed} of ${result.reviewed} open ${result.reviewed === 1 ? 'case would' : 'cases would'} change assessment route.</strong>
        ${result.transitions.length ? `<ul class="details-list">${result.transitions.map(item => `<li>${e(nameOf(item.from))} → ${e(nameOf(item.to))} (${item.count})</li>`).join('')}</ul>` : ''}</div>`;
    } else if (action === 'revoke-key') {
      await api(`/api/admin/keys/${id}`, { method: 'DELETE' });
      toast('Mock API key revoked.');
      await navigate('keys');
    } else if (action === 'camera') await startCamera();
  } catch (error) {
    if (['extract', 'dictate', 'record'].includes(action)) textOutput('#assist-message', error.message, 'error');
    else if (action === 'camera' && $('#redeem-form')) formMessage($('#redeem-form'), error.message);
    else toast(error.message, true);
  } finally { chip.disabled = false; }
}

app.addEventListener('submit', handleSubmit);
app.addEventListener('click', handleClick);
app.addEventListener('input', event => {
  if (event.target.closest('#application-form') || event.target.id === 'statement') persistDraft();
  if (event.target.closest('#policy-form') && $('#preview-result')) $('#preview-result').innerHTML = '';
});
app.addEventListener('change', async event => {
  const element = event.target;
  if (element.id === 'family-select') await navigate('apply', { citizen: element.value });
  if (element.id === 'queue-filter') {
    const selected = element.value;
    const items = cases.filter(item => selected === 'all' || (selected === 'open' ? !['approved', 'rejected'].includes(item.status) : item.status === selected || item.assessment?.route === selected));
    $('#queue-rows').innerHTML = caseRows(sortQueue(items), true);
  }
  if (element.id === 'audio-file' && element.files.length) {
    try { await useTranscript(element.files[0]); } catch (error) { textOutput('#assist-message', error.message, 'error'); }
  }
  if (element.id === 'qr-file' && element.files.length) {
    try { await scanImage(element.files[0]); } catch (error) { if ($('#redeem-form')) formMessage($('#redeem-form'), error.message); }
  }
});
window.addEventListener('offline', updateConnection);
window.addEventListener('online', () => { updateConnection(); syncPending(); });
setInterval(() => { if (session) syncPending(); }, 20000);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

session = readAccount();
api('/health', { token: null }).then(result => { health = result; }).catch(() => {});
if (session && menus[session.user?.role]) {
  await navigate('home', null, { focus: false });
  syncPending();
} else loginView();
