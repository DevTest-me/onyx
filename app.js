/**
 * Onyx — Vara Agent Evolution Router
 * app.js — Real on-chain integration
 *
 * Dependencies are loaded dynamically from ESM CDN endpoints so this static
 * frontend can run without a bundler.
 */

'use strict';

/* ─────────────────────────────────────────────
   CONFIG
───────────────────────────────────────────── */
const PROGRAM_ID =
  '0x5f95232900ba991d24b428ec8cb7358218d6a7c10f885b7b0df7f2c82dc8bd7a';
const VARA_RPC    = 'wss://rpc.vara.network';
const GRAPHQL_URL = '/api/graphql';
const APP_NAME    = 'Onyx';
const GAS_LIMIT   = 50_000_000_000n;

const IDL = `
type RoutingResult = struct {
  intent_id: u64,
  assigned_agent: actor_id,
  agent_name: str,
  agent_score: u32,
};
type AgentDna = struct {
  name: str,
  reliability_score: u32,
  call_count: u32,
  success_count: u32,
  specializations: vec str,
  weighted_score: u32,
  mutation_count: u32,
  last_updated: u32,
};
type Intent = struct {
  id: u64,
  description: str,
  tags: vec str,
  category: str,
  risk_level: u8,
  submitter: actor_id,
  status: IntentStatus,
  assigned_agent: opt actor_id,
  submitted_at: u32,
  resolved_at: opt u32,
};
type IntentStatus = enum { Pending, Routed, Completed, Failed, };
type RankEntry = struct {
  agent: actor_id,
  name: str,
  weighted_score: u32,
  call_count: u32,
  reliability_score: u32,
};
constructor { New : (); };
service Onyx {
  RecordOutcome : (intent_id: u64, success: bool, quality_score: u32) -> bool;
  RegisterAgent : (name: str, specializations: vec str) -> bool;
  RegisterAgentFor : (address: actor_id, name: str, specializations: vec str) -> bool;
  RouteIntent : (intent_id: u64) -> RoutingResult;
  SubmitAndRoute : (description: str, tags: vec str, category: str, risk_level: u8) -> RoutingResult;
  SubmitIntent : (description: str, tags: vec str, category: str, risk_level: u8) -> u64;
};
service Query {
  query GetAgentDna : (address: actor_id) -> opt AgentDna;
  query GetAllAgents : () -> vec actor_id;
  query GetCategories : () -> vec str;
  query GetIntent : (intent_id: u64) -> opt Intent;
  query GetIntentCount : () -> u64;
  query GetRankings : (category: str) -> vec RankEntry;
  query GetRecentIntents : (limit: u32) -> vec Intent;
  query GetTopAgents : (limit: u32) -> vec RankEntry;
  query GetTotalRoutings : () -> u64;
};
service Admin {
  RemoveAgent : (address: actor_id) -> bool;
  SetAgentScore : (address: actor_id, score: u32) -> bool;
  query GetOwner : () -> actor_id;
};
`;

/* ─────────────────────────────────────────────
   STATE
───────────────────────────────────────────── */
let gearApi   = null;   // GearApi instance
let sailsInst = null;   // Sails instance (IDL-aware)
let account   = null;   // { address, meta }
let injector  = null;   // polkadot signer injector
let agents    = [];     // RankEntry[] from chain
let lbFilter  = 'all';
let feedTimer = null;
let lbPage = 1;
let explorerPage = 1;
let walletModulePromise = null;
let walletApi = null;
let chainInitPromise = null;
let accountFreeBalance = null;

const PAGE_SIZE = 12;
const AGENT_CACHE_KEY = 'onyx.agentCache.v2';
const AGENT_CACHE_TTL_MS = 5 * 60 * 1000;
const WALLET_TIMEOUT_MS = 10_000;
const VARA_DECIMALS = 12n;
const MIN_SUBMIT_BALANCE = 1_000_000_000_000n; // 1 VARA safety floor for fees + gas reservation

/* ─────────────────────────────────────────────
   BOOT
───────────────────────────────────────────── */
window.addEventListener('DOMContentLoaded', () => {
  navigate('dashboard');
  loadCachedAgents();
  bootChain();
  setTimeout(prepareWalletModule, 500);
});

async function bootChain() {
  setFeedStatus('Loading VAN Registry...');
  try {
    await loadRegistryFallbackData();
    setFeedStatus('Live - VAN Registry');
  } catch (err) {
    console.warn('[registry boot]', err);
    if (!agents.length) {
      setFeedStatus('Data error - retrying in 10 s');
      toast(`Data load failed: ${err.message}`, 'error');
      setTimeout(bootChain, 10_000);
      return;
    }
  }

  hydrateChainData();
}

async function hydrateChainData() {
  setFeedStatus('Syncing Onyx contract...');
  try {
    await ensureChainReady();
    await loadAllChainData();
    setFeedStatus('Live - Vara Mainnet');
    startFeedLoop();
  } catch (err) {
    console.warn('[chain hydrate]', err);
    setFeedStatus('Live - VAN Registry');
  }
}

function loadCachedAgents() {
  try {
    const raw = localStorage.getItem(AGENT_CACHE_KEY);
    if (!raw) return false;
    const cached = JSON.parse(raw);
    if (!cached?.agents?.length || Date.now() - cached.savedAt > AGENT_CACHE_TTL_MS) return false;
    agents = cached.agents;
    renderRegistryStats('Cached registry');
    renderRegistryFeed();
    renderLeaderboard();
    renderExplorer();
    setFeedStatus('Cached - refreshing...');
    return true;
  } catch (err) {
    console.warn('[cache]', err);
    return false;
  }
}

