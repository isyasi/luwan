import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const WHALE_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SCREEN_STATE_PATH = join(WHALE_ROOT, 'screen_state.json')
const SCREEN_STATE_FRESH_MS = 5000

const readScreenStateBlock = () => {
  try {
    const raw = readFileSync(SCREEN_STATE_PATH, 'utf8')
    const data = JSON.parse(raw)
    if (!data || !data.ok) return ''
    const ts = Number(data.ts) || 0
    if (ts && (Date.now() / 1000 - ts) > SCREEN_STATE_FRESH_MS / 1000) return ''
    const summary = String(data.summary || '').trim()
    if (!summary) return ''
    const detail = String(data.detail || '').trim()
    const body = detail ? `${summary}\n${detail}` : summary
    return '[CURRENT_SCREEN_STATE]\n' + body.slice(0, 400)
  } catch {
    return ''
  }
}

export const name = 'whale-pet-bridge'
export const inject = ['webServer', 'sessionController', 'sessions']

// 从一条 assistant/message 事件里把「念得出来的文字」拼出来。
// 兼容三种形态：完整 content、data.stream 里的 text-chunks、message.text 兜底。
const textOfEvent = (ev) => {
  if (!ev || ev.type !== 'assistant/message') return ''
  const d = ev.data || {}
  const m = d.message || {}
  const parts = []
  if (Array.isArray(m.content)) {
    for (const b of m.content) {
      if (b && b.type === 'text' && typeof b.text === 'string' && b.text.trim()) parts.push(b.text.trim())
    }
  }
  if (!parts.length && typeof m.text === 'string' && m.text.trim()) parts.push(m.text.trim())
  // 恢复过的会话文字躺在 stream 的 text-chunks 里
  if (Array.isArray(d.stream)) {
    for (const r of d.stream) {
      if (r && r.type === 'text-chunks' && Array.isArray(r.texts)) {
        for (const t of r.texts) if (typeof t === 'string' && t.trim()) parts.push(t.trim())
      }
    }
  }
  return parts.join('\n\n')
}

export function apply(ctx) {
  const reply = (res, code, body) => {
    res.writeHead(code, {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'})
    res.end(JSON.stringify(body))
  }

  // 每个会话的「最后已消费序号」+「当前这一轮已累计的回复文本」。
  // 内存态即可：桌宠进程重启后会重新发消息、重新从头跟。
  const cursors = new Map() // sessionId -> { seq: number, text: string, finished: boolean }

  ctx.effect(() => ctx.webServer.register({kind: 'exact', path: '/whale-pet/status', handler: (req, res) => {
    reply(res, 200, {ok: true, bridge: 'whale-pet'})
  }}))

  ctx.effect(() => ctx.webServer.register({kind: 'exact', path: '/whale-pet/send', handler: async (req, res) => {
    if (req.method !== 'POST') return reply(res, 405, {ok: false, error: 'POST required'})
    if (req.headers.origin) return reply(res, 403, {ok: false, error: 'Browser-origin requests are not accepted'})
    if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress)) return reply(res, 403, {ok: false})
    try {
      let size = 0
      const chunks = []
      for await (const chunk of req) {
        size += chunk.length
        if (size > 32768) return reply(res, 413, {ok: false, error: 'Message too large'})
        chunks.push(chunk)
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      const text = typeof body.text === 'string' ? body.text.trim() : ''
      const sessionId = typeof body.sessionId === 'string' ? body.sessionId : ''
      if (!text || !/^session-[a-zA-Z0-9-]+$/.test(sessionId)) return reply(res, 400, {ok: false, error: 'Invalid message or session'})
      // 先把光标推进到「这条消息发出前」的序号，这样 /reply 只会看到这轮之后的回复。
      try {
        const s = ctx.sessions.get(sessionId)
        const baseline = s ? Number(s.seq) : 0
        cursors.set(sessionId, { seq: baseline, text: '', finished: false })
      } catch {}
      const requestId = randomUUID()
      const screenBlock = readScreenStateBlock()
      const promptText = (screenBlock && !text.includes('[CURRENT_SCREEN_STATE]')) ? (screenBlock + '\n\n' + text) : text
      await ctx.sessionController.prompt({requestId, sessionId, mode: 'queue', content: [{type: 'text', text: promptText}]}, new AbortController().signal)
      reply(res, 200, {ok: true, requestId})
    } catch {
      reply(res, 500, {ok: false, error: 'DSH did not accept the message; check the selected conversation'})
    }
  }}))

  // 流式首句即读：桌宠发完消息后轮询这里，拿「这一轮已经生成的全部文本」。
  // 一旦有字就返回 text，桌宠立刻开合成；finished 表示这一轮彻底结束。
  ctx.effect(() => ctx.webServer.register({kind: 'exact', path: '/whale-pet/reply', handler: (req, res) => {
    if (req.method !== 'GET') return reply(res, 405, {ok: false, error: 'GET required'})
    if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress)) return reply(res, 403, {ok: false})
    const url = new URL(req.url, 'http://127.0.0.1')
    const sessionId = url.searchParams.get('sessionId') || ''
    if (!/^session-[a-zA-Z0-9-]+$/.test(sessionId)) return reply(res, 400, {ok: false, error: 'Invalid session'})
    try {
      const s = ctx.sessions.get(sessionId)
      if (s === undefined) return reply(res, 404, {ok: false, error: 'session not found'})
      const cur = cursors.get(sessionId) || { seq: Number(s.seq), text: '', finished: false }
      const nowSeq = Number(s.seq)
      // 有新事件就增量吸收
      if (nowSeq > cur.seq) {
        let events = []
        try {
          events = s.snapshotEvents(cur.seq) || []
        } catch {}
        let finished = cur.finished
        let acc = cur.text
        for (const ev of events) {
          const t = ev && ev.type ? String(ev.type) : ''
          if (t === 'assistant/message') {
            const chunk = textOfEvent(ev)
            if (chunk) acc = acc ? acc + '\n\n' + chunk : chunk
          } else if (t === 'turn/end') {
            const reason = ev.data && ev.data.reason
            if (reason && reason.kind === 'completed') finished = true
          }
        }
        cur.seq = nowSeq
        cur.text = acc
        cur.finished = finished
        cursors.set(sessionId, cur)
      }
      reply(res, 200, { ok: true, text: cur.text, finished: cur.finished, seq: nowSeq })
    } catch (error) {
      reply(res, 500, { ok: false, error: String(error && error.message) })
    }
  }}))
}
