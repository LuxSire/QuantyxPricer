import React, { useState, useEffect, useCallback } from 'react'
import { usePrices } from './hooks/usePrices'

const CURVE_TYPES = ['cds', 'ois', 'fx', 'basis', 'volatility', 'swap', 'forward']

const TENOR_RANK = {
  ON: 0, '1W': 1, '1M': 2, '2M': 3, '3M': 4, '6M': 5, '9M': 6,
  '1Y': 7, '2Y': 8, '3Y': 9, '4Y': 10, '5Y': 11, '6Y': 12,
  '7Y': 13, '8Y': 14, '9Y': 15, '10Y': 16, '12Y': 17,
  '15Y': 18, '20Y': 19, '25Y': 20, '30Y': 21,
}

function tenorRank(t) {
  return TENOR_RANK[t] ?? 99
}

function fmtRate(v, curveType, tenor) {
  if (v == null) return '—'
  const n = Number(v)
  if (isNaN(n)) return '—'
  if (curveType === 'cds') return `${n.toFixed(3)} bp`
  if (tenor === 'ON') return `${n.toFixed(3)}%`
  return `${(n * 100).toFixed(3)}%`
}

export default function Curves({ apiBase }) {
  const { fetchCurves, insertCurve, fetchIndividualCdsRate } = usePrices(apiBase)
  const [curves, setCurves] = useState(null)
  const [error, setError] = useState(null)
  const [addOpen, setAddOpen] = useState(false)
  const [fetchingRows, setFetchingRows] = useState(new Set())
  const [rowErrors, setRowErrors] = useState({})

  const loadCurves = useCallback(() => {
    let mounted = true
    fetchCurves().then(data => {
      if (!mounted) return
      if (!data || data.length === 0) { setError('No curves returned'); return }
      setError(null)
      setCurves(data)
    })
    return () => { mounted = false }
  }, [apiBase])

  useEffect(() => {
    return loadCurves()
  }, [loadCurves])

  const handleFetchRate = async (curve_name) => {
    setFetchingRows(prev => new Set([...prev, curve_name]))
    setRowErrors(prev => { const next = { ...prev }; delete next[curve_name]; return next })
    const result = await fetchIndividualCdsRate(curve_name)
    setFetchingRows(prev => { const next = new Set(prev); next.delete(curve_name); return next })
    if (result.ok) {
      setCurves(null)
      loadCurves()
    } else {
      setRowErrors(prev => ({ ...prev, [curve_name]: result.error || 'Failed' }))
    }
  }

  if (error) return (
    <div className="curves-page">
      <div className="curves-page-toolbar">
        <button className="clear-btn" onClick={() => { window.location.hash = '' }}>← Back</button>
      </div>
      <p className="curves-page-error">{error}</p>
    </div>
  )

  if (!curves) return (
    <div className="curves-page">
      <div className="curves-page-toolbar">
        <button className="clear-btn" onClick={() => { window.location.hash = '' }}>← Back</button>
      </div>
      <p className="curves-page-loading">Loading curves...</p>
    </div>
  )

  const tenorSet = new Set()
  for (const c of curves) {
    if (Array.isArray(c.pillars)) {
      for (const p of c.pillars) if (p.tenor) tenorSet.add(p.tenor)
    }
  }
  const tenors = Array.from(tenorSet).sort((a, b) => tenorRank(a) - tenorRank(b))

  return (
    <div className="curves-page">
      <div className="curves-page-toolbar">
        <button className="clear-btn" onClick={() => { window.location.hash = '' }}>← Back</button>
        <button className="clear-btn" onClick={() => setAddOpen(true)}>+ Add</button>
      </div>
      <h2>Curves</h2>
      <div className="curves-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Curve</th>
              <th>Type</th>
              <th>As of</th>
              {tenors.map(t => <th key={t} className="center">{t}</th>)}
              <th className="center">Fetch</th>
            </tr>
          </thead>
          <tbody>
            {curves.map((c, i) => {
              const pillarMap = {}
              if (Array.isArray(c.pillars)) {
                for (const p of c.pillars) pillarMap[p.tenor] = p.rate
              }
              return (
                <tr key={i}>
                  <td className="mono nowrap">{c.curve_name}</td>
                  <td>{c.curve_type || '—'}</td>
                  <td className="center">{c.as_of || '—'}</td>
                  {tenors.map(t => (
                    <td key={t} className="center">
                      {pillarMap[t] != null ? fmtRate(pillarMap[t], c.curve_type, t) : '—'}
                    </td>
                  ))}
                  <td className="center">
                    <button
                      className={`clear-btn curve-fetch-btn${fetchingRows.has(c.curve_name) ? ' curve-fetch-btn--loading' : ''}`}
                      onClick={() => handleFetchRate(c.curve_name)}
                      disabled={fetchingRows.has(c.curve_name)}
                      title={rowErrors[c.curve_name] || `Fetch rate for ${c.curve_name}`}
                    >
                      {fetchingRows.has(c.curve_name) ? '⏳' : rowErrors[c.curve_name] ? '✕' : '↓'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {addOpen && (
        <AddCurveDialog
          insertCurve={insertCurve}
          onClose={() => setAddOpen(false)}
          onSaved={() => { setAddOpen(false); setCurves(null); loadCurves() }}
        />
      )}
    </div>
  )
}

function AddCurveDialog({ insertCurve, onClose, onSaved }) {
  const [name, setName] = useState('')
  const [type, setType] = useState('cds')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async () => {
    const trimmed = name.trim()
    if (!trimmed) { setError('Curve name is required'); return }
    setLoading(true)
    setError('')
    const result = await insertCurve({ curve_name: trimmed, curve_type: type })
    setLoading(false)
    if (result.ok) {
      onSaved()
    } else {
      setError(result.error || 'Insert failed')
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter') handleSubmit()
    if (e.key === 'Escape') onClose()
  }

  return (
    <div className="delete-field-backdrop" onClick={onClose}>
      <div className="delete-field-modal" onClick={(e) => e.stopPropagation()}>
        <div className="delete-field-title">Add curve</div>

        <div className="add-curve-field">
          <label className="add-curve-label">Curve name</label>
          <input
            className="add-curve-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={handleKey}
            placeholder="e.g. CDS_FRANCE_5Y"
            autoFocus
          />
        </div>

        <div className="add-curve-field">
          <label className="add-curve-label">Curve type</label>
          <select
            className="add-curve-input"
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            {CURVE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        {error && <div className="add-curve-error">{error}</div>}

        <div className="delete-field-actions">
          <button className="clear-btn clear-btn--cancel" onClick={onClose} disabled={loading}>Cancel</button>
          <button className="clear-btn" onClick={handleSubmit} disabled={loading}>
            {loading ? 'Saving...' : 'Add'}
          </button>
        </div>
      </div>
    </div>
  )
}
