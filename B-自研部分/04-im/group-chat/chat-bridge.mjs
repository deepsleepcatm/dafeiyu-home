// 群聊桥：群消息 → 叫醒对应的组 → 它的回复写回群（组A 2026-10-29）
//
// 为什么不用轮询调度器：轮询的天花板是"间隔"。桥是事件驱动的——
// 你 @ 谁，1~2 秒那个组就开始动，而且组之间互相 @ 也能触发。
//
// 用法：
//   node src/chat-bridge.mjs            # 常驻
//   node src/chat-bridge.mjs --once     # 只跑一轮（调试）
//   node src/chat-bridge.mjs --dry      # 只打印"会叫醒谁"，不发 prompt
//
// 依赖：qq-bridge 自己的 dsh-client（鉴权）+ node:sqlite（读群聊库）

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require_ = createRequire(import.meta.url);
// config.py 是 Python 的，这里用同样规则读同一份 json。
import fs0 from 'node:fs';
const _cfgJson = (() => {
  try {
    const p = path.join(process.env.DSH_HOME || path.join(process.env.USERPROFILE || '', '.dsh'), 'mem-tools', 'config.json');
    return JSON.parse(fs0.readFileSync(p, 'utf8'));
  } catch { return {}; }
})();
const cfgv = (key, dflt) => {
  const parts = String(key).split('.');
  let cur = _cfgJson;
  for (const p of parts) { if (cur && typeof cur === 'object' && p in cur) cur = cur[p]; else return dflt; }
  return cur ?? dflt;
};
const expand = (v) => String(v).replace(/^~/, process.env.USERPROFILE || '');

import { makeTracer } from './trace.mjs';
import { NodeApiClient, unwrap, createTurnCollector, discoverDshLaunchToken } from './dsh-client.js';
import { DatabaseSync } from 'node:sqlite';

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\//, ''));
const ROOT = path.join(HERE, '..');
const CHAT_DIR = expand(cfgv('chat.dir', '~/dafeiyu/协作/群聊'));
const DB_PATH = path.join(CHAT_DIR, 'chat.db');
const STATE_PATH = path.join(CHAT_DIR, 'bridge-state.json');

// 你的组名，自己改。数组里写几个，群聊就开几条会话。
// 规则：谁是「主人」，谁是「组长」，哪些是干活的组 —— 全由你定。
const GROUPS = ['主人', '组长', '组A', '组B', '组C'];

// 主人不一定写 @：她会写「3.角色组 阿越不像人设了」「组B 去学一下」。
// 所以除了 @，还要认「开头就点名」和一堆口语别名。
// 口语别名：你在群里可能不写全名，写简称它也能认。
// 比如配了 '前端': '组A'，你说「@前端」它就找组A。不想配就留空。
const ALIASES = {
  // '别名': '组A',
};

const COOLDOWN_MS = 2 * 1000;       // 2026-10-31 23:48 省钱：同组 20 秒一次（原来拉满到 2 秒）
const HOURLY_CAP = 600;               // 23:48 省钱：一小时 40 次（原来 600）
const BROADCAST_EXCLUDE = new Set(['主人', '组长']);   // @所有人 时不惊动这几个   // @所有人 时不惊动这几个
const BROADCAST_PACE_MS = 3000;     // 23:48 省钱：广播 10 秒一个
const PENDING_FILE = path.join(CHAT_DIR, '广播待办.jsonl');

const pendingAsk = new Map();   // 组 -> {text, at}：说自己卡住了，还没闭环
const ASK_RE = /(要学|不会|卡在|搞不定|不懂)/;
const DONE_RE = /(完成|好了|做好了|交付|路径|已解决|跑完|出好了)/;
const ASK_TTL_MS = 10 * 60 * 1000;

function noteAskIfAny(group, text) {
  if (DONE_RE.test(text)) { pendingAsk.delete(group); return; }
  if (ASK_RE.test(text)) pendingAsk.set(group, { text: String(text).slice(0, 80), at: Date.now() });
}

function pumpAskNudge() {
  return;   // 2026-10-31 20:41 主人说「提醒没关」→ 彻底停掉，不再往群里写〔提醒〕
  const now = Date.now();
  for (const [group, item] of pendingAsk) {
    if (now - item.at < ASK_TTL_MS) continue;
    pendingAsk.delete(group);
    const line = `〔提醒〕${group} 十分钟前说卡在「${item.text}」——解决了没？没解决就 @组B，解决了就把文件路径报一句。`;
    try { addMsg(nowStr(), '提醒', line); } catch {}
    log(`提醒未闭环：${group}`);
  }
}