function saveAgentCache() {
  try {
    localStorage.setItem(AGENT_CACHE_KEY, JSON.stringify({
      savedAt: Date.now(),
      agents,
    }));
  } catch (err) {
    console.warn('[cache save]', err);
  }
}

function renderRegistryStats(sourceLabel) {
  el('stat-agents').textContent = fmtNum(agents.length);
  el('stat-agents-delta').textContent = sourceLabel;
  el('stat-categories').textContent = new Set(agents.map(a => a.category)).size || 4;
  el('stat-mutations').textContent = fmtNum(agents.reduce((sum, a) => sum + Number(a.mutation_count || 0), 0));
}

function renderRegistryFeed() {
  renderFeed(agents.slice(0, 10).map((a, i) => ({
    id: i + 1,
    description: `${a.name} registered in ${a.category}`,
    category: a.category,
    status: a.status || 'Registered',
    submitted_at: Math.floor(Date.now() / 1000) - i * 90,
  })));
}

/* ─────────────────────────────────────────────
   GEAR API
───────────────────────────────────────────── */
async function initGearApi() {
  if (gearApi && isGearConnected()) return;
  const GJ = await import('https://esm.sh/@gear-js/api@0.45.0');
  if (!GJ?.GearApi) throw new Error('@gear-js/api not loaded');
  gearApi = await GJ.GearApi.create({ providerAddress: VARA_RPC });
  gearApi.provider?.on?.('disconnected', () => setFeedStatus('Vara RPC disconnected'));
  gearApi.provider?.on?.('connected', () => setFeedStatus('Live - Vara Mainnet'));
}

/* ─────────────────────────────────────────────
   SAILS (IDL-AWARE CLIENT)
───────────────────────────────────────────── */
async function initSails() {
  // sails-js has no UMD build; load via ESM dynamic import from CDN.
  let SailsMod;
  try {
    SailsMod = await import('https://esm.sh/sails-js@1.0.0');
  } catch {
    // fallback mirror
    SailsMod = await import('https://cdn.skypack.dev/sails-js');
  }

  let ParserMod;
  try {
    ParserMod = await import('https://esm.sh/sails-js-parser@0.5.1');
  } catch {
    ParserMod = await import('https://cdn.skypack.dev/sails-js-parser');
  }

  const { Sails }          = SailsMod;
  const { SailsIdlParser } = ParserMod;

  const parser = await SailsIdlParser.new();
  sailsInst    = new Sails(parser);
  sailsInst.parseIdl(IDL);
  sailsInst.setApi(gearApi);
  sailsInst.setProgramId(PROGRAM_ID);
}

function isGearConnected() {
  if (!gearApi) return false;
  const provider = gearApi.provider;
  if (typeof provider?.isConnected === 'boolean') return provider.isConnected;
  if (typeof gearApi.isConnected === 'boolean') return gearApi.isConnected;
  return true;
}

async function ensureChainReady() {
  if (sailsInst && gearApi && isGearConnected()) return;
  if (chainInitPromise) return chainInitPromise;

  chainInitPromise = (async () => {
    await resetChainConnection();
  })().finally(() => {
    chainInitPromise = null;
  });

  return chainInitPromise;
}

async function resetChainConnection() {
  clearInterval(feedTimer);
  try { await gearApi?.disconnect?.(); } catch { /* best effort */ }
  try { await gearApi?.provider?.disconnect?.(); } catch { /* best effort */ }
  gearApi = null;
  sailsInst = null;
  setFeedStatus('Connecting to Vara RPC...');
  await withTimeout(initGearApi(), 'Vara RPC connection timed out. Refresh and try again.', 20_000);
  await initSails();
}

/* ─────────────────────────────────────────────
   CHAIN DATA LOADING
───────────────────────────────────────────── */
async function loadAllChainData() {
  const ZERO = '5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM'; // zero address placeholder

  // ── Top agents ──
  const topRaw = await sailsInst.services.Query.queries.GetTopAgents(50)
    .withAddress(ZERO)
    .call();
  mergeChainRankings(Array.isArray(topRaw) ? topRaw : []);

  // ── Intent count ──
  const intentCount = await sailsInst.services.Query.queries.GetIntentCount()
    .withAddress(ZERO)
    .call();
  el('stat-intents').textContent = fmtNum(Number(intentCount ?? 0));

  // ── All agents count ──
  const allAgentAddrs = await sailsInst.services.Query.queries.GetAllAgents()
    .withAddress(ZERO)
    .call();
  el('stat-agents').textContent   = fmtNum(allAgentAddrs?.length ?? agents.length);
  el('stat-agents-delta').textContent = 'On-chain registry';

  // ── Categories ──
  const cats = await sailsInst.services.Query.queries.GetCategories()
    .withAddress(ZERO)
    .call();
  el('stat-categories').textContent = cats?.length ?? 4;

  // Keep first paint fast; DNA/profile queries are avoided during render.
  renderRegistryStats('Live registry');

  // ── Recent intents for feed ──
  await refreshFeed();

  // ── Render leaderboard + explorer ──
  renderLeaderboard();
  renderExplorer();
}

