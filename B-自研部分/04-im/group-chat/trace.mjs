// trace.mjs —— 桥的结构化追踪（组A 2026-11-24，v2）
//
// 来源说明（2026-11-24 补）：
//   设计思路完整来自 cheesehaqi/dsh-qq-onebot-bridge 的 lib/trace.js（292 行，MIT）。
//   v1 我只读了前 60 行就动手，抄漏了 step() / 序列化防护 / summary() / summarizeTrace() /
//   level 过滤 / keepBytes 滚动 —— 六块。v2 按完整实现补齐。
//
// 核心思想（原文）：
//   "每条入站消息一个 traceId，每个决策点记一条（stage/ok/reason/ms），包括每一次静默丢弃"
//   —— 把 silent drop 变成 visible。
//
// 它比我 v1 强的地方，一句话：**v1 能记，v2 能解释。**
//   summarizeTrace() 把决策链翻成一句人话，直接回答"它为什么不理我"。
//
// 用法：
//   const tr = trace.start({ msgId: 123, sender: '组C' })
//   tr.mark('targets', { ok: true, data: { hit: ['组B'] } })
//   await tr.step('wake', () => wake(...))          // 自动记耗时/失败
//   tr.mark('allowed', { ok: false, reason: 'busy', data: { group: '组B' } })
//   trace.summary()                                  // 全局：哪在丢
//   trace.explain(id)                                // 单条：一句人话

import fs from 'node:fs'
import path from 'node:path'

export const LEVELS = ['debug', 'info', 'warn', 'error']

// 我们的阶段（中文名给 explain() 用）
export const STAGES = {
  inbound: '收到消息',
  bot: '机器人自己发的',
  targets: '算目标组',
  broadcast: '广播入队',
  retry: '撞冷却/忙碌',
  allowed: '闸门',
  wake: '叫醒',
  agent: '模型在跑',
  reply: '出话',
}

let seq = 0
function newTraceId(now = Date.now()) {
  seq = (seq + 1) % 1_000_000
  return `t-${Math.floor(now / 1000).toString(36)}-${seq.toString(36)}`
}

// 序列化失败不许毁掉整条写入（v1 缺这块，循环引用会丢事件）
function jsonlSafe(obj) {
  try {
    return JSON.stringify(obj)
  } catch {
    return JSON.stringify({ v: 2, ts: Date.now(), id: obj?.id ?? '?', level: 'error', stage: 'serialize', ok: false, reason: 'event not serializable' })
  }
}

function serializable(data) {
  try {
    JSON.stringify(data)
    return data
  } catch {
    let keys = []
    try { keys = Object.keys(data ?? {}).slice(0, 10) } catch { keys = [] }
    return { unserializable: true, type: typeof data, keys }
  }
}

