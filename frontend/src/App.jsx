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

export default function App() {
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [meta, setMeta] = useState(null) // 知识库规模，页头显示
  const abortRef = useRef(null)
  const bottomRef = useRef(null)
  const scrollerRef = useRef(null)

  // 拉一次知识库规模。拿不到就静默，页头不显示这块，不影响提问。
  useEffect(() => {
    fetch('/health')
      .then((r) => r.json())
      .then((d) => setMeta(d))
      .catch(() => {})
  }, [])

  // 贴底滚动：只在用户本来就在底部附近时才自动滚，
  // 否则往上翻历史的时候会被强行拽回去。
  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160
    if (nearBottom) bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

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
        { id: aid, role: 'assistant', text: '', status: '正在检索资料…', streaming: true },
      ])
      setBusy(true)

      const ctrl = new AbortController()
      abortRef.current = ctrl

      // SSE 事件处理。和旧版协议一致：status / hits / delta / reset / done / error
      const handle = (ev) => {
        if (ev.type === 'status') {
          patch(aid, () => ({ status: ev.text || '' }))
        } else if (ev.type === 'delta') {
          patch(aid, (m) => ({ text: m.text + (ev.text || ''), status: '' }))
        } else if (ev.type === 'reset') {
          // 模型先说了一段又转去调工具，那段是思考不是答案，撤回
          patch(aid, () => ({ text: '' }))
        } else if (ev.type === 'done') {
          patch(aid, (m) => ({ text: ev.answer || m.text, status: '', streaming: false }))
        } else if (ev.type === 'error') {
          patch(aid, () => ({ text: '', status: '', streaming: false, error: ev.error || '服务暂时不可用' }))
        }
      }

      try {
        const resp = await fetch('/ask_stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question }),
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
    [busy, patch]
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

  const empty = messages.length === 0

  return (
    <div className="relative flex h-full flex-col overflow-hidden overflow-x-hidden">
      {/* 背景层：光斑 + 网格，都设 pointer-events-none */}
      <div className="pointer-events-none fixed inset-0 -z-10">
        <div className="absolute -top-52 left-1/2 h-[560px] w-[900px] -translate-x-1/2 rounded-full bg-cyan/[0.16] blur-[130px]" />
        <div className="absolute -top-20 right-[-10%] h-[420px] w-[560px] rounded-full bg-violet/[0.16] blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[-5%] h-[320px] w-[480px] rounded-full bg-violet/[0.07] blur-[120px]" />
      </div>
      <div className="grid-layer -z-10" />

      <Header meta={meta} onClear={clear} hasMessages={!empty} />

      <main ref={scrollerRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-5 pb-8 pt-4">
          {empty ? (
            <EmptyState onPick={ask} />
          ) : (
            <div className="space-y-7 pt-4">
              {messages.map((m) => (
                <Message key={m.id} msg={m} />
              ))}
              <div ref={bottomRef} className="h-1" />
            </div>
          )}
        </div>
      </main>

      <Composer busy={busy} onSend={ask} onStop={stop} />
    </div>
  )
}

/* ------------------------------------------------------------------ */

function Header({ meta, onClear, hasMessages }) {
  const blocks = meta?.blocks
  const ready = meta ? meta.status !== 'degraded' : null

  return (
    <header className="relative z-10 shrink-0 border-b border-line/80 bg-ink/70 backdrop-blur-xl">
      <div className="top-line absolute inset-x-0 top-0 h-px" />
      <div className="mx-auto flex h-14 w-full max-w-3xl items-center justify-between px-5">
        <div className="flex items-center gap-2.5">
          {/* 一个方块 logo，不用图标库 */}
          <div className="grid h-6 w-6 place-items-center rounded-[7px] bg-gradient-to-br from-cyan to-violet text-[11px] font-bold text-ink">
            C
          </div>
          <span className="text-[14px] font-medium tracking-wide text-white/90">校园问答</span>
          <span className="hidden font-mono text-[11px] text-dim sm:inline">campus-agent</span>
        </div>

        <div className="flex items-center gap-4">
          {blocks ? (
            <span className="hidden items-center gap-1.5 font-mono text-[11px] text-dim sm:flex">
              <span className="text-white/60">{blocks}</span> 篇资料
            </span>
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

function Message({ msg }) {
  if (msg.role === 'user') {
    return (
      <div className="flex animate-rise justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md border border-white/10 bg-white/[0.055] px-4 py-2.5 text-[14.5px] leading-relaxed text-white/95">
          {msg.text}
        </div>
      </div>
    )
  }

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

        {msg.text ? (
          <div
            className={
              'answer whitespace-pre-wrap break-words text-[14.5px] leading-[1.8] text-[#dfe5f0]' +
              (msg.streaming ? ' caret' : '')
            }
          >
            {msg.text}
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