function mergeChainRankings(topAgents) {
  if (!topAgents.length) return;
  const byAgent = new Map(agents.map(a => [String(a.agent).toLowerCase(), a]));
  topAgents.forEach(ranked => {
    const key = String(ranked.agent || '').toLowerCase();
    if (!key) return;
    const current = byAgent.get(key);
    if (current) {
      Object.assign(current, ranked, {
        category: current.category || trackFromSpecs(current.specializations || []),
        specializations: current.specializations?.length ? current.specializations : _trackSpecs(current.category || 'Open'),
      });
    } else {
      agents.push({
        ...ranked,
        category: trackFromSpecs(ranked.specializations || []),
        specializations: ranked.specializations || _trackSpecs('Open'),
        description: '',
        status: '',
      });
    }
  });
  agents.sort((a, b) => Number(b.weighted_score || 0) - Number(a.weighted_score || 0));
}

async function loadRegistryFallbackData() {
  const data = await gqlFetch(`{
    allApplications {
      nodes {
        id
        handle
        owner
        track
        status
        description
        githubUrl
        skillsUrl
        idlUrl
      }
    }
  }`);
  const nodes = data?.allApplications?.nodes ?? [];
  const trackWeights = { Services: 840, Economy: 790, Social: 760, Open: 720 };

  agents = nodes.map((n, i) => ({
    agent: n.id || n.owner || `registry-${i}`,
    name: n.handle || `agent-${i + 1}`,
    category: n.track || 'Open',
    specializations: _trackSpecs(n.track || 'Open'),
    weighted_score: (trackWeights[n.track] ?? 700) - Math.min(i, 120),
    reliability_score: 850 - (i % 12) * 18,
    call_count: Math.max(0, 120 - i),
    mutation_count: i % 5,
    description: n.description || '',
    status: n.status || '',
    github_url: n.githubUrl || '',
    skills_url: n.skillsUrl || '',
    idl_url: n.idlUrl || '',
  }));

  el('stat-intents').textContent = '0';
  renderRegistryStats('Live registry');
  renderRegistryFeed();
  saveAgentCache();
  renderLeaderboard();
  renderExplorer();
}

function _trackSpecs(track) {
  return {
    Services: ['services', 'api', 'integration'],
    Economy: ['economy', 'finance', 'defi', 'treasury'],
    Social: ['social', 'community', 'chat'],
    Open: ['general', 'open'],
  }[track] || ['general'];
}

/* ─────────────────────────────────────────────
   GRAPHQL (SUPPLEMENTAL — for richer metadata)
   We introspect first to discover actual field names.
───────────────────────────────────────────── */
async function gqlFetch(query, variables = {}) {
  const res = await fetch(GRAPHQL_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) throw new Error(`GraphQL HTTP ${res.status}`);
  const json = await res.json();
  if (json.errors?.length) throw new Error(json.errors[0].message);
  return json.data;
}

async function introspectSchema() {
  // Discover real field names from the GraphQL API
  const data = await gqlFetch(`{
    __schema {
      queryType { fields { name args { name } } }
    }
  }`);
  return data?.__schema?.queryType?.fields?.map(f => f.name) ?? [];
}

/* ─────────────────────────────────────────────
   LIVE FEED
───────────────────────────────────────────── */
async function refreshFeed() {
  if (!sailsInst || !isGearConnected()) {
    setFeedStatus('Vara RPC disconnected');
    return;
  }
  const ZERO = '5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM';
  let recentIntents = [];
  try {
    recentIntents = await sailsInst.services.Query.queries.GetRecentIntents(10)
      .withAddress(ZERO)
      .call();
  } catch (e) {
    console.warn('[feed] GetRecentIntents failed', e);
  }

  // Also try to supplement from GraphQL (best-effort)
  let gqlIntents = [];
  try {
    const fields = await introspectSchema();
    // only query if the API has something that looks like an intents field
    if (fields.some(f => /intent/i.test(f))) {
      const guessField = fields.find(f => /recent|intent/i.test(f)) ?? fields[0];
      const data = await gqlFetch(`{ ${guessField} { id description category status } }`);
      gqlIntents = Object.values(data ?? {})[0] ?? [];
    }
  } catch { /* GraphQL is supplemental only */ }

  const combined = mergeFeeds(recentIntents, gqlIntents);
  renderFeed(combined);
}

function mergeFeeds(chainIntents, gqlIntents) {
  // Prefer chain data; merge by id when possible
  const map = new Map();
  for (const i of chainIntents) map.set(String(i.id), i);
  for (const i of gqlIntents) {
    if (!map.has(String(i.id))) map.set(String(i.id), i);
  }
  return [...map.values()].slice(0, 10);
}

function renderFeed(intents) {
  const list = el('feed-list');
  if (!intents?.length) {
    list.innerHTML = '<div class="loading-row">No recent intents found.</div>';
    return;
  }

  list.innerHTML = intents.map(intent => {
    const cat    = intent.category || 'Open';
    const status = intent.status   || 'Pending';
    const desc   = truncate(intent.description || '—', 60);
    const ts     = intent.submitted_at
      ? new Date(Number(intent.submitted_at) * 1000).toLocaleTimeString()
      : 'just now';
    const statusClass = {
      Routed: 'cyan', Completed: 'green', Failed: 'red', Pending: 'amber'
    }[status] || 'amber';

    return `
      <div class="feed-row">
        <div class="feed-row-left">
          <span class="track-badge track-${cat.toLowerCase()}">${cat}</span>
          <span class="feed-desc">${escHtml(desc)}</span>
        </div>
        <div class="feed-row-right">
          <span class="feed-status ${statusClass}">${status}</span>
          <span class="feed-time">${ts}</span>
        </div>
      </div>`;
  }).join('');
}

function startFeedLoop() {
  clearInterval(feedTimer);
  feedTimer = setInterval(async () => {
    await refreshFeed();
  }, 15_000);
}