export function makeTracer(dir, opts = {}) {
  const file = path.join(dir, 'trace.jsonl')
  const memoryLimit = opts.memoryLimit ?? 500
  const maxBytes = opts.maxBytes ?? 4 * 1024 * 1024
  const keepBytes = opts.keepBytes ?? 512 * 1024

  const ring = []
  const traces = new Map()
  const counts = { total: 0, byLevel: {}, byStage: {}, dropped: 0 }

  // 滚动：超过 maxBytes 时只保留最后 keepBytes（v1 是整个 rename，旧的全没了）
  function appendCapped(line) {
    try {
      let size = 0
      try { size = fs.statSync(file).size } catch {}
      if (size + line.length + 1 > maxBytes) {
        let keep = ''
        try {
          const buf = fs.readFileSync(file)
          const from = Math.max(0, buf.length - keepBytes)
          keep = buf.subarray(from).toString('utf8')
          const nl = keep.indexOf('\n')
          keep = nl >= 0 ? keep.slice(nl + 1) : ''
        } catch {}
        fs.writeFileSync(file, keep, 'utf8')
      }
      fs.appendFileSync(file, line + '\n', 'utf8')
      return true
    } catch {
      return false
    }
  }

  function emit(ev) {
    counts.total += 1
    counts.byLevel[ev.level] = (counts.byLevel[ev.level] ?? 0) + 1
    const sk = ev.stage || 'unknown'
    counts.byStage[sk] = (counts.byStage[sk] ?? 0) + 1

    const tr = traces.get(ev.id)
    if (tr) {
      tr.lastAt = ev.ts
      tr.stages.push(sk)
      tr.durationMs = ev.ts - tr.startedAt
    }

    ring.push(ev)
    if (ring.length > memoryLimit) ring.splice(0, ring.length - memoryLimit)

    if (!appendCapped(jsonlSafe(ev))) counts.dropped += 1
    return ev
  }

  return {
    file,
    counts,

    /** 开一条追踪。meta 会进第一条 inbound 事件。 */
    start(meta = {}) {
      const t0 = Date.now()
      const id = newTraceId(t0)
      traces.set(id, { id, startedAt: t0, stages: [], lastAt: t0, meta })
      if (traces.size > memoryLimit) {
        for (const k of [...traces.keys()].slice(0, traces.size - memoryLimit)) traces.delete(k)
      }
      let last = t0
      emit({ v: 2, ts: t0, id, level: 'info', stage: 'inbound', ok: true, ...meta })

      const handle = {
        id,
        /** 记一步。ok=false + reason 就是"为什么没走" */
        mark(stage, { ok = true, reason = '', data = null, level = 'info' } = {}) {
          const now = Date.now()
          const ev = { v: 2, ts: now, id, level, stage, ok: ok !== false, ms: Math.max(0, now - last) }
          if (reason) ev.reason = String(reason).slice(0, 300)
          if (data != null) ev.data = serializable(data)
          emit(ev)
          last = now
          return handle
        },
        /** 包一个异步调用：自动记耗时，失败时记完再抛 */
        async step(stage, fn, { data = null, level = 'debug' } = {}) {
          const t0 = Date.now()
          try {
            const v = await fn()
            emit({ v: 2, ts: Date.now(), id, level, stage, ok: true, ms: Date.now() - t0, ...(data ? { data: serializable(data) } : {}) })
            last = Date.now()
            return v
          } catch (e) {
            emit({ v: 2, ts: Date.now(), id, level: 'error', stage, ok: false, ms: Date.now() - t0, reason: String(e?.message ?? e).slice(0, 300) })
            last = Date.now()
            throw e
          }
        },
      }
      return handle
    },

    /** 单条 trace 的事件，按顺序 */
    get(id) { return ring.filter((e) => e.id === id) },

    /** 全局：哪在丢 */
    summary() {
      const reasons = new Map()
      for (const e of ring) {
        if (e.ok === false && e.reason) {
          const k = `${e.stage}: ${e.reason}`
          reasons.set(k, (reasons.get(k) ?? 0) + 1)
        }
      }
      return {
        total: counts.total,
        byStage: { ...counts.byStage },
        byLevel: { ...counts.byLevel },
        dropped: counts.dropped,
        ringSize: ring.length,
        traces: traces.size,
        topReasons: [...reasons.entries()]
          .sort((a, b) => b[1] - a[1])
          .slice(0, 8)
          .map(([reason, count]) => ({ reason, count })),
      }
    },

    /** 把一条 trace 翻成一句人话（对应它家的 /为什么） */
    explain(id) {
      const list = this.get(id).filter((e) => e.stage)
      if (!list.length) return { ok: false, chain: '', conclusion: '没有记录' }
      const chain = list.map((e) => `${STAGES[e.stage] ?? e.stage}${e.ok === false ? '⛔' : '✅'}`).join(' → ')
      const failed = list.find((e) => e.ok === false)
      let conclusion
      if (failed) {
        conclusion = `卡在「${STAGES[failed.stage] ?? failed.stage}」：${failed.reason || '（没写原因，属于待补的静默分支）'}`
      } else if (list.some((e) => e.stage === 'wake' && e.ok)) {
        conclusion = '一路通过，已叫醒。'
      } else {
        conclusion = '一路通过（这条没触发任何组：可能没 @ 人，或发言人是机器人）。'
      }
      return {
        ok: !failed,
        chain,
        conclusion,
        durationMs: list.length > 1 ? list[list.length - 1].ts - list[0].ts : 0,
      }
    },

    /** 最近几条 trace 的轨迹（给快照/健康检查用） */
    recentTraces(limit = 20) {
      return [...traces.values()]
        .sort((a, b) => b.startedAt - a.startedAt)
        .slice(0, limit)
        .map((t) => ({ id: t.id, startedAt: t.startedAt, durationMs: t.durationMs ?? 0, stages: t.stages, meta: t.meta }))
    },
  }
}