const retryQueue = [];   // 冷却/忙的时候被跳过的消息，稍后补叫一次
// ---------- 工单台账（Super-agent 内化 2026-11-20）：每个派活一张 Ticket ----------
const TICKET_FILE = path.join(CHAT_DIR, '工单.jsonl');
const tickets = new Map();          // id -> {group,sender,text,status,tries,dueAt,createdAt}
let ticketSeq = 0;
function saveTickets() {
  try { fs.writeFileSync(TICKET_FILE, [...tickets.values()].map(t => JSON.stringify(t)).join('\n') + '\n', 'utf8'); } catch {}
}
function loadTickets() {
  try {
    if (!fs.existsSync(TICKET_FILE)) return;
    for (const line of fs.readFileSync(TICKET_FILE, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      const t = JSON.parse(line);
      tickets.set(t.id, t);
      ticketSeq = Math.max(ticketSeq, Number(String(t.id).replace('t','')) || 0);
      //
      // 原来这里无条件是 pushRetry(t.group, t.sender, t.text, 30000) ——
      // ① 没传第 5 个参数 msgId，唤醒消息里永远是 #0
      // ② 没有过期判断，一个没关的工单每次重启都重放一次
      // ③ 组看到「0 条未读」就去读最新，读到上一条又触发唤醒 → 3 分钟一轮
      // 组C被空唤醒 20 次就是这么来的。
      if (t.status === 'doing' || t.status === 'open') {
        const ageMs = Date.now() - (Number(t.createdAt) || 0);
        const MAX_AGE = 6 * 60 * 60 * 1000;          // 6 小时还没关，认定废了
        if (Number(t.createdAt) > 0 && ageMs > MAX_AGE) {
          t.status = 'stale';
          log(`[工单] 过期不恢复 #${t.id}（${Math.round(ageMs / 3600000)}h 前开的，${t.group} ← ${t.sender}）`);
          continue;
        }
        //
        // 原来这里 pushRetry 重放一次，而 wake() 里又会 openTicket 开一个新工单 ——
        // 于是每重启一次工单翻倍，同一句话被唤醒 N 次（组C吃到 20 次就是这么滚出来的）。
        //
        // 重启后旧工单的会话上下文已经没了，重放只会让它读一段已经处理过的旧话。
        // 所以：只记录「还有这些没关」，不重试。要重试得人手动来。
        t.status = 'stale';
        log(`[工单] #${t.id} 上次没关，标 stale 不再重放（${t.group} ← ${t.sender}）`);
      }
    }
  } catch (e) { log(`[工单] 台账读取失败：${e?.message ?? e}`); }
}
const ticketDedupe = new Map();   // 2026-11-24：key = 组|前80字 → 工单 id
function openTicket(group, sender, text) {
  // 原来重放 + 重开会让同一句话堆出 3 个工单（#t4591/#t4595/#t4596）。
  const dk = `${group}|${String(text ?? '').slice(0, 80)}`;
  const prev = ticketDedupe.get(dk);
  if (prev) {
    const pt = tickets.get(prev);
    if (pt && (pt.status === 'open' || pt.status === 'doing')) {
      log(`[工单] 同一句话已有 #${prev}，不重复开票（${group}）`);
      return prev;
    }
  }
  if (ticketDedupe.size > 300) {
    const keep = [...ticketDedupe.entries()].slice(-200);
    ticketDedupe.clear();
    for (const [k, v] of keep) ticketDedupe.set(k, v);
  }
  const made = _openTicketInner(group, sender, text);
  ticketDedupe.set(dk, made);
  return made;
}
function _openTicketInner(group, sender, text) {
  ticketSeq += 1;
  const t = { id: 't' + ticketSeq, group, sender, text, status: 'open', tries: 0, dueAt: Date.now() + 60 * 60 * 1000, createdAt: Date.now(), closedAt: null };
  tickets.set(t.id, t);
  saveTickets();
  return t.id;
}
function markTicket(id, status) {
  const t = tickets.get(id);
  if (!t) return;
  t.status = status;
  t.closedAt = Date.now();
  saveTickets();
}
function failTicket(id, reason) {
  const t = tickets.get(id);
  if (!t) return;
  t.tries += 1;
  t.status = t.tries >= 3 ? 'unavailable' : 'open';
  if (t.status === 'unavailable') {
    t.closedAt = Date.now();
    log(`[工单] #${id} 连挂 3 次，标 unavailable：${reason ?? ''}`);
    addMsg(nowStr(), '组A', `〔工单〕#${id} ${t.group} 的活连挂 3 次（${reason ?? '未回'}），不自动重试了。`);
  } else {
    t.dueAt = Date.now() + (10 * 60 * 1000);
    // 重试后 wake() 拿到 0 → 提示写「#0、0 条未读」→ 组去读最新又触发 → 自己喂自己。
    // 同上：不能用 maxId() 兜底，没有就传 0
    const tid2 = Number(t.msgId) > 0 ? Number(t.msgId) : 0;
    pushRetry(t.group, t.sender, t.text, 15 * 1000 * t.tries, tid2);   // 退避：15s→30s→45s
    log(`[工单] #${id} 第 ${t.tries} 次失败，${15 * t.tries} 秒后补叫`);
  }
  saveTickets();
}

function pushRetry(group, sender, text, delayMs, msgId = 0) {
  // 于是提示变成「#0，之后 0 条未读」—— 组C连吃两轮就是这个原因。
  // 广播那条路径（pushBroadcast）存了 srcId，只有 retry 这条漏了。
  retryQueue.push({ group, sender, text, dueAt: Date.now() + delayMs, msgId: Number(msgId) || 0 });
  if (retryQueue.length > 40) retryQueue.shift();
}

function pumpRetry() {
  if (!retryQueue.length) return;
  const now = Date.now();
  for (let i = 0; i < retryQueue.length; i++) {
    const job = retryQueue[i];
    if (now < job.dueAt) continue;
    if (SKIP_GROUPS.has(job.group) || !state.sessions[job.group]) { retryQueue.splice(i, 1); i--; continue; }
    if (inFlight.has(job.group)) continue;
    const last = state.cooldown[job.group] ?? 0;
    if (now - last < COOLDOWN_MS) { job.dueAt = last + COOLDOWN_MS + 1000; continue; }
    retryQueue.splice(i, 1); i--;
    inFlight.add(job.group);
    wake(job.group, job.sender, job.text, 0, 'group', job.msgId || 0)
      .then((ok) => { for (const t of tickets.values()) if (t.status === 'open' && t.group === job.group && t.sender === job.sender) markTicket(t.id, 'done'); })
      .catch((e) => log(`补叫 ${job.group} 失败：${e?.message ?? e}`))
      .finally(() => inFlight.delete(job.group));
    log(`补叫：${job.group}（刚才被冷却挡下）`);
    break;
  }
}

const broadcastQueue = [];
const BQ_FILE = path.join(CHAT_DIR, 'broadcast_queue.json');
function saveBQ() { try { fs.writeFileSync(BQ_FILE, JSON.stringify(broadcastQueue), 'utf8'); } catch {} }
function loadBQ() {
  try {
    const v = JSON.parse(fs.readFileSync(BQ_FILE, 'utf8'));
    if (Array.isArray(v)) broadcastQueue.push(...v);
    if (broadcastQueue.length) log(`恢复上次没叫完的广播：${broadcastQueue.length} 组`);
  } catch {}
}          // [{text, groups:[], at}]
let lastBroadcastWake = 0;

let lastBroadcastAt = 0;
function pushBroadcast(text, groups, srcId = 0, from = '') {
  // 现在只合并「完全相同的那句」，不同的话一律排队，一条都不许吞。
  const now = Date.now();
  const same = broadcastQueue.some((j) => j.text === text);
  if (same) { log('广播合并：同一句话已在队列里，不重复排'); return; }
  lastBroadcastAt = now;
  broadcastQueue.push({ text, groups: [...groups], at: Date.now(), srcId: Number(srcId) || 0, from: from || '群里有人' });
  saveBQ();
  log(`广播入队：${groups.length} 个组（队列 ${broadcastQueue.length} 条）`);
}

// 每次 tick 调用：队列非空且到点了，就叫一个
function pumpBroadcast() {
  if (!broadcastQueue.length) return;
  if (Date.now() - lastBroadcastWake < BROADCAST_PACE_MS) return;
  const job = broadcastQueue[0];
  while (job.groups.length) {
    const g = job.groups.shift();
    if (SKIP_GROUPS.has(g)) continue;
    if (inFlight.has(g)) { job.groups.push(g); break; }   // 忙就放回队列尾，下轮再说
    if (inFlightSessions.has(state.sessions[g])) continue;
    lastBroadcastWake = Date.now();
    inFlight.add(g);
    wake(g, job.from || '群里有人', job.text, 0, 'group', job.srcId || 0)
      .catch((e) => log(`广播叫醒 ${g} 失败：${e?.message ?? e}`))
      .finally(() => inFlight.delete(g));
    log(`广播轮到这个：${g}（还剩 ${job.groups.length} 个）`);
    break;
  }
  if (!job.groups.length) { broadcastQueue.shift(); }
  saveBQ();
}

function recordPending(text, groups) {
  try {
    const line = JSON.stringify({ at: nowStr(), groups, text: String(text).slice(0, 300) });
    fs.appendFileSync(PENDING_FILE, line + '\n', 'utf8');
  } catch (e) { log(`写待办失败：${e?.message ?? e}`); }
}
const TURN_TIMEOUT_MS = 180 * 1000;  // 一次唤醒最多等 3 分钟
const MAX_HOPS = 3;                  // 组之间最多聊三轮（主人=0，一轮=1）

const argv = new Set(process.argv.slice(2));
const DRY = argv.has('--dry');
const ONCE = argv.has('--once');

function log(msg) { try { console.log(`[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}] ${msg}`); } catch {} }

// ---------- DSH 客户端 ----------
const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
const token = cfg?.dsh?.authToken || discoverDshLaunchToken() || undefined;
const api = new NodeApiClient(cfg.dsh.baseUrl, undefined, {
  token,
  tokenExplicit: cfg?.dsh?.authTokenExplicit === true,
  header: cfg?.dsh?.authHeader,
  prefix: cfg?.dsh?.authPrefix,
  allowRemote: cfg?.dsh?.allowRemote === true,
});

// ---------- 群聊库 ----------
const db = new DatabaseSync(DB_PATH);
const q = (sql, ...args) => db.prepare(sql).all(...args);
const addMsg = (ts, sender, text, at = '', scope = 'group') =>
  db.prepare('INSERT INTO messages(ts,sender,text,at,scope) VALUES(?,?,?,?,?)').run(ts, sender, text, at, scope);

function nowStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// ---------- 状态 ----------
function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8')); } catch {
    const maxId = q('SELECT COALESCE(MAX(id),0) AS m FROM messages')[0].m;
    return { lastId: maxId, sessions: {}, workspaceId: null, cooldown: {}, hourly: {} };
  }
}
const state = loadState();
const trace = makeTracer(CHAT_DIR);