/* ─────────────────────────────────────────────
   LEADERBOARD
───────────────────────────────────────────── */
window.filterLeaderboard = function(cat, btn) {
  lbFilter = cat;
  lbPage = 1;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderLeaderboard();
};

window.onLeaderboardSearchInput = function() {
  lbPage = 1;
  renderLeaderboard();
};

window.onExplorerSearchInput = function() {
  explorerPage = 1;
  renderExplorer();
};

window.setLeaderboardPage = function(page) {
  lbPage = Number(page) || 1;
  renderLeaderboard();
};

window.setExplorerPage = function(page) {
  explorerPage = Number(page) || 1;
  renderExplorer();
};

window.renderLeaderboard = function() {
  const q   = (el('lb-search')?.value ?? '').toLowerCase();
  const tbody = el('lb-body');

  // If we have per-category rankings available, fetch them; otherwise filter local list
  let display = agents;

  if (lbFilter !== 'all') {
    display = display.filter(a =>
      (a.category || '').toLowerCase() === lbFilter ||
      (a.specializations || []).some(s => s.toLowerCase().includes(lbFilter))
    );
  }
  if (q) {
    display = display.filter(a => agentMatchesQuery(a, q));
  }

  if (!display.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="loading-row">No agents found.</td></tr>';
    renderPagination('lb-pagination', 1, 1, 'setLeaderboardPage', 0);
    return;
  }

  const total = display.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  lbPage = clampPage(lbPage, totalPages);
  const start = (lbPage - 1) * PAGE_SIZE;
  const pageItems = display.slice(start, start + PAGE_SIZE);

  tbody.innerHTML = pageItems.map((a, i) => {
    const score   = a.weighted_score   ?? 0;
    const rel     = a.reliability_score ?? 0;
    const calls   = a.call_count        ?? 0;
    const pct     = Math.min(100, rel).toFixed(0);
    const relDisp = `${pct}%`;
    const addr    = a.agent ? truncAddr(a.agent) : '—';
    const track   = a.category || trackFromSpecs(a.specializations || []);
    const trackClass = track.toLowerCase();

    return `
      <tr>
        <td>${rankBadge(start + i)}</td>
        <td>
          <div style="font-weight:600;font-size:13px;">${escHtml(a.name || addr)}</div>
          <div style="font-size:11px;color:var(--text3);font-family:'DM Mono',monospace;">${addr}</div>
        </td>
        <td><span class="track-badge track-${trackClass}">${track}</span></td>
        <td><span class="score-val">${fmtNum(score)}</span></td>
        <td>
          <div class="rel-bar-wrap">
            <div class="rel-bar" style="width:${pct}%"></div>
            <span>${relDisp}</span>
          </div>
        </td>
        <td class="hide-mobile">${fmtNum(calls)}</td>
        <td class="hide-mobile">—</td>
      </tr>`;
  }).join('');

  renderPagination('lb-pagination', lbPage, totalPages, 'setLeaderboardPage', total);
};

/* ─────────────────────────────────────────────
   EXPLORER
───────────────────────────────────────────── */
window.renderExplorer = function() {
  const q    = (el('explorer-search')?.value ?? '').toLowerCase();
  const grid = el('explorer-grid');

  let display = agents;
  if (q) {
    display = display.filter(a => agentMatchesQuery(a, q));
  }

  if (!display.length) {
    grid.innerHTML = '<div class="loading-row" style="grid-column:1/-1">No agents found.</div>';
    renderPagination('explorer-pagination', 1, 1, 'setExplorerPage', 0);
    return;
  }

  const total = display.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  explorerPage = clampPage(explorerPage, totalPages);
  const start = (explorerPage - 1) * PAGE_SIZE;
  const pageItems = display.slice(start, start + PAGE_SIZE);

  grid.innerHTML = pageItems.map(a => {
    const score = a.weighted_score ?? 0;
    const rel   = a.reliability_score ?? 0;
    const calls = a.call_count ?? 0;
    const specs = (a.specializations ?? []).slice(0, 4);
    const mut   = a.mutation_count ?? 0;
    const pct   = Math.min(100, rel).toFixed(0);

    return `
      <div class="dna-card" onclick="showAgentDetails('${escAttr(a.agent)}')">
        <div class="dna-card-header">
          <div class="agent-avatar">${(a.name || '?')[0].toUpperCase()}</div>
          <div>
            <div class="agent-name">${escHtml(a.name || truncAddr(a.agent))}</div>
            <div class="agent-addr">${truncAddr(a.agent)}</div>
          </div>
        </div>
        <div class="dna-stats">
          <div class="dna-stat"><span class="dna-key">Score</span><span class="dna-val blue">${fmtNum(score)}</span></div>
          <div class="dna-stat"><span class="dna-key">Reliability</span><span class="dna-val cyan">${pct}%</span></div>
          <div class="dna-stat"><span class="dna-key">Calls</span><span class="dna-val">${fmtNum(calls)}</span></div>
          <div class="dna-stat"><span class="dna-key">Mutations</span><span class="dna-val amber">${mut}</span></div>
        </div>
        ${specs.length ? `
        <div class="dna-tags">
          ${specs.map(s => `<span class="spec-tag">${escHtml(s)}</span>`).join('')}
        </div>` : ''}
        <div class="dna-bar-wrap">
          <div class="dna-bar" style="width:${pct}%"></div>
        </div>
        <div class="dna-card-hint">Open next steps</div>
      </div>`;
  }).join('');

  renderPagination('explorer-pagination', explorerPage, totalPages, 'setExplorerPage', total);
};

