import { useCallback, useEffect, useRef, useState } from 'react'

// 推荐问题。空状态里点一下就直接问，省掉"不知道该问什么"的尴尬。
const SUGGESTIONS = [
  { title: '奖学金一等多少钱', hint: '奖助学金' },
  { title: '找算法实习的时间线怎么安排', hint: '实习就业' },
  { title: '延期毕业要办什么手续', hint: '学籍管理' },
  { title: '图书馆几点开门', hint: '校园生活' },
]

let seq = 0
const nextId = () => `m${++seq}`

// 模型会把"猜你想问"直接写进正文。这里把它切出来，渲染成可点的胶囊，
// 而不是一段看着像按钮、其实点不了的文字；反馈也只提交正文部分。
function splitFollowups(text) {
  const idx = text.indexOf('猜你想问')
  if (idx < 0) return { main: text, followups: [] }
  const main = text.slice(0, idx).trim()
  const tail = text.slice(idx)
  const followups = tail
    .split('\n')
    .map((l) => l.replace(/^[\s\d.、)）:：]+/, '').trim())
    .filter((l) => l && l !== '猜你想问' && !l.startsWith('猜你想问'))
  return { main, followups: followups.slice(0, 3) }
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [meta, setMeta] = useState(null) // 知识库规模，页头显示
  const [docsOpen, setDocsOpen] = useState(false) // 资料管理抽屉
  const [convId, setConvId] = useState('default') // 当前对话 id
  const [convs, setConvs] = useState([]) // 对话列表
  const [convsOpen, setConvsOpen] = useState(false) // 对话抽屉（移动端）
  const [view, setView] = useState('landing') // 'landing' 过渡页 | 'chat' 对话
  const abortRef = useRef(null)
  const bottomRef = useRef(null)
  const scrollerRef = useRef(null)

  // 拉一次知识库规模。拿不到就静默，页头不显示这块，不影响提问。
  const refreshMeta = useCallback(() => {
    fetch('/health')
      .then((r) => r.json())
      .then((d) => setMeta(d))
      .catch(() => {})
  }, [])
  useEffect(() => { refreshMeta() }, [refreshMeta])

  // 对话列表：随每轮问答结束刷新，标题取第一句提问。
  const refreshConvs = useCallback(() => {
    fetch('/api/conversations')
      .then((r) => r.json())
      .then((d) => {
        const server = d.conversations || []
        setConvs((prev) => {
          const ids = new Set(server.map((c) => c.id))
          // 刚发出、服务端还没落盘的对话先留着，下一轮刷新再收编
          const pending = prev.filter((c) => c.pending && !ids.has(c.id))
          return [...server, ...pending]
        })
      })
      .catch(() => {})
  }, [])
  useEffect(() => { refreshConvs() }, [refreshConvs])

  // 切到某段旧对话：把历史读回来铺到界面。
  // 命中块和点赞是"展示态"，服务端只存问答文本，所以从 localStorage 恢复。
  const switchConv = useCallback(async (id) => {
    if (busy) return
    setConvsOpen(false)
    setConvId(id)
    try {
      const r = await fetch('/api/conversations/' + encodeURIComponent(id) + '/messages')
      const d = await r.json()
      let saved = []
      try {
        saved = JSON.parse(localStorage.getItem('extras:' + id) || '[]')
      } catch (e) {
        saved = []
      }
      let ai = 0
      const list = (d.messages || []).map((m) => {
        const isAssistant = m.role === 'assistant'
        const extra = isAssistant ? (saved[ai++] || {}) : {}
        return {
          id: nextId(),
          role: m.role,
          text: m.content,
          streaming: false,
          hits: extra.hits || undefined,
          feedback: extra.feedback || undefined,
        }
      })
      setMessages(list)
    } catch (e) {
      setMessages([])
    }
  }, [busy])

  // 新对话：前端生成 id，第一条消息发出后才会出现在列表里。
  const newConv = useCallback(() => {
    setConvsOpen(false)
    setMessages([])
    setConvId('c' + Date.now().toString(36))
  }, [])

  const startChat = useCallback(() => setView('chat'), [])

  const deleteConv = useCallback(
    async (id) => {
      await fetch('/api/conversations/' + encodeURIComponent(id), { method: 'DELETE' })
      refreshConvs()
      if (id === convId) {
        setMessages([])
        setConvId('default')
      }
    },
    [convId, refreshConvs]
  )

  // 贴底滚动：只在用户本来就在底部附近时才自动滚，
  // 否则往上翻历史的时候会被强行拽回去。
  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160
    if (nearBottom) bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  // 命中块/点赞属于展示态，服务端只存问答文本；按对话存 localStorage，切回来能还原
  useEffect(() => {
    const extras = messages
      .filter((m) => m.role === 'assistant')
      .map((m) => ({ hits: m.hits || null, feedback: m.feedback || null }))
    if (!extras.some((e) => e.hits || e.feedback)) return
    try {
      localStorage.setItem('extras:' + convId, JSON.stringify(extras))
    } catch (e) {
      /* 隐私模式下 localStorage 不可用，忽略 */
    }
  }, [messages, convId])

  const patch = useCallback((id, updater) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...updater(m) } : m)))
  }, [])

  const ask = useCallback(
    async (raw) => {
      const question = raw.trim()
      if (!question || busy) return

      const uid = nextId()
      const aid = nextId()
      setMessages((prev) => [
        ...prev,
        { id: uid, role: 'user', text: question },
        { id: aid, role: 'assistant', text: '', status: '正在检索资料…', streaming: true, question },
      ])
      // 第一句话就先把这段对话插进左边列表，不等回答完 —— "开始对话"时历史立刻更新
      setConvs((prev) => {
        if (prev.some((c) => c.id === convId)) return prev
        return [{
          id: convId,
          title: question.slice(0, 24),
          turns: 1,
          updated: Date.now() / 1000,
          pending: true,
        }, ...prev]
      })
      setBusy(true)

      const ctrl = new AbortController()
      abortRef.current = ctrl

      // SSE 事件处理。和旧版协议一致：status / hits / delta / reset / done / error
      const handle = (ev) => {
        if (ev.type === 'status') {
          patch(aid, () => ({ status: ev.text || '' }))
        } else if (ev.type === 'hits') {
          // 命中块先到，答案还没开始吐。先渲染出来，用户马上知道"找到了什么"。
          patch(aid, () => ({ hits: ev.hits || [] }))
        } else if (ev.type === 'delta') {
          patch(aid, (m) => ({ text: m.text + (ev.text || ''), status: '' }))
        } else if (ev.type === 'reset') {
          // 模型先说了一段又转去调工具，那段是思考不是答案，撤回
          patch(aid, () => ({ text: '' }))
        } else if (ev.type === 'done') {
          patch(aid, (m) => ({ text: ev.answer || m.text, status: '', streaming: false, requestId: ev.request_id || '' }))
          refreshConvs()
        } else if (ev.type === 'error') {
          patch(aid, () => ({ text: '', status: '', streaming: false, error: ev.error || '服务暂时不可用' }))
        }
      }

      try {
        const resp = await fetch('/ask_stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question, conv_id: convId }),
          signal: ctrl.signal,
        })

        if (!resp.ok || !resp.body) {
          let msg = `请求失败（${resp.status}）`
          try {
            const j = await resp.json()
            msg = j.error || msg
          } catch (e) {
            /* 响应不是 JSON，用默认文案 */
          }
          patch(aid, () => ({ text: '', status: '', streaming: false, error: msg }))
          return
        }

        const reader = resp.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let pending = ''

        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          pending += decoder.decode(value, { stream: true })

          let cut
          while ((cut = pending.indexOf('\n\n')) >= 0) {
            const chunk = pending.slice(0, cut)
            pending = pending.slice(cut + 2)
            for (const line of chunk.split('\n')) {
              if (!line.startsWith('data:')) continue
              try {
                handle(JSON.parse(line.slice(5).trim()))
              } catch (e) {
                /* 单条事件解析失败不影响后续 */
              }
            }
          }
        }
      } catch (err) {
        if (err.name === 'AbortError') {
          patch(aid, (m) => ({ streaming: false, status: '', text: m.text || '（已停止）' }))
        } else {
          patch(aid, () => ({ streaming: false, status: '', error: '连接中断，请重试' }))
        }
      } finally {
        // 兜底：无论走哪条路，占位消息都不能停在「流式中」
        patch(aid, (m) => (m.streaming ? { ...m, streaming: false, status: '' } : m))
        setBusy(false)
        abortRef.current = null
      }
    },
    [busy, patch, convId, refreshConvs]
  )

  const stop = () => {
    abortRef.current?.abort()
  }

  const clear = async () => {
    try {
      await fetch('/clear', { method: 'POST' })
    } finally {
      setMessages([])
    }
  }

  // 点赞/点踩：先本地标记（立刻有反馈），再异步提交。失败不打扰用户。
  const sendFeedback = useCallback(
    async (msg, rating) => {
      if (!msg || msg.feedback) return
      patch(msg.id, () => ({ feedback: rating }))
      try {
        await fetch('/api/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            rating,
            question: msg.question || '',
            answer: splitFollowups(msg.text || '').main,
            request_id: msg.requestId || '',
          }),
        })
      } catch (e) {
        /* 记录失败不影响阅读，bad case 少一条而已 */
      }
    },
    [patch]
  )

  const empty = messages.length === 0

  // 过渡页：第一次进来先给一个介绍页，点"开始对话"才进聊天
  if (view === 'landing') {
    return (
      <Landing
        convs={convs}
        onStart={startChat}
        onResume={(id) => { startChat(); switchConv(id) }}
      />
    )
  }

  return (
    <div className="relative flex h-full overflow-hidden overflow-x-hidden">
      {/* 背景层：光斑 + 网格，都设 pointer-events-none */}
      <div className="pointer-events-none fixed inset-0 -z-10">
        <div className="absolute -top-52 left-1/2 h-[560px] w-[900px] -translate-x-1/2 rounded-full bg-cyan/[0.16] blur-[130px]" />
        <div className="absolute -top-20 right-[-10%] h-[420px] w-[560px] rounded-full bg-violet/[0.16] blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[-5%] h-[320px] w-[480px] rounded-full bg-violet/[0.07] blur-[120px]" />
      </div>
      <div className="grid-layer -z-10" />

      {/* 左侧常驻对话列表（桌面端，md 以上显示） */}
      <Sidebar
        className="hidden md:flex"
        convs={convs}
        current={convId}
        onNew={newConv}
        onSwitch={switchConv}
        onDelete={deleteConv}
        onOpenDocs={() => setDocsOpen(true)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <Header meta={meta} onClear={clear} hasMessages={!empty} onOpenDocs={() => setDocsOpen(true)} onOpenConvs={() => setConvsOpen(true)} />

        <main ref={scrollerRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-5 pb-8 pt-4">
            {empty ? (
              <EmptyState onPick={ask} />
            ) : (
              <div className="space-y-7 pt-4">
                {messages.map((m) => (
                  <Message key={m.id} msg={m} onFeedback={sendFeedback} onAsk={ask} />
                ))}
                <div ref={bottomRef} className="h-1" />
              </div>
            )}
          </div>
        </main>

        <Composer busy={busy} onSend={ask} onStop={stop} />
      </div>

      <DocsPanel open={docsOpen} onClose={() => setDocsOpen(false)} onChanged={refreshMeta} />
      <ConvsPanel
        open={convsOpen}
        onClose={() => setConvsOpen(false)}
        convs={convs}
        current={convId}
        onSwitch={switchConv}
        onNew={newConv}
        onDelete={deleteConv}
      />
    </div>
  )
}

/* ------------------------------------------------------------------ */

function Header({ meta, onClear, hasMessages, onOpenDocs, onOpenConvs }) {
  const blocks = meta?.blocks
  const docCount = meta?.docs
  const ready = meta ? meta.status !== 'degraded' : null

  return (
    <header className="relative z-10 shrink-0 border-b border-line/80 bg-ink/70 backdrop-blur-xl">
      <div className="top-line absolute inset-x-0 top-0 h-px" />
      <div className="mx-auto flex h-14 w-full max-w-3xl items-center justify-between px-5">
        <div className="flex items-center gap-2.5">
          {/* 桌面端左侧栏已经有 logo，顶栏再来一个就重复了，只在移动端显示 */}
          <div className="grid h-6 w-6 place-items-center rounded-[7px] bg-gradient-to-br from-cyan to-violet text-[11px] font-bold text-ink md:hidden">
            C
          </div>
          <span className="text-[14px] font-medium tracking-wide text-white/90 md:hidden">校园问答</span>
          <span className="hidden font-mono text-[11px] text-dim md:inline">campus-agent</span>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={onOpenConvs}
            title="对话记录"
            className="rounded-md border border-line px-2.5 py-1 text-[11px] text-dim transition hover:border-white/20 hover:text-white/80 md:hidden"
          >
            对话
          </button>
          {blocks ? (
            <button
              onClick={onOpenDocs}
              title="管理资料"
              className="flex items-center gap-1.5 rounded-md px-1.5 py-1 font-mono text-[11px] text-dim transition hover:bg-white/5 hover:text-white/80"
            >
              <span className="text-white/60">{blocks}</span> 块
              {docCount != null ? <span className="text-dim/60">· {docCount} 份</span> : null}
            </button>
          ) : null}
          <span className="flex items-center gap-1.5 font-mono text-[11px] text-dim">
            <span
              className={
                'h-1.5 w-1.5 rounded-full ' +
                (ready === false ? 'bg-amber-400' : 'animate-breathe bg-cyan')
              }
            />
            {ready === false ? '降级' : '在线'}
          </span>
          {hasMessages ? (
            <button
              onClick={onClear}
              className="rounded-md border border-line px-2.5 py-1 text-[11px] text-dim transition hover:border-white/20 hover:text-white/80"
            >
              清空
            </button>
          ) : null}
        </div>
      </div>
    </header>
  )
}

function EmptyState({ onPick }) {
  return (
    <div className="my-auto animate-rise">
      <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-line bg-white/[0.03] px-3 py-1 font-mono text-[11px] text-dim">
        <span className="h-1 w-1 rounded-full bg-cyan" />
        校内资料检索 · 逐字生成
      </div>

      <h1 className="text-[38px] font-semibold leading-[1.2] tracking-[-0.02em] text-white sm:text-[54px]">
        北交校园，
        <span className="bg-gradient-to-r from-cyan via-[#9d8bff] to-violet bg-clip-text text-transparent">
          什么都能问
        </span>
      </h1>

      {/* 一条短渐变线，替代副标题的位置，视觉上把标题和说明分开 */}
      <div className="mt-5 h-px w-24 bg-gradient-to-r from-cyan/70 to-transparent" />

      <p className="mt-4 max-w-lg text-[14px] leading-relaxed text-dim">
        选课、奖助、实习、毕业。答案全部来自校内资料，找不到就说找不到。
      </p>

      <div className="mt-10 grid gap-2.5 sm:grid-cols-2">
        {SUGGESTIONS.map((s, i) => (
          <button
            key={s.title}
            onClick={() => onPick(s.title)}
            style={{ animationDelay: `${60 + i * 55}ms` }}
            className="group animate-rise rounded-xl border border-line bg-white/[0.025] px-4 py-3.5 text-left transition hover:-translate-y-0.5 hover:border-cyan/35 hover:bg-white/[0.05]"
          >
            <div className="text-[13.5px] text-white/85">{s.title}</div>
            <div className="mt-0.5 font-mono text-[10.5px] text-dim group-hover:text-cyan/70">{s.hint}</div>
          </button>
        ))}
      </div>
    </div>
  )
}

function Message({ msg, onFeedback, onAsk }) {
  if (msg.role === 'user') {
    return (
      <div className="flex animate-rise justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md border border-white/10 bg-white/[0.055] px-4 py-2.5 text-[14.5px] leading-relaxed text-white/95">
          {msg.text}
        </div>
      </div>
    )
  }

  const { main, followups } = splitFollowups(msg.text || '')

  return (
    <div className="flex animate-rise gap-3">
      <div className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-[7px] bg-gradient-to-br from-cyan to-violet text-[10px] font-bold text-ink">
        AI
      </div>
      <div className="min-w-0 flex-1">
        {msg.status ? (
          <div className="flex items-center gap-2 font-mono text-[12px] text-dim">
            <span className="flex gap-1">
              <i className="h-1 w-1 animate-bounce rounded-full bg-cyan [animation-delay:0ms]" />
              <i className="h-1 w-1 animate-bounce rounded-full bg-cyan [animation-delay:120ms]" />
              <i className="h-1 w-1 animate-bounce rounded-full bg-cyan [animation-delay:240ms]" />
            </span>
            {msg.status}
          </div>
        ) : null}

        {main ? (
          <div
            className={
              'answer whitespace-pre-wrap break-words text-[14.5px] leading-[1.8] text-[#dfe5f0]' +
              (msg.streaming ? ' caret' : '')
            }
          >
            {main}
          </div>
        ) : null}

        {/* "猜你想问"渲染成可点的胶囊，点了直接接着问 */}
        {followups.length && !msg.streaming ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {followups.map((q, i) => (
              <button
                key={i}
                onClick={() => onAsk && onAsk(q)}
                className="rounded-lg border border-line bg-white/[0.03] px-2.5 py-1.5 text-left text-[12px] text-white/75 transition hover:border-cyan/35 hover:text-white"
              >
                {q}
              </button>
            ))}
          </div>
        ) : null}

        {/* 流式刚开始、一个字都还没到的时候给个光标，避免一片空白 */}
        {msg.streaming && !msg.text && !msg.status ? (
          <div className="caret h-5 text-[14.5px] text-[#dfe5f0]" />
        ) : null}

        {msg.error ? (
          <div className="mt-1 rounded-lg border border-red-500/25 bg-red-500/[0.07] px-3 py-2 text-[13px] text-red-300/90">
            {msg.error}
          </div>
        ) : null}

        {msg.text && !msg.streaming ? (
          <div className="mt-2 flex items-center gap-2">
            {['up', 'down'].map((r) => (
              <button
                key={r}
                onClick={() => onFeedback && onFeedback(msg, r)}
                disabled={!!msg.feedback}
                className={
                  'rounded-md border px-2 py-1 text-[11px] transition ' +
                  (msg.feedback === r
                    ? 'border-cyan/40 bg-cyan/10 text-cyan/90'
                    : 'border-line text-dim hover:border-white/20 hover:text-white/70') +
                  (msg.feedback ? ' cursor-default' : '')
                }
              >
                {r === 'up' ? '👍 有用' : '👎 没用'}
              </button>
            ))}
            {msg.feedback ? (
              <span className="font-mono text-[10.5px] text-dim/60">已记录</span>
            ) : null}
          </div>
        ) : null}

        {msg.hits && msg.hits.length ? (
          <div className="mt-3 space-y-1.5">
            <div className="font-mono text-[10.5px] text-dim/60">参考 {msg.hits.length} 条资料</div>
            {msg.hits.map((h) => (
              <details
                key={h.rank}
                className="rounded-lg border border-line bg-white/[0.02] px-3 py-2 transition hover:border-white/15"
              >
                <summary className="cursor-pointer list-none text-[12.5px] leading-relaxed text-white/75">
                  <span className="mr-1.5 font-mono text-cyan/70">[{h.rank}]</span>
                  {h.title}
                  <span className="ml-2 font-mono text-[10px] text-dim/60">{h.score}</span>
                </summary>
                <p className="mt-1.5 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-dim">
                  {h.text}
                </p>
              </details>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function Composer({ busy, onSend, onStop }) {
  const [value, setValue] = useState('')
  const taRef = useRef(null)

  // textarea 自适应高度，最多 5 行
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 132)}px`
  }, [value])

  const submit = () => {
    if (!value.trim() || busy) return
    onSend(value)
    setValue('')
  }

  const onKeyDown = (e) => {
    // isComposing：中文输入法选词时的回车不能当发送
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="relative shrink-0">
      {/* 底部渐隐，让内容滚到边上时不硬切 */}
      <div className="pointer-events-none absolute inset-x-0 -top-16 h-16 bg-gradient-to-t from-ink to-transparent" />
      <div className="mx-auto w-full max-w-3xl px-5 pb-5 pt-1">
        <div className="input-glow flex items-end gap-2 rounded-2xl border border-line bg-white/[0.035] p-2 pl-4 backdrop-blur-xl transition">
          <textarea
            ref={taRef}
            rows={1}
            value={value}
            maxLength={200}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="问一个校内问题…"
            className="min-h-[26px] flex-1 resize-none bg-transparent py-1 text-[14.5px] leading-relaxed text-white/95 outline-none placeholder:text-dim/70"
          />
          {busy ? (
            <button
              onClick={onStop}
              title="停止生成"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line bg-white/[0.04] text-white/70 transition hover:border-white/25 hover:text-white"
            >
              <span className="h-2.5 w-2.5 rounded-[2px] bg-current" />
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!value.trim()}
              title="发送"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-cyan to-violet text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:brightness-100"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 19V5M12 5l-6 6M12 5l6 6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between px-1 font-mono text-[10.5px] text-dim/70">
          <span>Enter 发送 · Shift + Enter 换行</span>
          <span>{value.length}/200</span>
        </div>
      </div>
    </div>
  )
}

/* 资料管理抽屉：上传 .md/.txt、看块数、删除上传文档。
   内置 knowledge.md 只读，不显示删除按钮。 */
function DocsPanel({ open, onClose, onChanged }) {
  const [docs, setDocs] = useState([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const fileRef = useRef(null)

  const load = useCallback(() => {
    fetch('/api/docs')
      .then((r) => r.json())
      .then((d) => setDocs(d.docs || []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (open) { setMsg(''); setErr(''); load() }
  }, [open, load])

  const upload = async (e) => {
    const f = e.target.files && e.target.files[0]
    if (!f) return
    setBusy(true); setMsg(''); setErr('')
    const fd = new FormData()
    fd.append('file', f)
    try {
      const r = await fetch('/api/docs', { method: 'POST', body: fd })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.error || '上传失败')
      setMsg(j.message || '上传成功')
      setDocs(j.docs || [])
      onChanged && onChanged()
    } catch (ex) {
      setErr(ex.message || '上传失败')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const del = async (name) => {
    setBusy(true); setMsg(''); setErr('')
    try {
      const r = await fetch('/api/docs/' + encodeURIComponent(name), { method: 'DELETE' })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.error || '删除失败')
      setMsg(j.message || '已删除')
      setDocs(j.docs || [])
      onChanged && onChanged()
    } catch (ex) {
      setErr(ex.message || '删除失败')
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-md flex-col border-l border-line bg-ink">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <div className="text-[15px] font-medium text-white/90">资料管理</div>
            <div className="mt-0.5 font-mono text-[10.5px] text-dim">.md / .txt，上传后自动切块入库</div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md border border-line px-2.5 py-1 text-[11px] text-dim transition hover:border-white/20 hover:text-white/80"
          >
            关闭
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          <label
            className={
              'block cursor-pointer rounded-xl border border-dashed border-line px-4 py-6 text-center transition hover:border-cyan/40 ' +
              (busy ? 'pointer-events-none opacity-50' : '')
            }
          >
            <input
              ref={fileRef}
              type="file"
              accept=".md,.txt"
              className="hidden"
              onChange={upload}
              disabled={busy}
            />
            <div className="text-[13px] text-white/80">{busy ? '处理中…' : '点击选择文件上传'}</div>
            <div className="mt-1 font-mono text-[10.5px] text-dim">支持 .md / .txt，≤ 512KB</div>
          </label>

          {msg ? (
            <div className="mt-3 rounded-lg border border-cyan/25 bg-cyan/[0.06] px-3 py-2 text-[12.5px] text-cyan/90">
              {msg}
            </div>
          ) : null}
          {err ? (
            <div className="mt-3 rounded-lg border border-red-500/25 bg-red-500/[0.07] px-3 py-2 text-[12.5px] text-red-300/90">
              {err}
            </div>
          ) : null}

          <div className="mt-5 space-y-1.5">
            {docs.map((d) => (
              <div
                key={d.name}
                className="flex items-center justify-between rounded-lg border border-line bg-white/[0.02] px-3 py-2.5"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 truncate text-[13px] text-white/85">
                    {d.name}
                    {d.builtin ? (
                      <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[9.5px] text-dim">内置</span>
                    ) : null}
                  </div>
                  <div className="mt-0.5 font-mono text-[10.5px] text-dim">
                    {d.blocks} 块 · {Math.max(1, Math.round((d.size || 0) / 1024))} KB
                  </div>
                </div>
                {d.builtin ? null : (
                  <button
                    onClick={() => del(d.name)}
                    disabled={busy}
                    className="ml-3 shrink-0 rounded-md border border-line px-2 py-1 text-[11px] text-dim transition hover:border-red-500/40 hover:text-red-300 disabled:opacity-40"
                  >
                    删除
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      </aside>
    </div>
  )
}

/* 对话记录抽屉：同一浏览器下的多段对话，可切换 / 新建 / 删除。 */
function ConvsPanel({ open, onClose, convs, current, onSwitch, onNew, onDelete }) {
  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-xs flex-col border-r border-line bg-ink">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <div className="text-[15px] font-medium text-white/90">对话记录</div>
            <div className="mt-0.5 font-mono text-[10.5px] text-dim">同一浏览器下可有多段对话</div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md border border-line px-2.5 py-1 text-[11px] text-dim transition hover:border-white/20 hover:text-white/80"
          >
            关闭
          </button>
        </div>

        <div className="p-3">
          <button
            onClick={onNew}
            className="w-full rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2 text-[13px] text-cyan/90 transition hover:bg-cyan/[0.1]"
          >
            + 新对话
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 pb-4">
          <ConvList convs={convs} current={current} onSwitch={onSwitch} onDelete={onDelete} />
        </div>
      </aside>
    </div>
  )
}

/* 对话列表：桌面侧边栏和移动端抽屉共用同一份 UI */
function ConvList({ convs, current, onSwitch, onDelete }) {
  if (convs.length === 0) {
    return (
      <div className="px-2 py-6 text-center font-mono text-[11px] text-dim/60">
        还没有对话，问一个问题就会出现
      </div>
    )
  }
  return (
    <div className="space-y-1">
      {convs.map((c) => (
        <div
          key={c.id}
          className={
            'group flex items-center gap-2 rounded-lg border px-2.5 py-2 transition ' +
            (c.id === current
              ? 'border-cyan/30 bg-cyan/[0.06]'
              : 'border-transparent hover:border-line hover:bg-white/[0.03]')
          }
        >
          <button onClick={() => onSwitch(c.id)} className="min-w-0 flex-1 text-left">
            <div className="truncate text-[12.5px] text-white/85">{c.title || '新对话'}</div>
            <div className="mt-0.5 font-mono text-[10px] text-dim/60">
              {c.turns || 1} 轮 · {new Date((c.updated || 0) * 1000).toLocaleDateString()}
            </div>
          </button>
          <button
            onClick={() => onDelete(c.id)}
            title="删除这段对话"
            className="shrink-0 rounded px-1.5 py-0.5 text-[10.5px] text-dim/50 opacity-0 transition hover:text-red-300 group-hover:opacity-100"
          >
            删除
          </button>
        </div>
      ))}
    </div>
  )
}

/* 桌面端常驻左侧栏 */
function Sidebar({ className, convs, current, onNew, onSwitch, onDelete, onOpenDocs }) {
  return (
    <aside className={'w-60 shrink-0 flex-col border-r border-line bg-ink/60 backdrop-blur-xl ' + (className || '')}>
      <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
        <div className="grid h-6 w-6 place-items-center rounded-[7px] bg-gradient-to-br from-cyan to-violet text-[11px] font-bold text-ink">
          C
        </div>
        <span className="text-[13.5px] font-medium tracking-wide text-white/90">校园问答</span>
      </div>

      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2 text-[13px] text-cyan/90 transition hover:bg-cyan/[0.1]"
        >
          + 新对话
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-4">
        <div className="mb-2 px-1 font-mono text-[10px] uppercase tracking-wider text-dim/50">对话记录</div>
        <ConvList convs={convs} current={current} onSwitch={onSwitch} onDelete={onDelete} />
      </div>

      <div className="border-t border-line p-3">
        <button
          onClick={onOpenDocs}
          className="w-full rounded-md border border-line px-3 py-2 text-[11.5px] text-dim transition hover:border-white/20 hover:text-white/80"
        >
          资料管理
        </button>
      </div>
    </aside>
  )
}

/* 过渡页：第一次进来先看介绍，点"开始对话"再进聊天 */
function Landing({ convs, onStart, onResume }) {
  const [stats, setStats] = useState(null)

  useEffect(() => {
    fetch('/api/stats')
      .then((r) => r.json())
      .then(setStats)
      .catch(() => {})
  }, [])

  const blocks = stats?.blocks
  const docCount = stats?.docs
  const questions = stats?.questions
  const ev = stats?.eval || {}
  const scored = ev.scored_questions
  const top1 = ev.top1

  const cards = [
    {
      k: blocks != null ? String(blocks) : '—',
      v: '块知识',
      d: docCount != null ? `${docCount} 份文档` : '培养 / 选课 / 实习 …',
    },
    {
      k: questions != null ? String(questions) : '—',
      v: '道评测题',
      d: scored != null ? `${scored} 道计检索指标` : '含陷阱、注入、跨块',
    },
    {
      // 和 README 的 96.0% 保持同样精度，别显示成 96%
      k: top1 != null && top1 !== '-' ? `${Number(top1).toFixed(1)}%` : '—',
      v: 'Top-1 命中',
      d: scored != null ? `${scored} 道计分题口径` : '宽召回 + RRF + rerank',
    },
  ]

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <div className="pointer-events-none fixed inset-0 -z-10">
        <div className="absolute -top-52 left-1/2 h-[560px] w-[900px] -translate-x-1/2 rounded-full bg-cyan/[0.16] blur-[130px]" />
        <div className="absolute -top-20 right-[-10%] h-[420px] w-[560px] rounded-full bg-violet/[0.16] blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[-5%] h-[320px] w-[480px] rounded-full bg-violet/[0.07] blur-[120px]" />
      </div>
      <div className="grid-layer -z-10" />

      <header className="relative z-10 flex h-14 items-center px-6">
        <div className="flex items-center gap-2.5">
          <div className="grid h-6 w-6 place-items-center rounded-[7px] bg-gradient-to-br from-cyan to-violet text-[11px] font-bold text-ink">C</div>
          <span className="text-[13.5px] font-medium tracking-wide text-white/90">校园问答</span>
        </div>
      </header>

      <main className="relative z-10 flex flex-1 items-center justify-center px-6">
        <div className="w-full max-w-2xl text-center">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-line bg-white/[0.03] px-3 py-1 font-mono text-[11px] text-dim">
            <span className="h-1 w-1 rounded-full bg-cyan" />
            校内资料检索 · 引用溯源 · 查不到就说查不到
          </div>

          <h1 className="text-[38px] font-semibold leading-[1.15] tracking-[-0.02em] text-white sm:text-[54px]">
            北交校园，
            <span className="bg-gradient-to-r from-cyan via-[#9d8bff] to-violet bg-clip-text text-transparent">
              什么都能问
            </span>
          </h1>

          <p className="mx-auto mt-5 max-w-lg text-[14px] leading-relaxed text-dim">
            培养方案、选课、实习、校招、论文、奖学金。答案全部来自校内资料，
            每条结论都能点开看原文。
          </p>

          <div className="mt-10 flex flex-col items-center gap-3">
            <button
              onClick={onStart}
              className="rounded-xl bg-gradient-to-r from-cyan to-violet px-8 py-3 text-[15px] font-medium text-ink transition hover:brightness-110"
            >
              开始对话 →
            </button>
            {convs.length ? (
              <button
                onClick={() => onResume(convs[0].id)}
                className="max-w-xs truncate text-[12.5px] text-dim transition hover:text-white/80"
              >
                继续上次对话：{convs[0].title}
              </button>
            ) : null}
          </div>

          <div className="mt-14 grid gap-3 text-left sm:grid-cols-3">
            {cards.map((x) => (
              <div key={x.v} className="rounded-xl border border-line bg-white/[0.025] px-4 py-3.5">
                <div className="text-[22px] font-semibold text-white/90">{x.k}</div>
                <div className="mt-0.5 text-[12px] text-white/70">{x.v}</div>
                <div className="mt-1 font-mono text-[10.5px] text-dim/70">{x.d}</div>
              </div>
            ))}
          </div>
        </div>
      </main>

      <footer className="relative z-10 py-6 text-center font-mono text-[10.5px] text-dim/50">
        答案来自知识库，找不到会直接说找不到
      </footer>
    </div>
  )
}