const saveState = () => fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2), 'utf8');

// ---------- 会话 ----------
async function ensureWorkspace() {
  if (state.workspaceId) return state.workspaceId;
  const v = unwrap(await api.workspace.create({ path: expand(cfgv('workspace.dir', '~/dsh-workspace')) }), 'workspace.create');
  state.workspaceId = v.workspace.workspaceId;
  saveState();
  log(`工作区就绪 ${state.workspaceId}`);
  return state.workspaceId;
}

async function ensureSession(group) {
  // 严禁自动建会话：组必须映射到它**原来的**对话。
  // 自动建出来的空壳会污染主人的会话列表（2026-10-29 已经被投诉两次）。
  const sid = state.sessions[group];
  if (!sid) {
    log(`跳过 ${group}：没有映射到已有会话（不自动新建）`);
    return null;
  }
  return sid;
}

// ---------- 事件泵 + 收集器 ----------
const collectors = new Map();   // sessionId -> {collector, resolve, timer}
const reverse = new Map();      // sessionId -> group

function makeCollector(sessionId, group) {
  return new Promise((resolve) => {
    const collector = createTurnCollector();
    const timer = setTimeout(() => {
      collectors.delete(sessionId);
      resolve({ ok: false, text: '', reason: 'timeout' });
    }, TURN_TIMEOUT_MS);
    collectors.set(sessionId, { collector, resolve, timer, group });
  });
}