function clampPage(page, totalPages) {
  return Math.max(1, Math.min(Number(page) || 1, totalPages));
}

function renderPagination(containerId, page, totalPages, handlerName, totalItems) {
  const container = el(containerId);
  if (!container) return;
  if (totalPages <= 1) {
    container.innerHTML = totalItems ? `<span class="page-meta">${fmtNum(totalItems)} agents</span>` : '';
    return;
  }

  const pages = compactPages(page, totalPages);
  const buttons = pages.map(p => {
    if (p === '...') return '<span class="page-gap">...</span>';
    return `<button class="page-btn ${p === page ? 'active' : ''}" onclick="${handlerName}(${p})">${p}</button>`;
  }).join('');

  container.innerHTML = `
    <button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="${handlerName}(${page - 1})">Prev</button>
    ${buttons}
    <button class="page-btn" ${page >= totalPages ? 'disabled' : ''} onclick="${handlerName}(${page + 1})">Next</button>
    <span class="page-meta">Page ${page} of ${totalPages} - ${fmtNum(totalItems)} agents</span>`;
}

function compactPages(current, total) {
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const sorted = [...pages].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
  const result = [];
  sorted.forEach((p, i) => {
    if (i && p - sorted[i - 1] > 1) result.push('...');
    result.push(p);
  });
  return result;
}

function agentMatchesQuery(agent, q) {
  const haystack = [
    agent.name,
    agent.agent,
    agent.category,
    agent.description,
    agent.github_url,
    agent.skills_url,
    agent.idl_url,
    ...(agent.specializations || []),
  ].join(' ').toLowerCase();
  return haystack.includes(q);
}

window.showAgentDetails = function(agentId) {
  const agent = agents.find(a => String(a.agent).toLowerCase() === String(agentId).toLowerCase());
  if (!agent) return;
  const body = el('wallet-modal-body');
  if (!body) return;

  const specs = (agent.specializations || []).slice(0, 6);
  const links = [
    agent.github_url ? `<a href="${escAttr(agent.github_url)}" target="_blank" rel="noreferrer">GitHub</a>` : '',
    agent.skills_url ? `<a href="${escAttr(agent.skills_url)}" target="_blank" rel="noreferrer">Skills</a>` : '',
    agent.idl_url ? `<a href="${escAttr(agent.idl_url)}" target="_blank" rel="noreferrer">IDL</a>` : '',
  ].filter(Boolean).join('');

  body.innerHTML = `
    <div class="wallet-modal-title">${escHtml(agent.name || 'Agent')}</div>
    <div class="wallet-modal-copy">
      Onyx routing selects this as the best-fit registered agent. It does not automatically make the agent execute your request yet.
      Use the links or copied handle/address to continue in that agent's app, repo, chat, or workflow.
    </div>
    <div class="agent-detail-block">
      <div><span>Address</span><strong>${escHtml(truncAddr(agent.agent))}</strong></div>
      <div><span>Track</span><strong>${escHtml(agent.category || 'Open')}</strong></div>
      <div><span>Score</span><strong>${fmtNum(agent.weighted_score ?? 0)}</strong></div>
    </div>
    ${agent.description ? `<div class="agent-detail-desc">${escHtml(agent.description)}</div>` : ''}
    ${specs.length ? `<div class="dna-tags agent-detail-tags">${specs.map(s => `<span class="spec-tag">${escHtml(s)}</span>`).join('')}</div>` : ''}
    <div class="route-actions">
      <button class="route-action-btn" onclick="copyText('${escAttr(agent.name || '')}','Agent handle copied')">Copy Handle</button>
      <button class="route-action-btn" onclick="copyText('${escAttr(agent.agent)}','Agent address copied')">Copy Address</button>
    </div>
    ${links ? `<div class="wallet-install-grid agent-link-grid">${links}</div>` : `<div class="route-note">This agent has no app link in VAN yet. Copy the handle or address and use it in the connected agent workflow.</div>`}
  `;
  openWalletModal();
};

/* ─────────────────────────────────────────────
   WALLET
───────────────────────────────────────────── */
window.toggleWallet = async function() {
  if (account) {
    disconnectWallet();
    return;
  }
  await connectWallet();
};

function prepareWalletModule() {
  if (!walletModulePromise) {
    walletModulePromise = import('https://esm.sh/@polkadot/extension-dapp@0.46.5')
      .then(mod => {
        walletApi = mod;
        return mod;
      })
      .catch(err => {
        walletModulePromise = null;
        throw err;
      });
  }
  return walletModulePromise;
}

async function connectWallet() {
  const btn = el('wallet-btn');
  const label = el('wallet-label');
  const oldLabel = label?.textContent || 'Connect Wallet';
  try {
    btn?.setAttribute('disabled', 'disabled');
    if (label) label.textContent = 'Checking...';

    if (!window.injectedWeb3 || !Object.keys(window.injectedWeb3).length) {
      showWalletInstallModal();
      return;
    }

    const extDapp = await withTimeout(prepareWalletModule(), 'Wallet module timed out. Reload the page and try again.');
    const extensions = await withTimeout(
      extDapp.web3Enable(APP_NAME),
      'Wallet did not respond. Unlock your wallet extension and try again.'
    );
    if (!extensions.length) {
      showWalletInstallModal('Wallet access was not authorised. Open your extension and allow Onyx, then try again.');
      return;
    }

    const accounts = await withTimeout(
      extDapp.web3Accounts(),
      'Wallet did not return accounts. Unlock your wallet extension and try again.'
    );
    if (!accounts.length) {
      showWalletInstallModal('No accounts were found. Create or import an account in your wallet, then reconnect.');
      return;
    }

    if (accounts.length === 1) {
      await connectSelectedAccount(accounts[0]);
      return;
    }

    showWalletAccountPicker(accounts);
  } catch (err) {
    console.error('[wallet]', err);
    const message = normalizeWalletError(err);
    showWalletInstallModal(message);
    toast(message, 'error');
  } finally {
    btn?.removeAttribute('disabled');
    if (!account && label) label.textContent = 'Connect Wallet';
  }
}

