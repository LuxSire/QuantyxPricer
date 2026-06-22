import React, { useEffect, useState } from 'react'
import { formatNumberForDisplay, isPercentageKey } from './helper'
import { useAsset } from './hooks/useAsset'
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'

export default function Instrument({ instrumentId, apiBase = '' }) {
  // instrumentId may be encoded as "ID::bond_file.json" from the table link
  let bondFile = null
  let isin = instrumentId
  if (instrumentId && instrumentId.includes('::')) {
    const parts = instrumentId.split('::')
    isin = parts[0]
    bondFile = parts.slice(1).join('::')
  }
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [priceEntry, setPriceEntry] = useState(null)
  const [editMode, setEditMode] = useState(false)
  const [draftValues, setDraftValues] = useState({})
  const [saving, setSaving] = useState(false)
  const mandatoryFields = {
    instrument_id: data?.instrument_id || isin || '',
    model: data?.model || '',
    currency: data?.currency || '',
  }
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [snack, setSnack] = useState({ visible: false, message: '', type: 'info' })
  const [snackHiding, setSnackHiding] = useState(false)
  const { fetchAsset, updateAsset } = useAsset(apiBase)

  useEffect(() => {
    let mounted = true
    async function fetchJson() {
      try {
        if (!isin) {
          if (mounted) setError('No instrument ID available')
          return
        }
        const j = await fetchAsset(isin)
        if (!mounted) return
        if (j) {
          // Ensure mandatory fields are always present
          const ensured = {
            ...j,
            instrument_id: j.instrument_id || isin,
            model: j.model || '',
            currency: j.currency || '',
          }
          setData(ensured)
        } else {
          setError('Could not fetch asset JSON for ' + isin)
        }
      } catch (e) {
        if (mounted) setError(String(e))
      }
    }
    fetchJson()
    return () => { mounted = false }
  }, [instrumentId, apiBase, bondFile, isin, fetchAsset])

  useEffect(() => {
    let mounted = true
    async function fetchPrices() {
      try {
        const base = String(apiBase || '').replace(/\/$/, '')
        const endpoint = `${base}/prices`
        const r = await fetch(endpoint)
        if (!r.ok) return
        const j = await r.json()
        if (!mounted) return
        // find by instrument id (match either instrument_id or bond_file)
        const isinKey = isin || instrumentId
        const found = j.find(e => e.instrument_id === isinKey || e.bond_file === (bondFile || `${isinKey}.json`))
        if (found) setPriceEntry(found)
      } catch {
        // ignore silently
      }
    }
    fetchPrices()
    return () => { mounted = false }
  }, [instrumentId, data, bondFile, isin, apiBase])

  useEffect(() => {
    let hideTimer = null
    let removeTimer = null
    if (snack.visible) {
      hideTimer = setTimeout(() => setSnackHiding(true), 4000)
    }
    if (snackHiding) {
      removeTimer = setTimeout(() => {
        setSnack({ visible: false, message: '', type: 'info' })
        setSnackHiding(false)
      }, 240)
    }
    return () => {
      if (hideTimer) clearTimeout(hideTimer)
      if (removeTimer) clearTimeout(removeTimer)
    }
  }, [snack.visible, snackHiding])

  if (error) return (
    <div>
      <a href="#" onClick={(e)=>{e.preventDefault(); window.location.hash=''}}>&larr; Back</a>
      <div className="error">{error}</div>
    </div>
  )
  if (!data) return (
    <div>
      <a href="#" onClick={(e)=>{e.preventDefault(); window.location.hash=''}}>&larr; Back</a>
      <div>Loading {instrumentId}...</div>
    </div>
  )

  // prepare values for three-column display
  const nestedKeys = new Set(['collateral', 'swap', 'csa', 'valuation_adjustments'])
  // priceResult/ytm used in fallbacks
  const priceResult = priceEntry && priceEntry.result ? priceEntry.result : null
  const pp = priceResult && priceResult.price_pct ? priceResult.price_pct : {}
  const colPV = pp.pv_note ?? (priceResult && (priceResult.pv_note ?? priceResult.selected_npv))
  const colWorst = pp.pv_note_to_worst ?? pp.pv_note_to_worst_call ?? (priceResult && (priceResult.npv_to_worst_call || priceResult.npv_to_worst))
  const colMat = pp.pv_note_to_maturity ?? (priceResult && (priceResult.npv_to_maturity || priceResult.npv_to_maturity))

  // Dynamically distribute all non-null fields from data across three columns
  const allEntries = Object.entries(data).filter((entry) => {
    const v = entry[1]
    return v != null && v !== '' && (typeof v !== 'object' || Object.keys(v).length > 0)
  })
  const colSize = Math.ceil(allEntries.length / 3)
  const firstCol = Object.fromEntries(allEntries.slice(0, colSize))
  const secondCol = Object.fromEntries(allEntries.slice(colSize, colSize * 2))
  const thirdCol = Object.fromEntries(allEntries.slice(colSize * 2))

  const toFiniteNumber = (value) => {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }

  const targetPrice = toFiniteNumber(data.target_price)
  const radarSource = [
    { metric: 'PV', actual: toFiniteNumber(colPV) },
    { metric: 'PV_to_worst', actual: toFiniteNumber(colWorst) },
    { metric: 'PV_to_maturity', actual: toFiniteNumber(colMat) },
  ]

  const hasRadarChart = Number.isFinite(targetPrice) && radarSource.every((d) => Number.isFinite(d.actual))
  const radarData = hasRadarChart
    ? radarSource.map((d) => ({
        metric: d.metric,
        // Chart radius represents distance from target price, so target sits at the center.
        delta_from_target: Math.abs(d.actual - targetPrice),
        actual: d.actual,
        target: targetPrice,
      }))
    : []
  const radarMax = hasRadarChart
    ? Math.max(...radarData.map((d) => d.delta_from_target), 1)
    : 1

  const renderValue = (k, v) => {
    const truncate = (s, n = 50) => (typeof s === 'string' && s.length > n) ? s.slice(0, n - 1) + '…' : s
    const lowerKey = String(k).toLowerCase()
    if (typeof v === 'number') {
      if (isPercentageKey(lowerKey)) {
        return formatNumberForDisplay(v, { scale: 100, suffix: '%' })
      }
      return formatNumberForDisplay(v)
    }
    if (v && typeof v === 'object' && nestedKeys.has(k)) {
      return (
        <table className="nested-table">
          <tbody>
            {Object.entries(v).map(([nk, nv]) => (
              <tr key={nk}>
                <td className="nested-key">{nk}</td>
                <td className="nested-value"><pre>{typeof nv === 'object' ? JSON.stringify(nv, null, 2) : String(nv)}</pre></td>
              </tr>
            ))}
          </tbody>
        </table>
      )
    }
    if (k === 'call_dates' && Array.isArray(v)) {
      return (
        <table className="call-dates-table">
          <thead>
            <tr><th>Call Date</th></tr>
          </thead>
          <tbody>
            {v.map((item, ri) => (
              Array.isArray(item) ? (
                <tr key={ri}>{item.map((c, ci) => <td key={ci}>{String(c)}</td>)}</tr>
              ) : (
                <tr key={ri}><td>{String(item)}</td></tr>
              )
            ))}
          </tbody>
        </table>
      )
    }
    if (typeof v === 'string' && lowerKey === 'description') return <pre>{truncate(v, 50)}</pre>
    return <pre>{typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)}</pre>
  }

  const isDateKey = (k) => {
    const s = String(k || '').toLowerCase()
    return s.includes('date') || s.includes('maturity') || s.includes('issue') || s.includes('expiry')
  }

  const toDateInputValue = (v) => {
    if (v == null) return ''
    const s = String(v).trim()
    if (!s) return ''
    // already ISO-like
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10)
    // common day-first formats (DD-MM-YYYY / DD/MM/YYYY)
    let m = s.match(/^(\d{2})[-/](\d{2})[-/](\d{4})$/)
    if (m) {
      const dd = m[1]
      const mm = m[2]
      const yyyy = m[3]
      return `${yyyy}-${mm}-${dd}`
    }
    // compact yyyymmdd
    m = s.match(/^(\d{4})(\d{2})(\d{2})$/)
    if (m) return `${m[1]}-${m[2]}-${m[3]}`
    const d = new Date(s)
    if (Number.isNaN(d.getTime())) return ''
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd}`
  }

  const onDraftChange = (k, nextValue) => {
    setDraftValues(prev => ({ ...prev, [k]: nextValue }))
  }

  const isDayCountKey = (k) => {
    const s = String(k || '').toLowerCase()
    return s === 'day_count_convention' || s === 'accrual_day_count' || s === 'day_count' || s === 'float_reference_day_count' || s === 'cms_day_count'
  }

  const renderEditor = (k, fallbackValue) => {
    const value = Object.prototype.hasOwnProperty.call(draftValues, k) ? draftValues[k] : fallbackValue
    if (isDayCountKey(k)) {
      const options = ['Actual360', 'Actual365Fixed', 'Thirty360', '30/360', 'ActualActual', 'ACT/ACT (PERIODIC BASIS)', 'ACT/ACT (ICMA)']
      const selected = value == null ? '' : String(value)
      const values = options.includes(selected) ? options : [...options, selected]
      return (
        <select value={selected} onChange={(e) => onDraftChange(k, e.target.value)}>
          <option value="">(empty)</option>
          {values.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      )
    }
    if (value != null && typeof value === 'object') {
      return (
        <textarea
          value={typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
          onChange={(e) => onDraftChange(k, e.target.value)}
          rows={4}
          style={{ width: '100%' }}
        />
      )
    }
    if (isDateKey(k)) {
      return (
        <input
          type="date"
          value={toDateInputValue(value)}
          onChange={(e) => onDraftChange(k, e.target.value)}
        />
      )
    }
    if (typeof value === 'boolean') {
      return (
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onDraftChange(k, e.target.checked)}
        />
      )
    }
    if (typeof value === 'number') {
      return (
        <input
          type="number"
          step="any"
          value={Number.isFinite(value) ? String(value) : ''}
          onChange={(e) => onDraftChange(k, e.target.value)}
        />
      )
    }
    return (
      <input
        type="text"
        value={value == null ? '' : String(value)}
        onChange={(e) => onDraftChange(k, e.target.value)}
      />
    )
  }

  const coerceEditedValue = (key, rawValue, originalValue) => {
    if (rawValue == null) return rawValue
    if (typeof rawValue === 'boolean') return rawValue
    if (typeof rawValue === 'string') {
      if (typeof originalValue === 'number') {
        const n = Number(rawValue)
        return Number.isNaN(n) ? originalValue : n
      }
      if (typeof originalValue === 'boolean') {
        return rawValue.toLowerCase() === 'true'
      }
      const trimmed = rawValue.trim()
      if ((typeof originalValue === 'object' && originalValue !== null) || trimmed.startsWith('{') || trimmed.startsWith('[')) {
        try {
          return JSON.parse(trimmed)
        } catch {
          return rawValue
        }
      }
      if (isDateKey(key)) return trimmed || null
      return rawValue
    }
    return rawValue
  }
  // build table rows: each row contains up to three field/value pairs (col pairs: 1-2, 3-4, 5-6)
  const entries1 = Object.entries(firstCol).filter((entry) => {
    const v = entry[1]
    return v != null && v !== ''
  })
  const entries2 = Object.entries(secondCol).filter((entry) => {
    const v = entry[1]
    return v != null && v !== ''
  })
  const entries3 = Object.entries(thirdCol).filter((entry) => {
    const v = entry[1]
    return v != null && v !== ''
  })
  const maxLen = Math.max(entries1.length, entries2.length, entries3.length)
  const rows = []
  for (let i = 0; i < maxLen; i++) {
    rows.push([
      entries1[i] || [null, null],
      entries2[i] || [null, null],
      entries3[i] || [null, null]
    ])
  }

  const allDisplayedEntries = [...entries1, ...entries2, ...entries3]

  const startEdit = () => {
    const initial = {}
    for (const [k, v] of allDisplayedEntries) initial[k] = v
    // Ensure mandatory fields are included in draft
    initial.instrument_id = mandatoryFields.instrument_id
    initial.model = mandatoryFields.model
    initial.currency = mandatoryFields.currency
    setDraftValues(initial)
    setEditMode(true)
  }

  const cancelEdit = () => {
    setEditMode(false)
    setDraftValues({})
    setNewKey('')
    setNewValue('')
  }

  const saveEdit = async () => {
    if (saving) return
    setSaving(true)
    try {
      const updated = JSON.parse(JSON.stringify(data))
      for (const [k, v] of Object.entries(draftValues)) {
        const original = Object.prototype.hasOwnProperty.call(updated, k) ? updated[k] : undefined
        updated[k] = coerceEditedValue(k, v, original)
      }

      // Include new field from the key/value textboxes if both are filled
      const trimmedKey = newKey.trim()
      const trimmedValue = newValue.trim()
      if (trimmedKey && trimmedValue) {
        updated[trimmedKey] = coerceEditedValue(trimmedKey, trimmedValue, undefined)
      }

      await updateAsset(updated, bondFile, isin)

      setData(updated)
      setEditMode(false)
      setDraftValues({})
      setNewKey('')
      setNewValue('')
      setSnack({ visible: true, message: 'Asset updated successfully', type: 'success' })
    } catch (e) {
      setSnack({ visible: true, message: `Save failed: ${String(e)}`, type: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const openTermsheet = async () => {
    try {
      const base = String(apiBase || '').replace(/\/$/, '')
      const url = `${base}/fetch_termsheet?instrument_id=${encodeURIComponent(isin)}&_ts=${Date.now()}`
      const resp = await fetch(url)
      if (!resp.ok) {
        const msg = await resp.text().catch(() => 'Could not load termsheet')
        setSnack({ visible: true, message: `Termsheet not available: ${msg}`, type: 'error' })
        return
      }
      const blob = await resp.blob()
      const blobUrl = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
      window.open(blobUrl, '_blank', 'noopener,noreferrer')
      // Revoke after a delay to avoid closing the opened resource too early.
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60000)
    } catch (e) {
      setSnack({ visible: true, message: `Termsheet open failed: ${String(e)}`, type: 'error' })
    }
  }

  const openReport = async () => {
    try {
      const base = String(apiBase || '').replace(/\/$/, '')
      const url = `${base}/fetch_report?instrument_id=${encodeURIComponent(isin)}&_ts=${Date.now()}`
      const resp = await fetch(url)
      if (!resp.ok) {
        const msg = await resp.text().catch(() => 'Could not load report')
        setSnack({ visible: true, message: `Report not available: ${msg}`, type: 'error' })
        return
      }
      const blob = await resp.blob()
      const blobUrl = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
      window.open(blobUrl, '_blank', 'noopener,noreferrer')
      // Revoke after a delay to avoid closing the opened resource too early.
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60000)
    } catch (e) {
      setSnack({ visible: true, message: `Report open failed: ${String(e)}`, type: 'error' })
    }
  }

  return (
    <div>
      <a href="#" onClick={(e)=>{e.preventDefault(); window.location.hash=''}}>&larr; Back</a>
      <div style={{ marginTop: 10, marginBottom: 8, display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button className="clear-btn clear-btn--termsheet" onClick={openTermsheet}>Termsheet</button>
        <button className="clear-btn clear-btn--report" onClick={openReport}>Report</button>
        {!editMode ? (
          <button className="clear-btn" onClick={startEdit}>Edit</button>
        ) : (
          <>
            <button className="clear-btn" onClick={saveEdit} disabled={saving}>{saving ? 'Saving...' : 'Save'}</button>
            <button className="clear-btn" onClick={cancelEdit} disabled={saving}>Cancel</button>
          </>
        )}
      </div>
      <h2>{(data.description ? (data.description.length > 100 ? data.description.slice(0, 99) + '…' : data.description) : instrumentId)}</h2>
      <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 16, gap: 16 }}>
        <div style={{ width: 380, height: 300, position: 'relative' }}>
          {hasRadarChart ? (
            <>
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={radarData} outerRadius="72%">
                  <PolarGrid />
                  <PolarAngleAxis
                    dataKey="metric"
                    axisLine={false}
                    tickLine={false}
                    tick={({ x, y, payload, textAnchor }) => {
                      const item = radarData.find((d) => d.metric === payload?.value)
                      if (!item || !Number.isFinite(x) || !Number.isFinite(y)) return null
                      return (
                        <text
                          x={x}
                          y={y}
                          textAnchor={textAnchor || 'middle'}
                          dominantBaseline="central"
                          fill="#ffffff"
                          fontSize={11}
                          fontWeight={600}
                          stroke="#0f172a"
                          strokeWidth={2}
                          paintOrder="stroke"
                        >
                          {formatNumberForDisplay(item.actual)}
                        </text>
                      )
                    }}
                  />
                  <PolarRadiusAxis
                    domain={[0, radarMax]}
                    tick={false}
                    axisLine={false}
                  />
                  <Tooltip
                    formatter={(value, name, payload) => {
                      if (name === 'delta_from_target') {
                        const item = payload && payload.payload ? payload.payload : null
                        const actual = item && Number.isFinite(item.actual)
                          ? formatNumberForDisplay(item.actual)
                          : '-'
                        const target = item && Number.isFinite(item.target)
                          ? formatNumberForDisplay(item.target)
                          : '-'
                        return [`Δ ${formatNumberForDisplay(Number(value))} | actual ${actual} | target ${target}`, 'distance']
                      }
                      return [formatNumberForDisplay(Number(value)), name]
                    }}
                  />
                  <Radar
                    name="distance"
                    dataKey="delta_from_target"
                    stroke="#206095"
                    fill="#206095"
                    fillOpacity={0.35}
                  />
                </RadarChart>
              </ResponsiveContainer>
              <div
                style={{
                  position: 'absolute',
                  left: '50%',
                  top: '50%',
                  transform: 'translate(-50%, -50%)',
                  fontSize: 12,
                  textAlign: 'center',
                  lineHeight: 1.2,
                  color: '#ffffff',
                  pointerEvents: 'none',
                }}
              >
                <div>{formatNumberForDisplay(targetPrice)}</div>
              </div>
            </>
          ) : (
            <div style={{ padding: 8, color: '#6b7280' }}>
              Spider chart unavailable: missing numeric PV/target values.
            </div>
          )}
        </div>
        <table className="instrument-table" style={{ border: 'none', alignSelf: 'flex-start' }}>
          <tbody>
            <tr>
              <td className="detail-key" style={{ border: 'none', padding: '6px 8px', fontWeight: 600 }}>instrument_id</td>
              <td className="detail-value" style={{ border: 'none', padding: '6px 8px' }}>
                {editMode ? renderEditor('instrument_id', mandatoryFields.instrument_id) : (mandatoryFields.instrument_id || '-')}
              </td>
            </tr>
            <tr>
              <td className="detail-key" style={{ border: 'none', padding: '6px 8px', fontWeight: 600 }}>model</td>
              <td className="detail-value" style={{ border: 'none', padding: '6px 8px' }}>
                {editMode ? renderEditor('model', mandatoryFields.model) : (mandatoryFields.model || '-')}
              </td>
            </tr>
            <tr>
              <td className="detail-key" style={{ border: 'none', padding: '6px 8px', fontWeight: 600 }}>currency</td>
              <td className="detail-value" style={{ border: 'none', padding: '6px 8px' }}>
                {editMode ? renderEditor('currency', mandatoryFields.currency) : (mandatoryFields.currency || '-')}
              </td>
            </tr>
            {editMode && (
              <tr>
                <td className="detail-key" style={{ border: 'none', padding: '6px 8px', fontWeight: 600 }}>
                  <input
                    type="text"
                    placeholder="new key"
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    style={{ width: '100%', padding: '4px 6px', fontSize: 13, boxSizing: 'border-box', border: '1px solid #444', borderRadius: 4, background: '#2a2a2a', color: '#fff' }}
                  />
                </td>
                <td className="detail-value" style={{ border: 'none', padding: '6px 8px' }}>
                  <input
                    type="text"
                    placeholder="new value"
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    style={{ width: '100%', padding: '4px 6px', fontSize: 13, boxSizing: 'border-box', border: '1px solid #444', borderRadius: 4, background: '#2a2a2a', color: '#fff' }}
                  />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <table className="instrument-table" style={{width: '100%', border: 'none'}}>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              <td className="detail-key" style={{border: 'none', padding: '6px 8px'}}>{row[0][0] || ''}</td>
              <td className="detail-value" style={{border: 'none', padding: '6px 8px'}}>{editMode ? renderEditor(row[0][0], row[0][1]) : (row[0][1] == null ? '-' : renderValue(row[0][0], row[0][1]))}</td>

              <td className="detail-key" style={{border: 'none', padding: '6px 8px', paddingLeft: '24px', borderLeft: '4px solid rgba(158,167,173,0.5)'}}>{row[1][0] || ''}</td>
              <td className="detail-value" style={{border: 'none', padding: '6px 8px'}}>{editMode ? renderEditor(row[1][0], row[1][1]) : (row[1][1] == null ? '-' : renderValue(row[1][0], row[1][1]))}</td>

              <td className="detail-key" style={{border: 'none', padding: '6px 8px', paddingLeft: '24px', borderLeft: '4px solid rgba(158,167,173,0.5)'}}>{row[2][0] || ''}</td>
              <td className="detail-value" style={{border: 'none', padding: '6px 8px'}}>{editMode ? renderEditor(row[2][0], row[2][1]) : (row[2][1] == null ? '-' : renderValue(row[2][0], row[2][1]))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(snack.visible || snackHiding) && (
        <div className={`snackbar snackbar--${snack.type || 'info'} ${snackHiding ? 'hide' : 'show'}`}>{snack.message}</div>
      )}
    </div>
  )
}