async function pump() {
  for (;;) {
    try {
      for await (const envelope of api.events.mux({})) {
        const frame = envelope?.payload;
        if (!frame || frame.type !== 'session/event') continue;
        const entry = collectors.get(frame.sessionId);
        if (!entry) continue;
        const ended = entry.collector.push(frame.event);
        if (ended) {
          clearTimeout(entry.timer);
          collectors.delete(frame.sessionId);
          entry.resolve({
            ok: ended.reason?.kind === 'completed',
            text: (ended.text || '').trim(),
            reason: ended.reason?.kind,
          });
        }
      }
    } catch (e) {
      log(`事件流断了：${e?.message ?? e}，3 秒后重连`);
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

// ---------- 唤醒 ----------
const recentSenders = [];
const inFlightSessions = new Set();   // 按会话锁：两个组共用一个会话时不打架
const inFlight = new Set();
const SKIP_GROUPS = new Set([]);

// 主人常写简写：「@角色」「@配音」「@封面」——认这几个字就够
// 主人的口语简称 → 正式组名。你在群里说「@前端」，它就找「组A」。
// 自己按需要加，不加也行（那就要写全名）。
const SHORT_NAMES = {
  'a': '组A', 'b': '组B', 'c': '组C',
};;

function mentionedGroups(text) {
  const out = new Set();
  // 组装归一：写「组装」和写「组长」指的是同一个人
  const raw = String(text ?? '').replace(/组装/g, '组长');

  // ⓪ 广播：@所有人 / 所有人 / 全体
  if (/@所有人|所有人|全体/i.test(raw)) {
    for (const g of GROUPS) out.add(g);
    return [...out];
  }

  // ① 显式 @（全名或去掉圈码的裸名：「@内容」也算 @组E）
  for (const g of GROUPS) {
    const bare = g.replace(/^[①-⑬]/, '');
    const idx = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬'.indexOf(g[0]) + 1;
    if (raw.includes('@' + g) || raw.includes('@' + bare)) out.add(g);
    // 「@9组N」「@6出图」：@ 后面跟数字再跟名字
    if (idx > 0 && new RegExp('@' + idx + '\\s*' + bare).test(raw)) out.add(g);
    if (idx > 0 && bare.length >= 2 && new RegExp('@[0-9]{0,2}\\s*' + bare).test(raw)) out.add(g);
    // 只写序号也认：「@3 阿越重做」「@③」
    // 规则：紧跟序号后面如果写的是别的组名，就不算这个组。
    if (idx > 0) {
      const circled = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬'[idx - 1];
      const otherBares = GROUPS
        .filter((x) => x !== g)
        .map((x) => x.replace(/^[①-⑬]/, ''))
        .filter((x) => x && x.length >= 2);
      const tailAfter = (mark) => {
        const i = raw.indexOf(mark);
        return i === -1 ? '' : raw.slice(i + mark.length, i + mark.length + 6).trim();
      };
      const otherStarts = (tail) => otherBares.some((o) => tail.startsWith(o));
      if (raw.includes('@' + circled) && !otherStarts(tailAfter('@' + circled))) out.add(g);
      if (new RegExp('@' + idx + '(?![0-9])').test(raw)) {
        const m = raw.match(new RegExp('@' + idx + '(?![0-9])'));
        const tail = raw.slice(raw.indexOf(m[0]) + m[0].length).trim().slice(0, 6);
        if (!otherStarts(tail)) out.add(g);
      }
    }
  }

  // ② 2026-11-03 23:01 删掉：按数字猜人（「1号」「3号」）误伤太多，只认 @

  // ③ 2026-11-03 23:01 删掉：开头点名靠猜，只认显式 @

return [...out];
}

// 一条消息"离主人有多远"：主人说的=0，组回的=N+1。
// 组之间可以自己对话，但链子最多 MAX_HOPS 层，防止 A→B→A 聊到天亮。
const hopOf = new Map();      // messageId -> hop
function setHop(id, hop) { hopOf.set(id, hop); if (hopOf.size > 400) hopOf.delete(hopOf.keys().next().value); }
function getHop(id) { return hopOf.get(id) ?? 0; }

let capNoticedHour = -1;

function allowed(group, sender, hop = 0) {
  const now = Date.now();
  if (sender === group) return 'self';
  if (hop > MAX_HOPS) return 'chain-too-long';
  const last = state.cooldown[group] ?? 0;
  if (group !== '组A' && now - last < COOLDOWN_MS) return 'cooldown';
  const hourKey = Math.floor(now / 3600000);
  state.hourly[hourKey] = state.hourly[hourKey] ?? 0;
  if (state.hourly[hourKey] >= HOURLY_CAP) {
    if (capNoticedHour !== hourKey) {
      capNoticedHour = hourKey;
      try { addMsg(nowStr(), '组A', `本小时唤醒配额用完了（${HOURLY_CAP} 次），到下一个整点恢复。不是它们不理你。`); } catch {}
    }
    return 'hourly-cap';
  }
  // 原来组A在思考时，桥照样往它的会话里塞 prompt，DSH 会排队 ——
  // 等它这一轮结束，排队的 3-5 条一次性投递，看起来就像「同一句话被唤醒 N 次」。
  // 现在：忙就返回 busy，交给 retryQueue 兜底（那边已带真实 msgId）。
  if (inFlight.has(group)) return 'busy';
  return null;
}

function briefCard(group) {
  try {
    const p = expand(cfgv('chat.brief_card', '~/dsh-workspace/工位卡.md'));
    const txt = fs.readFileSync(p, 'utf8');
    const start = txt.indexOf(`# 你是 ${group}`);
    if (start < 0) return '';
    const next = txt.indexOf('\n# 你是 ', start + 5);
    return txt.slice(start, next < 0 ? start + 320 : Math.min(next, start + 320));
  } catch { return ''; }
}


// 把"汇报腔/长文"剪成"像人在群里说的一句话"
function humanize(t, maxLen = 120, maxSent = 2) {
  // 组长这种要排期、要讲理由的角色放宽到 6 句 / 900 字，其它组照旧短。
  let s = String(t || '').trim();
  s = s.replace(/```[\s\S]*?```/g, ' ')
       .replace(/^\s*#{1,6}\s*/gm, '')
       .replace(/\*\*(.+?)\*\*/g, '$1')
       .replace(/`([^`]+)`/g, '$1')
       .replace(/^\s*[-*+]\s+/gm, '')
       .replace(/^\s*\d+[.、)]\s+/gm, '')
       .replace(/\|/g, ' ')
       .replace(/\[(?:[^\]]*)\]\([^)]*\)/g, '$1')
       .replace(/[ \t]{2,}/g, ' ')
       .trim();
  const parts = s.split(/(?<=[。！？!?])/).map((x) => x.trim()).filter(Boolean);
  let out = parts.slice(0, maxSent).join('') || s;
  out = out.replace(/\n+/g, ' ').trim();
  if (out.length > maxLen) out = out.slice(0, maxLen - 2) + '…';
  return out;
}
function maxId() {
  return q('SELECT COALESCE(MAX(id),0) AS m FROM messages')[0].m;
}

// 只给它「上次读到之后」的新消息；第一次叫醒时给最近 6 条当背景
// 组去读最新几条常看到自己刚发的那条，就以为误报。这里把「起点 + 条数」算准。
function unreadFrom(group, uptoId) {
  const seen = Number(state.seen?.[group] ?? 0);
  const from = Number.isFinite(seen) && seen > 0 ? seen : Math.max(0, Number(uptoId) - 8);
  // 原来这里不排除 sender，于是组C被叫醒后按提示去读，读出来的是自己发的那条。
  // 「谁说的话就不叫谁」这句在 targets 里做了，未读计数这里漏了。
  const n = q(
    'SELECT COUNT(*) AS c FROM messages WHERE id>? AND id<=? AND sender<>?',
    from, Number(uptoId) || 0, group
  )[0].c;
  return { from, count: Number(n) || 0 };
}

function contextFor(group) {
  const seen = state.seen?.[group];
  const from = Number.isFinite(seen) ? seen : Math.max(0, maxId() - 6);
  const rows = q('SELECT id,sender,text FROM messages WHERE id>? ORDER BY id ASC LIMIT 3', from);
  if (!rows.length) return '（没有新消息，你上次读到的那条之后没人再说话）';
  return rows.map((r) => `${r.sender}: ${r.text.slice(0, 50)}`).join('\n');
}

function markSeen(group) {
  if (!state.seen) state.seen = {};
  state.seen[group] = maxId();
}

function senderLabel(s) {
  if (!s) return '主人';
  if (s.startsWith('TG·') || s.startsWith('QQ·')) return '主人';
  return s;
}

// 「在吗」这类不用叫模型，直接回一句，省一次唤醒（2026-10-29 23:40）
const PING_RE = /^(在吗|在不在|在么|在不在呀|有人吗|在)[\s？?。！!]*$/;

// ---------- 上下文预算：超过 80% 自动换新会话（2026-11-03 05:41 主人问「撑爆怎么办」） ----------
const CTX_LIMIT = 0.8;
// ---------- 全员静音（2026-11-03 06:19 主人：他们自己聊起来净是没营养的，先全员静音） ----------
const MUTE_FLAG = path.join(CHAT_DIR, '静音.flag');
function isMuted() {
  try { return fs.existsSync(MUTE_FLAG); } catch { return false; }
}

function oneLine(t, max = 180) {
  let s = String(t ?? '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!?\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (s.length > max) s = s.slice(0, max - 1) + '…';
  return s;
}

async function rotateIfBloated(group, sid) {
  try {
    const v = unwrap(await api.sessions.list({}), 'session.list');
    const s = (v.items ?? []).find((x) => x.sessionId === sid);
    const vals = s?.projections?.values ?? {};
    const p = vals.contextPressure ?? {};
    const used = Number(p.projectedTokens ?? p.pressureTokens ?? 0);
    const win = Number(p.contextWindow ?? 0);
    if (!win || used / win < CTX_LIMIT) return sid;
    log(`[轮换] ${group} 上下文 ${((used / win) * 100).toFixed(0)}% → 换新会话`);
    const created = unwrap(await api.sessions.create(state.workspaceId ? { workspaceId: state.workspaceId } : {}), 'session.create');
    const fresh = created?.sessionId;
    if (!fresh) return sid;
    state.sessions[group] = fresh;
    saveState();
    return fresh;
  } catch (e) {
    log(`[轮换] ${group} 检查失败：${e?.message ?? e}`);
    return sid;
  }
}

const wakeDedupe = new Map();

async function wake(group, sender, message, hop = 0, scope = 'group', msgId = 0) {
  // ═══ 2026-11-24 组A（第七层）：去重放在**唯一入口**，所有路径都过这一关 ═══
  //
  // 原来加在 inFlightSessions.add 之前，只覆盖了 tick 那条路；
  // 「忙→补叫」走 pumpRetry，绕过它，于是同一条消息被唤醒第二次（组C收到的"重复"就是这个）。
  // 现在放在函数第一行：tick / 补叫 / 广播 / 机检 不管从哪进来，都先过这一关。
  {
    const dk = `${group}|${senderLabel(sender)}|${String(message ?? '').slice(0, 80)}`;
    const last = wakeDedupe.get(dk) || 0;
    const gap = Date.now() - last;
    if (last && gap < 5 * 60 * 1000) {
      log(`跳过 ${group}：5 分钟内同一句话已唤醒过（${Math.round(gap / 1000)}s 前），不重复叫`);
      return;
    }
    wakeDedupe.set(dk, Date.now());
    if (wakeDedupe.size > 300) {
      const keep = [...wakeDedupe.entries()].sort((a, b) => b[1] - a[1]).slice(0, 300);
      wakeDedupe.clear();
      for (const [k, v] of keep) wakeDedupe.set(k, v);
    }
  }
  const TASK_RE = /(做|出|改|跑|生成|画|配|剪|写|整理|学|查|下载|重做|补|提交|开始|动手|安排|拼|审|对齐|核对|导出|上传|练|顶|搞)/;
  const isTask = TASK_RE.test(String(message ?? ''));
  // 不是"干活"，就交给接线员
  // 主人的话永远走真会话：她要的是本人回答；组与组之间的闲聊才只转达（2026-10-30 02:29 选 A）
  const fromOwner = sender === '主人' || sender.startsWith('TG·') || sender.startsWith('QQ·');
  const fromMe = sender === '组A';       // 组A说的话不转达：它 @ 谁就是说给谁听
  if (!isTask && !fromOwner && !fromMe && group !== '组A' && apiKey()) {
    // 是"提问"就真去叫人（说了"去通知"就必须真通知，不许撒谎）
    const isQuestion = /[？?]|吗|怎么|哪|为什么|能不能|是否|是不是/.test(String(message ?? ''));
    if (isQuestion) {
      addMsg(nowStr(), '接线员', `〔转达〕${senderLabel(sender)} 在问 ${group}，我去叫它本人答。`);
      log(`[接线员] 提问 → 交回真会话 ${group}`);
      // 不 return：往下走会话路径
    } else {
      markSeen(group);
      state.cooldown[group] = Date.now();
      log(`[接线员] 寒暄，静默跳过：${group}`);
      return;
    }
  }
  if (isMuted() && group !== '组A') { log(`静音中，不叫 ${group}`); return; }

  // 省电模式：通知/回执这类，免费模型代答一句，不开真会话（2026-11-18）
  if (cheapEligible(group, sender, message)) {
    try {
      const said = await cheapReply(group, sender, message);
      if (said) {
        addMsg(nowStr(), group, said);
        markSeen(group);
        state.cooldown[group] = Date.now();
        saveState();
        log(`[省·免费代答] ${group}: ${said.slice(0, 40)}`);
        return;
      }
      log(`[省·免费代答] ${group} 没出话，改走真会话`);
    } catch (e) {
      log(`[省·免费代答] 出错，改走真会话：${e?.message ?? e}`);
    }
  }

  let sid = await ensureSession(group);   // let：上下文超 80% 时 rotateIfBloated 会换成新会话
  if (!sid) return;   // 没映射就别乱叫（2026-10-29 16:47 广播时踩过）
  // 同上：组A也不许在会话忙时重复塞 prompt
  if (inFlightSessions.has(sid)) { log(`跳过 ${group}：会话正忙`); return; }
  sid = await rotateIfBloated(group, sid);   // 上下文超 80% 就换新会话，别等撑爆
  if (PING_RE.test(String(message ?? '').trim())) {
    addMsg(nowStr(), group, '在。');
    log(`[省] ${group}：只问在不在，没叫模型`);
    state.cooldown[group] = Date.now();
    markSeen(group);
    return;
  }
  // （去重已移到函数最开头，唯一入口 — 2026-11-24 第七层）
  inFlightSessions.add(sid);
  reverse.set(sid, group);
  // 原来直接 `msgId || maxId()`，unread.count 恒 0，模板又照写「#0」「0 条未读」，
  // 组看到 0 条未读就去读最新，读到上一条又触发唤醒 —— 自己喂自己，3 分钟一轮。
  const realMsgId = Number(msgId) > 0 ? Number(msgId) : 0;
  const unread = unreadFrom(group, realMsgId || maxId());
  const hasRealNews = realMsgId > 0 && unread.count > 0;
  // 主人要的是"说到就去做"：命令句就不许只回一句完事

  // 组A被叫醒时只给一行，别把整段工位卡灌进主人的对话窗口
  const SHORT_WAKE = true;
  const prompt = SHORT_WAKE && group === '组A' ? [
    `【群里有人找你】${senderLabel(sender)}：${oneLine(message, 240)}`,
    hasRealNews
      ? `（读 /api/messages?afterSeq=${unread.from} 能看到 ${unread.count} 条新的，别自己猜。）`
      : '（这不是“有新消息”的通知，是上面那件事要你处置。）',
    '你是组A。能一句话回就回；要动手就先做，做完说「好了，在 <目录>」。',
    '直接给结论，不要复述这条指令，也不要在思考里重复同一句话。',
  ].join('\n') : [
    `你是「${group}」。`,
    `工位说明在 ${cfgv('chat.seat_dir', '~/dafeiyu/协作/工位')}\\${group}.md，开工前读它。`,
    `组状态卡在 ${cfgv('chat.state_dir', '~/dafeiyu/协作/群聊/状态卡')}\\${group}.md（跨会话记忆：你的上次进展/未完成的事）。开头就写「续上：…」，写完你的进展后改它，只留最新 10 行。`,
    hasRealNews
      ? `${senderLabel(sender)} 在群里说了新消息（#${realMsgId}）。你上次读到 #${unread.from}，之后有 ${unread.count} 条未读；读 /api/messages?afterSeq=${unread.from} 看全文，别自己猜。`
      : `${senderLabel(sender)} 有事找你：${oneLine(message, 300)}。这不是“有新消息”的通知，别去读消息列表找不存在的未读。`,
  ].join('\n');


  if (DRY) { log(`[dry] 会叫醒 ${group}（${sid}）`); return; }

  log(`叫醒 ${group}：${senderLabel(sender)} → #${msgId}，未读 ${unread.count} 条（从 #${unread.from}）`);
  // 工单台账：干活的叫醒才开票（Super-agent 内化 2026-11-20）
  const ticketId = (isTask || fromOwner) ? openTicket(group, senderLabel(sender), oneLine(message, 80)) : null;
  const wait = makeCollector(sid, group);
  const accepted = await api.sessions.prompt({ sessionId: sid, mode: 'queue', content: [{ type: 'text', text: prompt }] });
  if (!accepted?.ok && accepted?.result?.ok === false) throw new Error('prompt 被拒');
  const result = await wait;
  if (result.ok && result.text) {
    // 现在放宽到 600 字 / 5 句（组长仍 900/6）。真需要长文就走文件路径，不在群里灌。
    const said = humanize(result.text,
      group === '组长' ? 900 : 600,
      group === '组长' ? 6 : 5);
    noteAskIfAny(group, said);
    // 不许自动发进群：组A的回复只给主人看，要发群我自己显式发
    // 组A的回复不自动进群：我自己用 chat.py 显式发，避免把跟主人的长对话漏到群里（2026-10-30 04:02）
    const info = (group === '组A') ? null : addMsg(nowStr(), group, said);
    try { setHop(Number(info.lastInsertRowid), hop + 1); } catch {}
    markSeen(group);
    if (ticketId) { markTicket(ticketId, 'done'); log(`[工单] #${ticketId} 完成`); }
    log(`[回] ${group}: ${said.slice(0, 60)}`);
    try { gateAfterDone(group, said); } catch (e) { log(`机检出错：${e?.message ?? e}`); }
  } else {
    log(`[空] ${group} 没回（${result.reason}）`);
    if (ticketId) failTicket(ticketId, result.reason ?? '没回');
    markSeen(group);
  }
  recentSenders.push(group);
  if (recentSenders.length > 6) recentSenders.shift();
  state.cooldown[group] = Date.now();
  const hourKey = Math.floor(Date.now() / 3600000);
  state.hourly[hourKey] = (state.hourly[hourKey] ?? 0) + 1;
  inFlightSessions.delete(sid);
  saveState();
}


// ---------- 维修提醒：守护 → 组A（不进群） ----------
const ALERT_FILE = expand(cfgv('chat.alert_file', '~/dafeiyu/协作/维修/维修提醒.jsonl'));
const ALERT_SESSION = 'session-957448e2-984f-44f3-9e02-ed5c102c0de9';   // 组A本体（当前对话）
let alertOffset = -1;

function pumpAlerts() {
  try {
    if (!fs.existsSync(ALERT_FILE)) return;
    if (alertOffset < 0) { alertOffset = fs.statSync(ALERT_FILE).size; return; }   // 首轮不补历史
    const st = fs.statSync(ALERT_FILE);
    if (st.size <= alertOffset) return;
    const buf = Buffer.alloc(st.size - alertOffset);
    const fd = fs.openSync(ALERT_FILE, 'r');
    fs.readSync(fd, buf, 0, buf.length, alertOffset);
    fs.closeSync(fd);
    alertOffset = st.size;
    for (const line of buf.toString('utf8').split('\n')) {
      if (!line.trim()) continue;
      let item;
      try { item = JSON.parse(line); } catch { continue; }
      const ageMin = (Date.now() - Date.parse(String(item.at).replace(' ', 'T'))) / 60000;
      if (!Number.isFinite(ageMin) || ageMin > 10) { log(`跳过过期提醒（${Math.round(ageMin)} 分钟前）`); continue; }
      const text = `[守护·${item.at}] ${item.text}`;
      api.sessions.prompt({
        sessionId: ALERT_SESSION,
        mode: 'queue',
        content: [{ type: 'text', text }],
      }).catch((e) => log(`发给组A失败：${e?.message ?? e}`));
      log(`已把守护的提醒转给组A：${String(item.text).slice(0, 40)}`);
    }
  } catch (e) {
    log(`读维修提醒失败：${e?.message ?? e}`);
  }
}


// 每个组只干自己那一摊：便宜嘴必须带着职责说话，否则会替别人发言（2026-10-30 02:24）
const JOB = {













  '组长': '统筹与裁定。五轨合成也归它（2026-11-03 组装组撤了）。',
  '组B': '缺方法时去学，写成文件给各组。',
  '组C': '人格与连续性。',
};

// ---------- 便宜嘴：闲聊/回执直接调 API，不开会话（2026-10-30） ----------
const API_KEY_FILE = path.join(CHAT_DIR, '.api_key');
const API_URL = 'https://api.deepseek.com/v1/chat/completions';

// ---------- 省电模式（2026-11-18）：组与组之间的通知/回执，用免费模型代答，不开真会话 ----------
// 开关：协作\群聊\省电模式.flag
// 只代答"通知/进度/回执"这类，真正派活的活仍然叫真人（DeepSeek）
const CHEAP_FLAG = path.join(CHAT_DIR, '省电模式.flag');
function cheapModeOn() {
  try { return fs.existsSync(CHEAP_FLAG); } catch { return false; }
}
const CHEAP_OK_RE = /(通知|报告|更新|已完成|完成了|写好了|做好了|跑完了|已归档|已上传|已修好|好了，在|记录一下|记一笔|入库|备份完)/;
const CHEAP_NO_RE = /(帮|请|能不能|是否|要不要|怎么|为什么|哪|谁|定稿|审核|裁定|决定|交付|派给)/;
function cheapEligible(group, sender, message) {
  if (!cheapModeOn() || !freeLaneOn()) return false;
  if (group === '组A' || group === '组长') return false;          // 这两位的判断不外包
  const fromOwner = sender === '主人' || sender.startsWith('TG·') || sender.startsWith('QQ·');
  if (fromOwner) return false;                                       // 主人说话永远真会话
  const t = String(message ?? '');
  if (CHEAP_NO_RE.test(t)) return false;                             // 问句/派活/点名 → 真人
  return CHEAP_OK_RE.test(t);
}
function personaBrief(group) {
  try {
    const p = path.join(expand(cfgv('chat.seat_dir', '~/dafeiyu/协作/工位')), `${group}.md`);
    const txt = fs.readFileSync(p, 'utf8');
    return txt.slice(0, 600);
  } catch { return `你是「${group}」。`; }
}
// 用免费模型代这一条回执（不进上下文、不开会话）
async function cheapReply(group, sender, message) {
  const sys = [
    personaBrief(group),
    '你在群聊里只回一句"收到版"：确认收到 + 一句基于这条消息本身的事实。',
    '硬规矩：**不许承诺去做任何事**（不说"我去核对/我去改/稍后处理"），因为你此刻没有手；',
    '也不许寒暄、不许复述整条、不许自称 AI。最多 40 字。',
  ].join('\n');
  const usr = `${senderLabel(sender)} 在群里说：${String(message).slice(0, 300)}`;
  const t = await callFlash(sys, usr, { sensitiveSource: String(message ?? '') });
  return t ? t.trim().slice(0, 160) : null;
}

// ---------- 免费车道（2026-11-17）：闲聊走 Kilo 免费通道，敏感的自己账号 ----------
const FREE_URL = 'https://kilo.ai/api/openrouter/v1/chat/completions';
const FREE_MODEL = 'baidu/ernie-5.5-flash-preview:free';   // 实测能出话、1M 上下文
const FREE_FLAG = path.join(CHAT_DIR, '免费车道.flag');     // 存在=闲聊走免费
function freeLaneOn() {
  try { return fs.existsSync(FREE_FLAG); } catch { return false; }
}
// 敏感内容黑名单：命中就走自己的 DeepSeek，绝不经免费网关
const SENSITIVE_RE = /(密码|验证码|身份证|银行卡|支付宝|微信支付|手机号|住址|地址|合同|报价|工资|收入|欠|借|贷款|客户|甲方|私事|家里|爸妈|猫|狗)/;
let apiKeyCache = null;
function apiKey() {
  if (apiKeyCache !== null) return apiKeyCache;
  try { apiKeyCache = fs.readFileSync(API_KEY_FILE, 'utf8').trim(); }
  catch { apiKeyCache = ''; }
  return apiKeyCache;
}

async function callFlash(system, user, opts = {}) {
  const sensitive = SENSITIVE_RE.test(String(opts.sensitiveSource ?? '') + String(user || ''));
  const useFree = freeLaneOn() && !sensitive;
  const key = apiKey();
  if (!useFree && !key) return null;
  const body = JSON.stringify({
    model: useFree ? FREE_MODEL : 'deepseek-flash',
    messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
    max_tokens: 320,
    temperature: 0.7,
  });
  if (useFree) {
    try {
      const r = await fetch(FREE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      if (!r.ok) {
        const detail = await r.text().catch(() => '');
        throw new Error('free ' + r.status + ' ' + detail.slice(0, 200));
      }
      const d = await r.json();
      const t = (d?.choices?.[0]?.message?.content ?? '').trim();
      if (t) {
        log('[省·免费] ' + t.slice(0, 30));
        try {
          fs.appendFileSync(path.join(CHAT_DIR, 'usage.jsonl'),
            JSON.stringify({ ts: nowStr(), model: FREE_MODEL, tokens: d?.usage?.total_tokens ?? 0, free: true }) + '\n', 'utf8');
        } catch {}
        return t;
      }
    } catch (e) {
      log('[免费车道] 失败，退回 DeepSeek：' + (e?.message ?? e));
    }
    if (!key) return null;
  }
  const res = await fetch(API_URL, {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' },
    body,
  });
  if (!res.ok) throw new Error('API ' + res.status);
  const data = await res.json();
  const text = data?.choices?.[0]?.message?.content ?? '';
  const used = data?.usage?.total_tokens ?? 0;
  log(`[省] ${used} tokens（没开会话）`);
  try {                                   // 记一笔用量，每天由 记账.py 汇总
    fs.appendFileSync(path.join(CHAT_DIR, 'usage.jsonl'),
      JSON.stringify({ ts: nowStr(), model: 'deepseek-flash', tokens: used }) + '\n', 'utf8');
  } catch {}
  return text.trim();
}


// ---------- 机检 + 自动打回 + 自动推进（2026-10-30 主人要求） ----------
const PY_BIN = expand(cfgv('runtime.python', 'python'));
const GATE_PY = expand(cfgv('chat.gate_py', '~/dafeiyu/协作/工具/验收.py'));
const GATE_GROUPS = new Set(['组G', '组H', '组J', '组K', '组L', '组M', '组N', '组O', '组P']);
const GATE_DONE_RE = /(好了|完成|做完|交付|跑完|出好了)/;
// 谁做完 → 叫谁（只写确定能接的下一棒）
const CHAIN = {








};

function runGate() {
  const r = child_process.spawnSync(PY_BIN, [GATE_PY], { encoding: 'utf8', timeout: 120000 });
  const out = (r.stdout || '') + (r.stderr || '');
  const fails = out.split('\n').filter((l) => l.includes('❌')).map((l) => l.replace(/^\s*❌\s*/, '').trim());
  return { pass: r.status === 0, fails, tail: out.split('\n').slice(-6).join(' / ') };
}

function gateAfterDone(group, text) {
  if (!GATE_GROUPS.has(group)) return;
  if (!GATE_DONE_RE.test(text)) return;
  if (!/第04集|目录|路径|\\|\//.test(text)) return;      // 只是嘴上说"好了"，不跑机检
  const g = runGate();
  if (!g.pass) {
    const why = (g.fails.slice(0, 3).join('；') || g.tail);
    // 先问「这是怎么回事」，不直接判它错（2026-10-30 02:06 主人要求）
    let ask = null;
    if (why.includes('配音')) ask = '组M';
    else if (why.includes('封面')) ask = '组P';
    else if (why.includes('字幕')) ask = '组M';
    if (ask && state.sessions[ask]) {
      const q = `机检发现：${why}。这个数是你这边的最终值吗？是的话说明原因，不是的话什么时候改。`;
      addMsg(nowStr(), '机检', `〔先问〕${why} —— 去问 ${ask} 了，不等它答不下结论。`);
      wake(ask, '机检', q, 0).catch((e) => log(`问 ${ask} 失败：${e?.message ?? e}`));
    } else {
      addMsg(nowStr(), '机检', `〔机检〕${group} 报完成，但没过：${why}。先修再报。`);
    }
    log(`[机检] ${group} 没过：${g.fails.slice(0, 2).join('；')}`);
    return;
  }
  addMsg(nowStr(), '机检', `〔待验收〕${group} 这批机器这关过了，@主人 等你一句话：行 / 不行。`);
  const next = CHAIN[group];
  if (next && state.sessions[next] && !SKIP_GROUPS.has(next)) {
    const t = `${group} 交活了，机检也过了，你接着开工。`;
    wake(next, '机检', t, 0, 'group')
      .catch((e) => log(`自动推进到 ${next} 失败：${e?.message ?? e}`));
    addMsg(nowStr(), '机检', `〔自动推进〕已叫 ${next} 接手。`);
    log(`[推进] ${group} → ${next}`);
  }
}

// ---------- 主循环 ----------
async function tick() {
  const rows = q("SELECT id,sender,text,COALESCE(scope,'group') AS scope FROM messages WHERE id>? ORDER BY id LIMIT 20", state.lastId);
  for (const row of rows) {
    state.lastId = row.id;
    // 每条进来的消息开一条追踪
    const tr = trace.start({ msgId: row.id, sender: row.sender });
    if (row.sender.startsWith('TG·') || row.sender.startsWith('QQ·')) {
      // 主人说话的渠道；仍然算消息
    }
    // 只有"人"能派活：机器人自己的话（组A/接线员/机检/守护/秘书/提醒）一律不触发任何组
    const BOT_SENDERS = new Set(['接线员', '机检', '守护', '秘书', '提醒']);   // 2026-10-31 03:09 组A不再算机器人：它 @ 谁，谁必须被叫醒
    if (BOT_SENDERS.has(row.sender)) { tr.mark('bot', { ok: false, reason: 'sender-is-bot' }); continue; }
    const broadcast = /@所有人|所有人|全体|@all/i.test(row.text);
    let targets = mentionedGroups(row.text);
    // 醒来看到「主人说了新消息」，其实最新那条就是它自己发的。谁说的话，就不叫谁。
    targets = targets.filter((g) => g !== row.sender);
    if (row.sender === '组A') targets = targets.filter((g) => g !== '组A');   // 别自己叫自己
    tr.mark('targets', { ok: targets.length > 0, reason: targets.length ? '' : 'no-target', data: { hit: targets, broadcast } });
    if (!targets.length) { continue; }

    if (broadcast) {
      // 广播：排队慢慢叫，**每个组最后都会轮到**，但一次只叫一个，
      // 免得 14 个会话同时开跑（那是同时烧 14 份钱，还容易把机器拖垮）。
      const open = targets.filter((g) => !SKIP_GROUPS.has(g) && !BROADCAST_EXCLUDE.has(g) && state.sessions[g]);
      pushBroadcast(row.text, open, row.id, row.sender);
      tr.mark('broadcast', { ok: open.length > 0, reason: open.length ? '' : 'all-excluded', data: { queued: open.length } });
      log(`广播入队：${open.length} 个（每 ${Math.round(BROADCAST_PACE_MS / 1000)} 秒叫一个）`);
      continue;                       // 交给队列去叫，不在这一轮硬叫
    }
    for (const g of targets) {
      if (SKIP_GROUPS.has(g)) { log(`跳过 ${g}（不接桥）`); continue; }
      const hop = getHop(row.id);
      const why = allowed(g, row.sender, hop);
      if (why === 'cooldown' || why === 'busy') {
        pushRetry(g, row.sender, row.text, 5000, row.id);
        log(`跳过 ${g}（${why}）→ 记下来，等会儿补叫`);
        tr.mark('retry', { ok: false, reason: why, data: { group: g, msgId: row.id } });
        continue;
      }
      if (why) { log(`跳过 ${g}（${why}）`); tr.mark('allowed', { ok: false, reason: why, data: { group: g } }); continue; }
      if (inFlight.has(g)) { log(`跳过 ${g}（上一轮还没回完）`); tr.mark('allowed', { ok: false, reason: 'in-flight', data: { group: g } }); continue; }
      // **不 await**：一个组在思考时，别的组照样能被叫醒。
      // 09:39 之前这里是串行的，组B一跑两分钟，后面的组长就一直干等。
      inFlight.add(g);
      tr.step('wake', () => wake(g, row.sender, row.text, hop, row.scope || 'group', row.id),
              { data: { group: g } })
        .catch((e) => log(`叫醒 ${g} 失败：${e?.message ?? e}`))
        .finally(() => inFlight.delete(g));
    }
  }
  // 每条消息的追踪在这里收尾（tr 是 for 循环里的，循环内已 end；这里兜底没人用时不留悬空）
  pumpBroadcast();
  pumpRetry();
  pumpAlerts();
  pumpAskNudge();
  saveState();
}

if (!DRY) loadBQ();
loadTickets();
if (!DRY) pump();
log(`桥启动：库=${DB_PATH} 会话=${Object.keys(state.sessions).length} 个 lastId=${state.lastId}`);

// 重启后必须重新 follow 已有会话，否则收不到它们的 turn 事件 →
// 表现就是「叫醒了、但永远不回」（2026-10-29 09:41 踩过）。
if (!DRY) {
  for (const [group, sid] of Object.entries(state.sessions)) {
    try {
      api.events.follow(sid);
      reverse.set(sid, group);
      log(`重新监听 ${group}（${sid}）`);
    } catch (e) {
      log(`重新监听 ${group} 失败：${e?.message ?? e}`);
    }
  }
}

if (ONCE) {
  await tick();
  log('跑完一轮，退出');
  process.exit(0);
}

for (;;) {
  try { await tick(); } catch (e) { log(`tick 出错：${e?.message ?? e}`); }
  await new Promise((r) => setTimeout(r, 1000));
}






