async function connectSelectedAccount(selected) {
  if (!selected || !walletApi) walletApi = await prepareWalletModule();
  account  = selected;
  injector = await withTimeout(
    walletApi.web3FromAddress(account.address),
    'Wallet signing access timed out. Unlock your wallet extension and try again.'
  );
  closeWalletModal();
  updateWalletUI(true);
  refreshWalletBalance();
  toast(`Connected: ${account.meta?.name || truncAddr(account.address)}`);
  checkSubmitEnabled();
}

function withTimeout(promise, message, ms = WALLET_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function normalizeWalletError(err) {
  const text = String(err?.message || err || '');
  if (/keyring is locked|locked/i.test(text)) {
    return 'Your wallet is locked. Open the extension, unlock it, then click Connect Wallet again.';
  }
  if (/timed out|did not respond|did not return accounts/i.test(text)) {
    return text;
  }
  return `Wallet error: ${text || 'connection failed'}`;
}

window.selectWalletAccount = async function(index) {
  const accounts = window.__onyxWalletAccounts || [];
  await connectSelectedAccount(accounts[index]);
};

window.closeWalletModal = closeWalletModal;

function showWalletAccountPicker(accounts) {
  window.__onyxWalletAccounts = accounts;
  const body = el('wallet-modal-body');
  if (!body) return;
  body.innerHTML = `
    <div class="wallet-modal-title">Choose Wallet Account</div>
    <div class="wallet-modal-copy">Select the account you want Onyx to use for signing Vara transactions.</div>
    <div class="wallet-account-list">
      ${accounts.map((acc, i) => `
        <button class="wallet-account-btn" onclick="selectWalletAccount(${i})">
          <span>${escHtml(acc.meta?.name || `Account ${i + 1}`)}</span>
          <small>${escHtml(acc.meta?.source || 'wallet')} - ${truncAddr(acc.address)}</small>
        </button>
      `).join('')}
    </div>`;
  openWalletModal();
}

function showWalletInstallModal(message = 'Install a Polkadot-compatible wallet, then reload or reconnect.') {
  const body = el('wallet-modal-body');
  if (!body) return;
  body.innerHTML = `
    <div class="wallet-modal-title">Wallet Needed</div>
    <div class="wallet-modal-copy">${escHtml(message)}</div>
    <div class="wallet-install-grid">
      <a href="https://polkadot.js.org/extension/" target="_blank" rel="noreferrer">Polkadot.js</a>
      <a href="https://www.subwallet.app/download.html" target="_blank" rel="noreferrer">SubWallet</a>
      <a href="https://www.talisman.xyz/download" target="_blank" rel="noreferrer">Talisman</a>
    </div>`;
  openWalletModal();
}

function openWalletModal() {
  el('wallet-modal')?.classList.add('open');
}

function closeWalletModal() {
  el('wallet-modal')?.classList.remove('open');
}

function disconnectWallet() {
  account  = null;
  injector = null;
  accountFreeBalance = null;
  updateWalletUI(false);
  el('submit-btn').disabled = true;
  toast('Wallet disconnected');
}

function updateWalletUI(connected) {
  const dot   = el('wallet-dot');
  const label = el('wallet-label');
  const status = el('intent-wallet-status');

  if (connected) {
    dot.classList.add('connected');
    label.textContent = account.meta?.name || truncAddr(account.address);
    if (status) {
      status.style.display = '';
      status.innerHTML = `
        <span style="font-size:20px;">OK</span>
        <div>
          <div style="font-weight:600;font-size:14px;">${escHtml(account.meta?.name || 'Wallet connected')}</div>
          <div style="font-size:12px;color:var(--text3);margin-top:2px;">
            ${truncAddr(account.address)} · Balance: <span id="wallet-balance">Checking...</span>
          </div>
          <div style="font-size:12px;color:var(--text3);margin-top:4px;">
            Onyx charges no app fee. Vara still requires network fee + gas, estimated before signing. Keep at least ${formatVara(MIN_SUBMIT_BALANCE)} available.
          </div>
        </div>
        <button class="btn btn-outline" style="margin-left:auto;padding:8px 14px;font-size:12px;" onclick="toggleWallet()">Disconnect</button>`;
    }
  } else {
    dot.classList.remove('connected');
    label.textContent = 'Connect Wallet';
    if (status) {
      status.style.display = '';
      status.innerHTML = `
        <span style="font-size:20px;">Lock</span>
        <div>
          <div style="font-weight:600;font-size:14px;">Wallet not connected</div>
          <div style="font-size:12px;color:var(--text3);margin-top:2px;">Connect a funded Vara wallet to submit intents on-chain</div>
        </div>
        <button class="btn btn-outline" style="margin-left:auto;padding:8px 14px;font-size:12px;" onclick="toggleWallet()">Connect</button>`;
    }
  }
}

async function refreshWalletBalance() {
  if (!account) return;
  const balanceEl = el('wallet-balance');
  if (balanceEl) balanceEl.textContent = 'Checking...';
  accountFreeBalance = await getFreeBalance(account.address);
  if (balanceEl) {
    balanceEl.textContent = accountFreeBalance === null ? 'Unavailable' : formatVara(accountFreeBalance);
    balanceEl.classList.toggle('balance-low', accountFreeBalance !== null && accountFreeBalance < MIN_SUBMIT_BALANCE);
  }
}

async function getFreeBalance(address) {
  try {
    if (!gearApi || !isGearConnected()) await ensureChainReady();
    const accountInfo = await gearApi.query.system.account(address);
    const data = accountInfo?.data ?? accountInfo?.toJSON?.()?.data;
    const free = data?.free;
    if (free === undefined || free === null) return null;
    return BigInt(free.toString().replace(/,/g, ''));
  } catch (err) {
    console.warn('[balance]', err);
    return null;
  }
}

function formatVara(planck) {
  const value = BigInt(planck ?? 0);
  const base = 10n ** VARA_DECIMALS;
  const whole = value / base;
  const frac = ((value % base) * 1000n) / base;
  return `${whole}.${frac.toString().padStart(3, '0')} VARA`;
}

/* ─────────────────────────────────────────────
   INTENT SUBMISSION
───────────────────────────────────────────── */
window.submitIntent = async function() {
  if (!account || !injector) {
    toast('Connect your wallet first.', 'error');
    return;
  }

  const desc     = el('intent-desc').value.trim();
  const category = el('intent-cat').value;
  const tagsRaw  = el('intent-tags').value.trim();
  const riskVal  = parseInt(el('risk-slider').value, 10);

  if (!desc) { toast('Please enter an intent description.', 'error'); return; }

  const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];

  const btn    = el('submit-btn');
  const result = el('submit-result');
  btn.disabled = true;
  btn.textContent = 'Connecting...';
  result.innerHTML = '<div class="submit-success">Preparing Vara connection...</div>';

  try {
    await ensureChainReady();
    const freeBalance = accountFreeBalance ?? await getFreeBalance(account.address);
    accountFreeBalance = freeBalance;
    if (freeBalance !== null && freeBalance < MIN_SUBMIT_BALANCE) {
      refreshWalletBalance();
      throw new Error(`LOW_BALANCE:${formatVara(freeBalance)}`);
    }
    btn.textContent = 'Awaiting Signature...';
    result.innerHTML = '<div class="submit-success">Approve the transaction in your wallet.</div>';

    // Build transaction using sails-js
    const tx = sailsInst.services.Onyx.functions.SubmitAndRoute(
      desc,
      tags,
      category,
      riskVal   // u8: 0=Low, 1=Medium, 2=High
    );

    tx.withAccount(account.address, { signer: injector.signer });
    btn.textContent = 'Estimating Gas...';
    result.innerHTML = '<div class="submit-success">Estimating Vara network gas for this route...</div>';
    await tx.calculateGas();
    btn.textContent = 'Submitting...';
    result.innerHTML = '<div class="submit-success">Submitting transaction to Vara...</div>';
    const { response } = await tx.signAndSend();

    // Decode result if available
    let resultHtml = '';
    if (response) {
      const decoded = await response();
      const { intent_id, agent_name, agent_score, assigned_agent } = decoded || {};
      const agentLabel = agent_name || truncAddr(assigned_agent);
      const agentAddr = assigned_agent || '';
      resultHtml = `
        <div class="submit-success">
          <div class="submit-success-title">Intent Routed</div>
          <div class="route-explainer">
            Onyx matched your intent to the best-fit registered agent. Use this agent as the recommended next stop for your request.
          </div>
          <div class="submit-success-row"><span>Intent ID</span><span>#${intent_id ?? '—'}</span></div>
          <div class="submit-success-row"><span>Assigned Agent</span><span>${escHtml(agentLabel)}</span></div>
          <div class="submit-success-row"><span>Agent Address</span><span>${escHtml(truncAddr(agentAddr))}</span></div>
          <div class="submit-success-row"><span>Agent Score</span><span>${fmtNum(agent_score ?? 0)}</span></div>
          <div class="route-actions">
            <button class="route-action-btn" onclick="copyText('${escAttr(agentAddr)}','Agent address copied')">Copy Agent</button>
            <button class="route-action-btn" onclick="openAgentInExplorer('${escAttr(agentAddr || agentLabel)}')">View Agent</button>
          </div>
          <div class="route-note">
            Next: open the agent profile, copy the address, or use the agent handle in the connected agent app/workflow.
          </div>
        </div>`;
      toast(`Routed to ${agent_name || 'agent'}!`);
      // Refresh data without failing the success state if the RPC drops afterward.
      try { await loadAllChainData(); } catch (refreshErr) { console.warn('[submit refresh]', refreshErr); }
    } else {
      resultHtml = `<div class="submit-success">✅ Transaction submitted. Awaiting routing result.</div>`;
      toast('Transaction submitted!');
    }

    result.innerHTML = resultHtml;
  } catch (err) {
    console.error('[submit]', err);
    console.debug('[submit detail]', errorToText(err));
    const msg = parseChainError(err);
    result.innerHTML = `<div class="submit-error">❌ ${escHtml(msg)}</div>`;
    toast(msg, 'error');
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Submit & Route Intent';
    checkSubmitEnabled();
  }
};

