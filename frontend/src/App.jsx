import { useMemo, useState } from 'react'

const MIN_BLOCKS = 8
const MAX_BLOCKS = 16
const MIN_TARGETS = 2
const MAX_TARGETS = 4

const uid = () => Math.random().toString(36).slice(2, 9)

const newBlock = () => ({ key: uid(), id: '', length: '' })
const newTarget = () => ({ key: uid(), target: '', tolerance: '' })

const initialBlocks = () => {
  // A small prefilled example close to the "preemption" situation.
  const demo = [
    ['G1', 10], ['G2', 20], ['G3', 30], ['G4', 40],
    ['G5', 50], ['G6', 60], ['G7', 71], ['G8', 80],
    ['G9', 90], ['G10', 100],
  ]
  return demo.map(([id, length]) => ({ key: uid(), id, length: String(length) }))
}

function intError(value, { min, max, name, nonneg = false }) {
  if (value.trim() === '') return `${name}必填`
  if (!/^[+-]?\d+$/.test(value.trim())) return `${name}必须是整数`
  const v = Number(value)
  if (nonneg && v < 0) return `${name}不能为负`
  if (v < min || v > max) return `${name}需在 ${min}–${max} 之间`
  return null
}

function clientValidate(blocks, targets) {
  const errors = {}
  if (!(MIN_BLOCKS <= blocks.length && blocks.length <= MAX_BLOCKS))
    errors.blocks = `需要 ${MIN_BLOCKS}–${MAX_BLOCKS} 个量块`
  if (!(MIN_TARGETS <= targets.length && targets.length <= MAX_TARGETS))
    errors.targets = `需要 ${MIN_TARGETS}–${MAX_TARGETS} 个目标间隙`

  const seen = new Set()
  blocks.forEach((b, i) => {
    if (!b.id.trim() || /\s/.test(b.id) || b.id.length > 32)
      errors[`blocks[${i}].id`] = '标识需为 1–32 个非空白字符'
    else if (seen.has(b.id)) errors[`blocks[${i}].id`] = `标识「${b.id}」重复`
    else seen.add(b.id)
    const e = intError(b.length, { min: 1, max: 1e9, name: '长度' })
    if (e) errors[`blocks[${i}].length`] = e
  })
  targets.forEach((t, i) => {
    const e1 = intError(t.target, { min: 1, max: 1e9, name: '目标间隙' })
    if (e1) errors[`targets[${i}].target`] = e1
    const e2 = intError(t.tolerance, { min: 0, max: 1e9, name: '允许偏差' })
    if (e2) errors[`targets[${i}].tolerance`] = e2
  })
  return errors
}

function FieldErr({ msg }) {
  return msg ? <div className="field-err">{msg}</div> : null
}

