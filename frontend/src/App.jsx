import React, { useMemo, useState } from 'react'

const MIN_BLOCKS = 8
const MAX_BLOCKS = 16
const MIN_TARGETS = 2
const MAX_TARGETS = 4

const uid = (() => {
  let n = 0
  return () => `r${++n}`
})()

function initialBlocks() {
  // A small preemption-flavoured demo dataset.
  const rows = [
    ['G1', 4], ['G2', 6], ['G3', 10], ['G4', 3], ['G5', 7],
    ['G6', 5], ['G7', 5], ['G8', 8],
  ]
  return rows.map(([label, length]) => ({ key: uid(), label, length: String(length) }))
}

function initialTargets() {
  return [
    { key: uid(), length: '10', tolerance: '0' },
    { key: uid(), length: '12', tolerance: '1' },
  ]
}

export default function App() {
  const [blocks, setBlocks] = useState(initialBlocks)
  const [targets, setTargets] = useState(initialTargets)
  const [errors, setErrors] = useState([])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null) // last SUCCESSFUL conclusion
  const [resultMeta, setResultMeta] = useState(null) // {at, failedSince}
  const [selected, setSelected] = useState(null) // block label

  const errorMap = useMemo(() => {
    const m = new Map()
    for (const e of errors) m.set(e.field, e.message)
    return m
  }, [errors])

  const errFor = (field) => errorMap.get(field)
  const rowError = (prefix) => {
    const hits = []
    for (const [f, msg] of errorMap) if (f.startsWith(prefix)) hits.push(msg)
    return hits.length ? hits.join('；') : null
  }

  function updateBlock(key, patch) {
    setBlocks((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)))
  }
  function updateTarget(key, patch) {
    setTargets((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)))
  }
  function addBlock() {
    if (blocks.length >= MAX_BLOCKS) return
    setBlocks((xs) => [...xs, { key: uid(), label: '', length: '' }])
  }
  function removeBlock(key) {
    if (blocks.length <= MIN_BLOCKS) return
    setBlocks((xs) => xs.filter((x) => x.key !== key))
  }
  function addTarget() {
    if (targets.length >= MAX_TARGETS) return
    setTargets((xs) => [...xs, { key: uid(), length: '', tolerance: '0' }])
  }
  function removeTarget(key) {
    if (targets.length <= MIN_TARGETS) return
    setTargets((xs) => xs.filter((x) => x.key !== key))
  }

  async function submit(ev) {
    ev.preventDefault()
    setLoading(true)
    setErrors([])
    const payload = {
      blocks: blocks.map((b) => ({ label: b.label, length: b.length })),
      targets: targets.map((t) => ({
        length: t.length, tolerance: t.tolerance,
      })),
    }
    try {
      const resp = await fetch('/api/solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await resp.json()
      if (data.ok) {
        setResult(data.result)
        setResultMeta({ at: new Date(), failedSince: 0 })
        setSelected(null)
      } else {
        // Failed validation/infeasibility: keep inputs AND keep showing the
        // previous successful conclusion; never overwrite it.
        setErrors(data.errors || [])
        setResultMeta((m) => m ? { ...m, failedSince: (m.failedSince || 0) + 1 } : m)
      }
    } catch (e) {
      setErrors([{ field: '_root', message: `请求失败：${e.message}` }])
    } finally {
      setLoading(false)
    }
  }

  const fateByLabel = useMemo(() => {
    const m = new Map()
    if (result) for (const f of result.block_fates) m.set(f.label, f)
    return m
  }, [result])

  const selectedFate = selected ? fateByLabel.get(selected) : null

  return (
    <div className="page">
      <header>
        <h1>精密量块 · 多目标间隙规范分配</h1>
        <p className="sub">
          8–16 块标识唯一的整数纳米量块，2–4 个带允许偏差的目标间隙。
          系统按 <b>最大绝对偏差 → 偏差绝对值之和 → 已用块数</b> 精确依次最小化，
          计数为任意精度的同优方案总数（不枚举截断）。
        </p>
      </header>

      <form onSubmit={submit} className="grid">
        <section className="card">
          <div className="card-head">
            <h2>量块（{blocks.length}/{MAX_BLOCKS}）</h2>
            <button type="button" onClick={addBlock}
              disabled={blocks.length >= MAX_BLOCKS}>+ 添加</button>
          </div>
          <div className="row header-row">
            <span>#</span><span>唯一标识</span><span>长度 (nm, 整数)</span><span></span>
          </div>
          {blocks.map((b, i) => {
            const eLabel = errFor(`blocks[${i}].label`)
            const eLen = errFor(`blocks[${i}].length`)
            const eRow = rowError(`blocks[${i}]`)
            return (
              <div className="row" key={b.key}>
                <span className="idx">{i + 1}</span>
                <span>
                  <input
                    value={b.label}
                    onChange={(e) => updateBlock(b.key, { label: e.target.value })}
                    className={eLabel ? 'bad' : ''}
                    placeholder="如 G1"
                  />
                  {eLabel && <em className="ferr">{eLabel}</em>}
                </span>
                <span>
                  <input
                    value={b.length}
                    inputMode="numeric"
                    onChange={(e) => updateBlock(b.key, { length: e.target.value })}
                    className={eLen ? 'bad' : ''}
                    placeholder="整数"
                  />
                  {eLen && <em className="ferr">{eLen}</em>}
                </span>
                <button type="button" className="del"
                  disabled={blocks.length <= MIN_BLOCKS}
                  onClick={() => removeBlock(b.key)}>×</button>
                {eRow && !eLabel && !eLen && <em className="ferr full">{eRow}</em>}
              </div>
            )
          })}
          {errFor('blocks') && <em className="ferr">{errFor('blocks')}</em>}
        </section>

        <section className="card">
          <div className="card-head">
            <h2>目标间隙（{targets.length}/{MAX_TARGETS}）</h2>
            <button type="button" onClick={addTarget}
              disabled={targets.length >= MAX_TARGETS}>+ 添加</button>
          </div>
          <div className="row header-row">
            <span>#</span><span>目标长度 (nm)</span><span>允许偏差 ±</span><span></span>
          </div>
          {targets.map((t, i) => {
            const eL = errFor(`targets[${i}].length`)
            const eT = errFor(`targets[${i}].tolerance`)
            return (
              <div className="row" key={t.key}>
                <span className="idx">{i + 1}</span>
                <span>
                  <input value={t.length} inputMode="numeric"
                    onChange={(e) => updateTarget(t.key, { length: e.target.value })}
                    className={eL ? 'bad' : ''} placeholder="整数" />
                  {eL && <em className="ferr">{eL}</em>}
                </span>
                <span>
                  <input value={t.tolerance} inputMode="numeric"
                    onChange={(e) => updateTarget(t.key, { tolerance: e.target.value })}
                    className={eT ? 'bad' : ''} placeholder="非负整数" />
                  {eT && <em className="ferr">{eT}</em>}
                </span>
                <button type="button" className="del"
                  disabled={targets.length <= MIN_TARGETS}
                  onClick={() => removeTarget(t.key)}>×</button>
              </div>
            )
          })}
          {errors
            .filter((e) => /^targets\[\d+\]$/.test(e.field))
            .map((e, i) => <em key={i} className="ferr">{e.message}</em>)}
          {errFor('allocation') && (
            <em className="ferr">{errFor('allocation')}</em>
          )}
          {errFor('targets') && <em className="ferr">{errFor('targets')}</em>}

          <div className="submit-line">
            <button type="submit" disabled={loading} className="primary">
              {loading ? '求解中…' : '提交并求规范分配'}
            </button>
            {errFor('_root') && <em className="ferr">{errFor('_root')}</em>}
          </div>
        </section>
      </form>

      {errors.length > 0 && (
        <div className="banner bad">
          本次提交存在字段级问题或无可行分配（见上方红色提示），未产生新结论；
          {result ? '上次成功结论仍保留在下方，输入也已保留可直接修改重试。'
                  : '尚无成功结论，输入已保留可直接修改重试。'}
        </div>
      )}

      {result && (
        <ResultPanel
          result={result}
          meta={resultMeta}
          selected={selected}
          onSelect={setSelected}
          selectedFate={selectedFate}
        />
      )}

      {!result && errors.length === 0 && (
        <p className="hint">提交后在此查看规范分配、各间隙实长与偏差；点选量块可查看它在所有同优方案中的去向。</p>
      )}
    </div>
  )
}