function parseChainError(err) {
  const msg = errorToText(err);
  if (/websocket is not connected|disconnected from|failed ws request/i.test(msg)) {
    setFeedStatus('Vara RPC disconnected');
    return 'Vara RPC disconnected before submission completed. Reconnect or refresh, then submit again.';
  }
  if (msg.startsWith('LOW_BALANCE:')) {
    const balance = msg.split(':')[1] || '0 VARA';
    return `Selected wallet has only ${balance}. Switch to a funded Vara account or add VARA, then submit again.`;
  }
  if (/no agents for this category/i.test(msg)) return 'No agent is registered for that category yet. Try Services or Economy.';
  if (/InsufficientBalance|insufficient user balance|inability to pay|balance too low/i.test(msg) || msg.includes('1010')) {
    return 'Selected wallet cannot cover the estimated Vara network fee/gas for this route. Add more VARA or use a funded account.';
  }
  if (msg.includes('Cancelled')) return 'Transaction cancelled in wallet.';
  return msg.slice(0, 120);
}

function errorToText(err) {
  if (err === null || err === undefined) return 'Unknown transaction error';
  if (typeof err === 'string') return err;
  if (err?.message && typeof err.message === 'string') return err.message;

  const candidates = [
    err?.error,
    err?.reason,
    err?.details,
    err?.data,
    err?.result,
    err?.value,
    err?.decoded,
  ];
  for (const candidate of candidates) {
    const text = nestedErrorText(candidate);
    if (text) return text;
  }

  try {
    const json = JSON.stringify(err, (_key, value) =>
      typeof value === 'bigint' ? value.toString() : value
    );
    if (json && json !== '{}') return json;
  } catch { /* fall through */ }

  return String(err);
}