export default function App() {
  const [blocks, setBlocks] = useState(initialBlocks)
  const [targets, setTargets] = useState([
    { key: uid(), target: '80', tolerance: '2' },
    { key: uid(), target: '100', tolerance: '2' },
  ])
  const [errors, setErrors] = useState({})
  const [result, setResult] = useState(null) // last SUCCESS only
  const [staleNote, setStaleNote] = useState(false)
  const [loading, setLoading] = useState(false)
  const [topError, setTopError] = useState('')
  const [selectedId, setSelectedId] = useState(null)

  const patchBlock = (key, patch) =>
    setBlocks((bs) => bs.map((b) => (b.key === key ? { ...b, ...patch } : b)))
  const patchTarget = (key, patch) =>
    setTargets((ts) => ts.map((t) => (t.key === key ? { ...t, ...patch } : t)))

  const submit = async () => {
    const local = clientValidate(blocks, targets)
    setErrors(local)
    if (Object.keys(local).length) {
      setTopError('输入有误，请按字段提示修正（已保留全部输入）。')
      setStaleNote(result ? true : false)
      return
    }
    const payload = {
      blocks: blocks.map((b) => ({
        id: b.id.trim(),
        length: Number(b.length),
      })),
      targets: targets.map((t) => ({
        target: Number(t.target),
        tolerance: Number(t.tolerance),
      })),
    }
    setLoading(true)
    setTopError('')
    try {
      const resp = await fetch('/api/solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await resp.json()
      if (!resp.ok || !data.ok) {
        const mapped = {}
        ;(data.errors || []).forEach((e) => (mapped[e.field] = e.message))
        setErrors(mapped)
        setTopError(
          data.infeasible
            ? '无可行分配：放宽允许偏差或调整量块后重试。'
            : '提交失败，请按字段提示修正（已保留全部输入与上次成功结果）。',
        )
        // Failure never overwrites the previous successful conclusion.
        setStaleNote(!!result)
        return
      }
      setResult(data)
      setStaleNote(false)
      setSelectedId(null)
    } catch (err) {
      setTopError(`服务暂不可用：${err.message}（已保留输入与上次成功结果）`)
      setStaleNote(!!result)
    } finally {
      setLoading(false)
    }
  }

  const selected = useMemo(() => {
    if (!result || selectedId == null) return null
    return result.destinations.find((d) => d.id === selectedId) || null
  }, [result, selectedId])

  const blockLength = (id) => {
    const b = blocks.find((x) => x.id.trim() === id)
    return b ? Number(b.length) : null
  }

  return (
    <div className="page">
      <header>
        <h1>量块间隙精密分配</h1>
        <p className="sub">
          整数纳米精确求解 · 依次最小化「最大绝对偏差 → 偏差绝对值之和 → 已用块数」·
          同优方案任意精度计数
        </p>
      </header>

      <section className="card">
        <div className="card-head">
          <h2>量块（{blocks.length}）</h2>
          <div className="btn-row">
            <button
              disabled={blocks.length >= MAX_BLOCKS}
              onClick={() => setBlocks((bs) => [...bs, newBlock()])}
            >
              + 添加量块
            </button>
            <button
              disabled={blocks.length <= MIN_BLOCKS}
              onClick={() => setBlocks((bs) => bs.slice(0, -1))}
            >
              − 删除末行
            </button>
          </div>
        </div>
        {errors.blocks && <FieldErr msg={errors.blocks} />}
        <div className="grid blocks-grid">
          {blocks.map((b, i) => (
            <div className="row" key={b.key}>
              <input
                className={errors[`blocks[${i}].id`] ? 'bad' : ''}
                placeholder="唯一标识"
                value={b.id}
                onChange={(e) => patchBlock(b.key, { id: e.target.value })}
              />
              <input
                className={`num ${errors[`blocks[${i}].length`] ? 'bad' : ''}`}
                placeholder="长度 nm"
                value={b.length}
                inputMode="numeric"
                onChange={(e) => patchBlock(b.key, { length: e.target.value })}
              />
              <FieldErr msg={errors[`blocks[${i}].id`]} />
              <FieldErr msg={errors[`blocks[${i}].length`]} />
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>目标间隙（{targets.length}）</h2>
          <div className="btn-row">
            <button
              disabled={targets.length >= MAX_TARGETS}
              onClick={() => setTargets((ts) => [...ts, newTarget()])}
            >
              + 添加间隙
            </button>
            <button
              disabled={targets.length <= MIN_TARGETS}
              onClick={() => setTargets((ts) => ts.slice(0, -1))}
            >
              − 删除末行
            </button>
          </div>
        </div>
        {errors.targets && <FieldErr msg={errors.targets} />}
        <table className="targets">
          <thead>
            <tr><th>顺序</th><th>目标间隙 (nm)</th><th>允许偏差 (nm)</th></tr>
          </thead>
          <tbody>
            {targets.map((t, i) => (
              <tr key={t.key}>
                <td>#{i + 1}</td>
                <td>
                  <input
                    className={errors[`targets[${i}].target`] ? 'bad' : ''}
                    value={t.target}
                    inputMode="numeric"
                    onChange={(e) => patchTarget(t.key, { target: e.target.value })}
                  />
                  <FieldErr msg={errors[`targets[${i}].target`]} />
                </td>
                <td>
                  <input
                    className={errors[`targets[${i}].tolerance`] ? 'bad' : ''}
                    value={t.tolerance}
                    inputMode="numeric"
                    onChange={(e) =>
                      patchTarget(t.key, { tolerance: e.target.value })
                    }
                  />
                  <FieldErr msg={errors[`targets[${i}].tolerance`]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="submit-row">
        <button className="primary" disabled={loading} onClick={submit}>
          {loading ? '求解中…' : '提交并求规范分配'}
        </button>
        {topError && <span className="top-err">{topError}</span>}
        {staleNote && !topError && (
          <span className="stale">下方仍为上次成功结论，未被本次失败覆盖。</span>
        )}
      </div>

      {result && <Result result={result} selectedId={selectedId}
                         onSelect={setSelectedId} selected={selected}
                         blockLength={blockLength} />}
    </div>
  )
}

function pct(countStr, totalStr) {
  try {
    const c = BigInt(countStr)
    const t = BigInt(totalStr)
    if (t === 0n) return '0%'
    const whole = (c * 10000n) / t
    const s = whole.toString().padStart(5, '0')
    return `${Number(s.slice(0, -2) + '.' + s.slice(-2)).toFixed(2)}%`
  } catch {
    return ''
  }
}

const KIND_LABEL = {
  fixed: { text: '固定到某间隙', cls: 'k-fixed' },
  flexible: { text: '可流转', cls: 'k-flex' },
  always_unused: { text: '始终未用', cls: 'k-unused' },
}

function Result({ result, selectedId, onSelect, selected, blockLength }) {
  return (
    <section className="card result">
      <h2>规范分配</h2>
      <div className="stats">
        <Stat label="最大绝对偏差" value={`${result.maxAbsDev} nm`} />
        <Stat label="偏差绝对值之和" value={`${result.sumAbsDev} nm`} />
        <Stat label="已用量块" value={`${result.usedCount} 块`} />
        <Stat label="同优方案总数" value={result.totalOptimal} mono />
      </div>

      <div className="gaps">
        {result.gaps.map((g, i) => {
          const ids = result.canonical[i]
          const inTol = Math.abs(g.deviation) <= g.tolerance
          return (
            <div className="gap" key={i}>
              <div className="gap-head">
                <h3>间隙 #{i + 1}</h3>
                <span className={`dev ${inTol ? 'ok' : 'viol'}`}>
                  实长 {g.actual} nm · 偏差 {g.deviation >= 0 ? '+' : ''}
                  {g.deviation} nm（限 ±{g.tolerance}）
                </span>
              </div>
              <div className="chips">
                {ids.map((id) => (
                  <Chip key={id} id={id} len={blockLength(id)}
                        active={selectedId === id}
                        onSelect={onSelect} tone={`gap${i % 4}`} />
                ))}
              </div>
            </div>
          )
        })}
      </div>

      <div className="unused-box">
        <h3>未用量块（{result.unused.length}）</h3>
        <div className="chips">
          {result.unused.length === 0 && <span className="muted">无</span>}
          {result.unused.map((id) => (
            <Chip key={id} id={id} len={blockLength(id)}
                  active={selectedId === id}
                  onSelect={onSelect} tone="unused" />
          ))}
        </div>
      </div>

      {selected && (
        <div className="dest-panel">
          <div className="dest-head">
            <h3>
              量块「{selected.id}」在全部 {result.totalOptimal} 个同优方案中的去向
            </h3>
            <button onClick={() => onSelect(null)}>关闭</button>
          </div>
          <span className={`kind ${KIND_LABEL[selected.kind].cls}`}>
            {KIND_LABEL[selected.kind].text}
          </span>
          {selected.kind === 'fixed' && (
            <span className="muted">
              　固定分配到间隙 #{selected.gap + 1}
            </span>
          )}
          <table className="flows">
            <thead>
              <tr><th>去向</th><th>方案数</th><th>占比</th></tr>
            </thead>
            <tbody>
              {selected.flows.map((f) => (
                <tr key={f.gap}>
                  <td>{f.gap === -1 ? '未使用' : `间隙 #${f.gap + 1}`}</td>
                  <td className="mono">{f.count}</td>
                  <td>{pct(f.count, result.totalOptimal)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="hint">
        点选任意量块查看它在所有同优方案中的去向；
        绿色「可流转」表示它可在多个间隙（含未用）之间流动而不改变最优指标。
      </p>
    </section>
  )
}

function Stat({ label, value, mono }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${mono ? 'mono' : ''}`}>{value}</div>
    </div>
  )
}

function Chip({ id, len, active, onSelect, tone }) {
  return (
    <button
      className={`chip ${tone} ${active ? 'active' : ''}`}
      onClick={() => onSelect(active ? null : id)}
      title="点选查看全部同优方案中的去向"
    >
      <span className="chip-id">{id}</span>
      <span className="chip-len">{len ?? '?'} nm</span>
    </button>
  )
}