function GapTag({ j }) {
  return <span className="gap-tag">间隙 {j + 1}</span>
}

function ResultPanel({ result, meta, selected, onSelect, selectedFate }) {
  const { objective, gaps, cooptimal_count: count, unused_blocks: unused } = result
  return (
    <section className="card result">
      <div className="card-head">
        <h2>规范分配结果</h2>
        <span className="ok-pill">成功 {meta?.at ? meta.at.toLocaleTimeString() : ''}</span>
      </div>
      {meta?.failedSince > 0 && (
        <div className="banner stale">
          此后有 {meta.failedSince} 次提交失败（输入/无解），以下为最近一次成功结论，未被覆盖。
        </div>
      )}

      <div className="obj-row">
        <div><label>最大绝对偏差</label><b>{objective.max_abs_dev}</b> nm</div>
        <div><label>偏差绝对值之和</label><b>{objective.sum_abs_dev}</b> nm</div>
        <div><label>已用量块</label><b>{objective.used_blocks}</b> 块</div>
        <div className="wide">
          <label>同优方案总数（精确值）</label>
          <b className="count">{count}</b>
        </div>
      </div>

      <h3>各间隙实长与偏差（规范解）</h3>
      <div className="gaps">
        {gaps.map((g) => (
          <div key={g.index} className={`gap ${g.within_tolerance ? '' : 'viol'}`}>
            <div className="gap-title">
              <GapTag j={g.index} />
              <span className="muted">目标 {g.target_length} ± {g.tolerance}</span>
            </div>
            <div className="gap-main">
              <span className="actual">{g.actual_length} nm</span>
              <span className={`dev ${g.deviation === 0 ? 'zero' : ''}`}>
                {g.deviation > 0 ? '+' : ''}{g.deviation} nm
              </span>
            </div>
            <div className="gap-blocks">
              {g.blocks.map((label) => (
                <BlockChip
                  key={label}
                  label={label}
                  fate={result.block_fates.find((f) => f.label === label)}
                  active={selected === label}
                  onClick={() => onSelect(selected === label ? null : label)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <h3>量块去向（点选查看在所有同优方案中的可能去向）</h3>
      <div className="fate-grid">
        {result.block_fates.map((f) => (
          <BlockChip
            key={f.label}
            label={f.label}
            fate={f}
            active={selected === f.label}
            onClick={() => onSelect(selected === f.label ? null : f.label)}
          />
        ))}
      </div>
      <div className="muted small">
        未用块（规范解）：{unused.length ? unused.join('、') : '无'}
      </div>

      {selectedFate && <FateDetail fate={selectedFate} gapCount={gaps.length} />}
    </section>
  )
}

function BlockChip({ label, fate, active, onClick }) {
  const cls = fate ? `chip fate-${fate.kind}` : 'chip'
  return (
    <button type="button" className={`${cls} ${active ? 'active' : ''}`} onClick={onClick}
      title={fate ? fateText(fate) : label}>
      {label}
    </button>
  )
}

function fateText(f) {
  if (f.kind === 'fixed') return `固定用于间隙 ${f.gap + 1}`
  if (f.kind === 'unused') return '所有同优方案中均未使用'
  const gs = f.gaps.map((j) => `间隙${j + 1}`).join('、')
  return f.can_be_unused ? `可在 ${gs} 或未使用之间流转` : `可在 ${gs} 之间流转`
}

function FateDetail({ fate, gapCount }) {
  return (
    <div className={`detail fate-${fate.kind}`}>
      <h4>量块 {fate.label}（{fate.length} nm）在所有同优方案中的去向</h4>
      {fate.kind === 'fixed' && (
        <p>🔒 <b>固定到间隙 {fate.gap + 1}</b>：每一个同优方案都把它用于该间隙，不会出现在别处，也不会闲置。</p>
      )}
      {fate.kind === 'unused' && (
        <p>⚪ <b>始终未用</b>：在所有同优方案中它都不被任何间隙使用。</p>
      )}
      {fate.kind === 'flexible' && (
        <div>
          <p>🔀 <b>可流转</b>：在不同的同优方案中它可能出现在：</p>
          <ul>
            {fate.gaps.map((j) => <li key={j}>用于 <GapTag j={j} /></li>)}
            {fate.can_be_unused && <li>保持未使用（不影响最优三元组）</li>}
          </ul>
        </div>
      )}
    </div>
  )
}