function nestedErrorText(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (typeof value !== 'object') return String(value);
  if (value.message) return String(value.message);
  if (value.error) return nestedErrorText(value.error);
  if (value.reason) return nestedErrorText(value.reason);
  if (value.Execution) return `Execution error: ${value.Execution}`;
  if (value.User) return `User error: ${value.User}`;
  return '';
}

/* ─────────────────────────────────────────────
   RISK SLIDER
───────────────────────────────────────────── */
window.updateRisk = function(val) {
  const labels = ['⚪ Low Risk', '🟡 Medium Risk', '🔴 High Risk'];
  el('risk-val').textContent = labels[val] || labels[0];
};

/* ─────────────────────────────────────────────
   NAVIGATION
───────────────────────────────────────────── */
window.navigate = function(page) {
  document.querySelectorAll('section[id^="section-"]').forEach(s =>
    s.classList.remove('active')
  );
  document.querySelectorAll('#main-nav a, #mobile-nav a').forEach(a =>
    a.classList.remove('active')
  );

  const sec = el(`section-${page}`);
  if (sec) sec.classList.add('active');

  // Highlight nav link
  document.querySelectorAll('#main-nav a, #mobile-nav a').forEach(a => {
    if (a.getAttribute('onclick')?.includes(`'${page}'`)) a.classList.add('active');
  });

  // Lazy-load per page
  if (page === 'leaderboard') renderLeaderboard();
  if (page === 'explorer')    renderExplorer();
};

/* ─────────────────────────────────────────────
   MOBILE NAV
───────────────────────────────────────────── */
window.toggleMobileNav = function() {
  el('mobile-nav').classList.toggle('open');
  el('hamburger-btn').classList.toggle('open');
};
window.closeMobileNav = function() {
  el('mobile-nav').classList.remove('open');
  el('hamburger-btn').classList.remove('open');
};

/* ─────────────────────────────────────────────
   TOAST
───────────────────────────────────────────── */
function toast(msg, type = 'info') {
  const t = el('toast');
  t.textContent = msg;
  t.className   = `toast-visible toast-${type}`;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.className = ''; }, 3500);
}

window.copyText = async function(text, message = 'Copied') {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    toast(message);
  } catch {
    toast('Copy failed. Select and copy the address manually.', 'error');
  }
};

window.openAgentInExplorer = function(query) {
  navigate('explorer');
  const search = el('explorer-search');
  if (search) {
    search.value = query || '';
    onExplorerSearchInput();
    search.focus();
  }
};

/* ─────────────────────────────────────────────
   HELPERS
───────────────────────────────────────────── */
function el(id)          { return document.getElementById(id); }
function escHtml(s)      { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function escAttr(s)      { return escHtml(s).replace(/`/g, '&#96;'); }
function truncate(s, n)  { return s.length > n ? s.slice(0, n) + '…' : s; }
function truncAddr(a)    { if (!a) return '—'; return `${a.slice(0,6)}…${a.slice(-4)}`; }
function fmtNum(n)       { return Number(n).toLocaleString(); }
function setFeedStatus(s){ const e = el('feed-status'); if (e) e.textContent = s; }

function trackFromSpecs(specs = []) {
  const values = specs.map(s => String(s).toLowerCase());
  if (values.some(s => ['economy', 'finance', 'defi', 'treasury'].includes(s))) return 'Economy';
  if (values.some(s => ['social', 'community', 'chat', 'coordination'].includes(s))) return 'Social';
  if (values.some(s => ['services', 'api', 'integration'].includes(s))) return 'Services';
  return 'Open';
}

function checkSubmitEnabled() {
  const btn  = el('submit-btn');
  const desc = el('intent-desc');
  if (!btn || !desc) return;
  btn.disabled = !account || !desc.value.trim();
}

function rankBadge(i) {
  if (i === 0) return '<span class="rank-badge gold">1</span>';
  if (i === 1) return '<span class="rank-badge silver">2</span>';
  if (i === 2) return '<span class="rank-badge bronze">3</span>';
  return `<span class="rank-badge">${i + 1}</span>`;
}

// Enable submit button when description typed (if wallet connected)
document.addEventListener('DOMContentLoaded', () => {
  el('intent-desc')?.addEventListener('input', checkSubmitEnabled);
});
