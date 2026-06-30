import React, { useEffect, useState } from 'react'
import { formatNumberForDisplay, isPercentageKey } from './helper'
import { useAsset } from './hooks/useAsset'
import { usePrices } from './hooks/usePrices'
import DataChart from './components/DataChart'

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
  const [pricingSingle, setPricingSingle] = useState(false)
  const { pricing_single_asset } = usePrices(apiBase)
  const [deletedFields, setDeletedFields] = useState(new Set())
  const [deleteConfirm, setDeleteConfirm] = useState(null)

  const MANDATORY_KEYS = new Set(['instrument_id', 'model', 'currency', 'ir_curve', 'cds_curve'])
  const COUPON_KEYS_LIST = ['coupon_structure', 'fixed_coupon_rate', 'accrual_day_count', 'coupon_frequency', 'calendar', 'business_day_convention']
  const DATES_KEYS_LIST  = ['evaluation_date', 'issue_date', 'maturity_date', 'call_dates', 'date_generation']
  const CARD_KEYS = new Set(['instrument_id', 'model', 'currency', 'ir_curve', 'cds_curve', ...COUPON_KEYS_LIST, ...DATES_KEYS_LIST])

  const mandatoryFields = {
    instrument_id: data?.instrument_id || isin || '',
    model: data?.model || '',
    currency: data?.currency || '',
    ir_curve: data?.ir_curve || '',
    cds_curve: data?.cds_curve || '',
  }
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [snack, setSnack] = useState({ visible: false, message: '', type: 'info' })
  const [dialog, setDialog] = useState(null) // { key, value }
  const [snackHiding, setSnackHiding] = useState(false)
  const { fetchAssetFields, updateAsset } = useAsset(apiBase)
  const [modelOptions, setModelOptions] = useState([])
  const [modelFieldData, setModelFieldData] = useState([])
  const [fields, setFields] = useState([])
  const [curves, setCurves] = useState([])
  const irCurveOptions = curves.filter(c => c.curve_type && c.curve_type.toLowerCase().includes('ois')).map(c => c.curve_name)
  const cdsCurveOptions = curves.filter(c => c.curve_type === 'cds').map(c => c.curve_name)
  useEffect(() => {
    let mounted = true
    async function fetchJson() {
      try {
        if (!isin) {
          if (mounted) setError('No instrument ID available')
          return
        }
        const result = await fetchAssetFields(isin)
        if (!mounted) return
        if (result) {
          const { asset, fields: assetFields, allModels } = result
          const ensured = {
            ...asset,
            instrument_id: asset.instrument_id || isin,
            model: asset.model || '',
            currency: asset.currency || '',
          }
          setData(ensured)
          setFields(assetFields)
          setModelFieldData(allModels)
          setModelOptions(allModels.map(m => m.name).filter(Boolean))
        } else {
          setError('Could not fetch asset JSON for ' + isin)
        }
      } catch (e) {
        if (mounted) setError(String(e))
      }
    }
    fetchJson()
    return () => { mounted = false }
  }, [instrumentId, apiBase, bondFile, isin, fetchAssetFields])

  useEffect(() => {
    let mounted = true
    const base = String(apiBase || '').replace(/\/$/, '')
    fetch(`${base}/fetch_curves`)
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (mounted) setCurves(data || []) })
      .catch(() => {})
    return () => { mounted = false }
  }, [apiBase])

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

  // Ordered list [key, value, required] — required→optional→extra when fields available.
  const allEntries = (fields.length > 0 ? fields.map(f => [f.name, f.value, f.required]) : Object.entries(data).map(([k, v]) => [k, v, null]))
    .filter(([, v]) => v != null && v !== '' && (typeof v !== 'object' || Object.keys(v).length > 0))
  const colSize = Math.ceil(allEntries.length / 3)
  const col1 = allEntries.slice(0, colSize)
  const col2 = allEntries.slice(colSize, colSize * 2)
  const col3 = allEntries.slice(colSize * 2)

  const toFiniteNumber = (value) => {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }

  const sensitivityData = (priceResult?.sensitivity || [])
    .filter(s => Number.isFinite(s.spread_bp) && Number.isFinite(s.pv_note_pct))
    .map(s => ({ x: s.spread_bp, y: s.pv_note_pct }))


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
    return <pre>{typeof v === 'object' ? truncate(JSON.stringify(v), 50) : truncate(String(v), 50)}</pre>
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
    if (k === 'ir_curve') {
      const selected = value == null ? '' : String(value)
      return (
        <select value={selected} onChange={(e) => onDraftChange(k, e.target.value)}>
          <option value="">(select IR curve)</option>
          {irCurveOptions.map(opt => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      )
    }
    if (k === 'cds_curve') {
      const selected = value == null ? '' : String(value)
      return (
        <select value={selected} onChange={(e) => onDraftChange(k, e.target.value)}>
          <option value="">(select CDS curve)</option>
          {cdsCurveOptions.map(opt => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      )
    }
    if (k === 'model') {
      const selected = value == null ? '' : String(value)
      const opts = modelOptions.includes(selected) ? modelOptions : [...modelOptions, selected].filter(Boolean)
      return (
        <select value={selected} onChange={(e) => onDraftChange(k, e.target.value)}>
          <option value="">(select model)</option>
          {opts.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      )
    }
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
  // build table rows: each row contains up to three [key, value, required] tuples
  const entries1 = col1.filter(([, v]) => v != null && v !== '')
  const entries2 = col2.filter(([, v]) => v != null && v !== '')
  const entries3 = col3.filter(([, v]) => v != null && v !== '')
  const maxLen = Math.max(entries1.length, entries2.length, entries3.length)
  const rows = []
  for (let i = 0; i < maxLen; i++) {
    rows.push([
      entries1[i] || [null, null, null],
      entries2[i] || [null, null, null],
      entries3[i] || [null, null, null],
    ])
  }

  const allDisplayedEntries = [...entries1, ...entries2, ...entries3]

  const startEdit = () => {
    const initial = {}
    const source = fields.length > 0 ? fields.map(f => [f.name, f.value]) : Object.entries(data)
    for (const [k, v] of source) initial[k] = v
    for (const [k, v] of Object.entries(mandatoryFields)) initial[k] = v
    setDraftValues(initial)
    setDeletedFields(new Set())
    setEditMode(true)
  }

  const cancelEdit = () => {
    setEditMode(false)
    setDraftValues({})
    setDeletedFields(new Set())
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

      // Remove fields the user marked for deletion
      for (const k of deletedFields) {
        delete updated[k]
      }

      await updateAsset(updated, bondFile, isin)

      setData(updated)
      setEditMode(false)
      setDraftValues({})
      setDeletedFields(new Set())
      setNewKey('')
      setNewValue('')
      setSnack({ visible: true, message: 'Asset updated successfully', type: 'success' })
    } catch (e) {
      setSnack({ visible: true, message: `Save failed: ${String(e)}`, type: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const openFieldDialog = (k, fallbackValue) => {
    const current = Object.prototype.hasOwnProperty.call(draftValues, k) ? draftValues[k] : fallbackValue
    setDialog({ key: k, value: current })
  }

  const confirmFieldDialog = (newValue) => {
    if (dialog) onDraftChange(dialog.key, newValue)
    setDialog(null)
  }

  const renderEditCell = (k, fallbackValue) => {
    if (!k) return null
    const current = Object.prototype.hasOwnProperty.call(draftValues, k) ? draftValues[k] : fallbackValue
    const preview = current == null
      ? '—'
      : typeof current === 'object'
        ? JSON.stringify(current).slice(0, 60) + (JSON.stringify(current).length > 60 ? '…' : '')
        : String(current).slice(0, 60) + (String(current).length > 60 ? '…' : '')
    return (
      <div
        title="Click to edit"
        onClick={() => openFieldDialog(k, fallbackValue)}
        style={{
          cursor: 'pointer',
          padding: '3px 6px',
          borderRadius: 4,
          border: '1px dashed #555',
          minHeight: 24,
          color: current == null ? '#6b7280' : 'inherit',
          userSelect: 'none',
          fontSize: 13,
        }}
      >
        {preview}
      </div>
    )
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
        <button
          className="clear-btn clear-btn--api"
          onClick={() => { window.location.hash = `#/bond_data/${isin}` }}
        >Web</button>
        <button
          className="clear-btn"
          disabled={pricingSingle || editMode}
          onClick={async () => {
            setPricingSingle(true)
            await pricing_single_asset(isin, setSnack)
            setPricingSingle(false)
          }}
        >
          {pricingSingle ? 'Pricing...' : 'Price'}
        </button>
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>

        {/* 4 cards in a row */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap' }}>

          {/* Bond Setup */}
          <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: '#d4af37', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Bond Setup</span>
            </div>
            <table style={{ borderCollapse: 'collapse' }}>
              <tbody>
                {[['instrument_id', mandatoryFields.instrument_id], ['model', mandatoryFields.model], ['currency', mandatoryFields.currency], ['ir_curve', mandatoryFields.ir_curve], ['cds_curve', mandatoryFields.cds_curve]].map(([label, val]) => (
                  <tr key={label} style={{ borderBottom: '1px solid #1a2535' }}>
                    <td style={{ padding: '5px 10px', fontSize: 11, color: '#6b7f99', fontWeight: 600, whiteSpace: 'nowrap', background: '#0c1520' }}>{label}</td>
                    <td style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6', whiteSpace: 'nowrap' }}>
                      {editMode ? renderEditor(label, val) : (val || '-')}
                    </td>
                  </tr>
                ))}
                {editMode && (
                  <tr style={{ borderBottom: '1px solid #1a2535' }}>
                    <td style={{ padding: '5px 10px', background: '#0c1520' }}>
                      <input type="text" placeholder="new key" value={newKey} onChange={(e) => setNewKey(e.target.value)}
                        style={{ width: '100%', padding: '3px 6px', fontSize: 11, boxSizing: 'border-box', border: '1px solid #334155', borderRadius: 3, background: '#0a1320', color: '#e6eef6' }} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input type="text" placeholder="new value" value={newValue} onChange={(e) => setNewValue(e.target.value)}
                        style={{ width: '100%', padding: '3px 6px', fontSize: 11, boxSizing: 'border-box', border: '1px solid #334155', borderRadius: 3, background: '#0a1320', color: '#e6eef6' }} />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Coupon */}
          <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: '#34d399', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Coupon</span>
            </div>
            <table style={{ borderCollapse: 'collapse' }}>
              <tbody>
                {COUPON_KEYS_LIST.map(label => {
                  const val = data?.[label]
                  if (!editMode && (val == null || val === '')) return null
                  return (
                    <tr key={label} style={{ borderBottom: '1px solid #1a2535' }}>
                      <td style={{ padding: '5px 10px', fontSize: 11, color: '#6b7f99', fontWeight: 600, whiteSpace: 'nowrap', background: '#0c1520' }}>{label}</td>
                      <td style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6', whiteSpace: 'nowrap' }}>
                        {editMode ? renderEditor(label, val) : String(val)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Dates */}
          <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: '#f472b6', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Dates</span>
            </div>
            <table style={{ borderCollapse: 'collapse' }}>
              <tbody>
                {DATES_KEYS_LIST.map(label => {
                  const val = data?.[label]
                  if (!editMode && (val == null || val === '' || (Array.isArray(val) && val.length === 0))) return null
                  return (
                    <tr key={label} style={{ borderBottom: '1px solid #1a2535' }}>
                      <td style={{ padding: '5px 10px', fontSize: 11, color: '#6b7f99', fontWeight: 600, whiteSpace: 'nowrap', background: '#0c1520' }}>{label}</td>
                      <td style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6', whiteSpace: 'nowrap' }}>
                        {editMode ? renderEditor(label, val) : (Array.isArray(val) ? val.join(', ') : String(val))}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Price Result */}
          {priceResult && (() => {
            const pp = priceResult.price_pct || {}
            const entries = [
              ['selected_npv',           priceResult.selected_npv],
              ['valuation_mode',         priceResult.valuation_mode],
              ['spread_bp',              priceResult.spread_bp],
              ['pv_to_worst_call',       pp.pv_note_to_worst_call ?? pp.pv_note_to_worst],
              ['pv_to_first_call',       pp.pv_note_to_first_call],
              ['pv_to_maturity',         pp.pv_note_to_maturity],
              ['model_ytm_to_maturity',  priceResult.model_ytm_to_maturity],
              ['model_ytc_to_first_call',priceResult.model_ytc_to_first_call],
            ].filter(([, v]) => v != null)
            return (
              <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: '#60a5fa', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Price Result</span>
                </div>
                <table style={{ borderCollapse: 'collapse' }}>
                  <tbody>
                    {entries.map(([k, v]) => {
                      const isPct = k === 'model_ytm_to_maturity' || k === 'model_ytc_to_first_call'
                      const display = typeof v === 'number'
                        ? isPct ? formatNumberForDisplay(v, { scale: 100, suffix: '%' }) : formatNumberForDisplay(v)
                        : String(v)
                      return (
                        <tr key={k} style={{ borderBottom: '1px solid #1a2535' }}>
                          <td style={{ padding: '5px 10px', fontSize: 11, color: '#6b7f99', fontWeight: 600, whiteSpace: 'nowrap', background: '#0c1520' }}>{k}</td>
                          <td style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6', fontFamily: 'monospace', textAlign: 'right', whiteSpace: 'nowrap' }}>{display}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )
          })()}

        </div>

        <div style={{ width: 380, height: 300 }}>
          <DataChart data={sensitivityData} />
        </div>
      </div>
      {editMode && <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden', marginTop: 4 }}>
        <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
          <span style={{ fontSize: 10, fontWeight: 700, color: '#9aa6b2', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Fields</span>
        </div>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <tbody>
            {rows.map((row, ri) => {
              const mkKeyProps = (col, extra = {}) => {
                const k = col[0]
                const isDeletable = editMode && k && !MANDATORY_KEYS.has(k)
                const isDeleted = editMode && k && deletedFields.has(k)
                return {
                  className: isDeleted ? 'field-deleted' : undefined,
                  style: {
                    padding: '5px 10px', fontSize: 11, color: col[2] === true ? '#facc15' : '#6b7f99',
                    fontWeight: 600, whiteSpace: 'nowrap', background: '#0c1520',
                    opacity: isDeleted ? 0.4 : 1,
                    textDecoration: isDeleted ? 'line-through' : 'none',
                    cursor: isDeletable ? 'context-menu' : 'default',
                    ...extra,
                  },
                  onContextMenu: isDeletable ? (e) => { e.preventDefault(); setDeleteConfirm(k) } : undefined,
                  title: isDeletable ? 'Right-click to delete field' : undefined,
                }
              }
              return (
                <tr key={ri} style={{ borderBottom: '1px solid #1a2535' }}>
                  <td {...mkKeyProps(row[0])}>{row[0][0] || ''}</td>
                  <td style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6' }}>{!deletedFields.has(row[0][0]) && (editMode ? renderEditCell(row[0][0], row[0][1]) : (row[0][1] == null ? '-' : renderValue(row[0][0], row[0][1])))}</td>

                  <td {...mkKeyProps(row[1], { borderLeft: '1px solid #1a2d44' })}>{row[1][0] || ''}</td>
                  <td style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6' }}>{!deletedFields.has(row[1][0]) && (editMode ? renderEditCell(row[1][0], row[1][1]) : (row[1][1] == null ? '-' : renderValue(row[1][0], row[1][1])))}</td>

                  <td {...mkKeyProps(row[2], { borderLeft: '1px solid #1a2d44' })}>{row[2][0] || ''}</td>
                  <td style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6' }}>{!deletedFields.has(row[2][0]) && (editMode ? renderEditCell(row[2][0], row[2][1]) : (row[2][1] == null ? '-' : renderValue(row[2][0], row[2][1])))}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>}
      {deleteConfirm && (
        <DeleteFieldDialog
          fieldKey={deleteConfirm}
          onConfirm={() => {
            setDeletedFields(prev => new Set([...prev, deleteConfirm]))
            setDeleteConfirm(null)
          }}
          onClose={() => setDeleteConfirm(null)}
        />
      )}
      {dialog && (
        <FieldDialog
          fieldKey={dialog.key}
          fieldValue={dialog.value}
          isDateKey={isDateKey}
          isDayCountKey={isDayCountKey}
          toDateInputValue={toDateInputValue}
          modelOptions={modelOptions}
          modelFieldData={modelFieldData}
          irCurveOptions={irCurveOptions}
          cdsCurveOptions={cdsCurveOptions}
          onConfirm={confirmFieldDialog}
          onClose={() => setDialog(null)}
        />
      )}
      {(snack.visible || snackHiding) && (
        <div className={`snackbar snackbar--${snack.type || 'info'} ${snackHiding ? 'hide' : 'show'}`}>{snack.message}</div>
      )}
    </div>
  )
}

function FieldDialog({ fieldKey, fieldValue, isDateKey, isDayCountKey, toDateInputValue, modelOptions = [], modelFieldData = [], irCurveOptions = [], cdsCurveOptions = [], onConfirm, onClose }) {
  const DAY_COUNT_OPTIONS = ['Actual360', 'Actual365Fixed', 'Thirty360', '30/360', 'ActualActual', 'ACT/ACT (PERIODIC BASIS)', 'ACT/ACT (ICMA)']

  const toEditString = (v) => {
    if (v == null) return ''
    if (typeof v === 'object') return JSON.stringify(v, null, 2)
    return String(v)
  }

  const [localValue, setLocalValue] = useState(toEditString(fieldValue))

  const handleConfirm = () => {
    const trimmed = typeof localValue === 'string' ? localValue.trim() : localValue
    // try to parse JSON if it looks like an object/array
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try { onConfirm(JSON.parse(trimmed)); return } catch {}
    }
    onConfirm(localValue)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') onClose()
    if (e.key === 'Enter' && !e.shiftKey && !(e.target.tagName === 'TEXTAREA')) handleConfirm()
  }

  const renderInput = () => {
    if (fieldKey === 'model') {
      const opts = modelOptions.includes(localValue) ? modelOptions : [...modelOptions, localValue].filter(Boolean)
      const selectedModel = modelFieldData.find(m => m.name === localValue)
      return (
        <>
          <select
            autoFocus
            value={localValue}
            onChange={(e) => setLocalValue(e.target.value)}
            style={{ width: '100%', padding: '6px 8px', fontSize: 14, background: '#1e293b', color: '#f1f5f9', border: '1px solid #444', borderRadius: 4 }}
          >
            <option value="">(select model)</option>
            {opts.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          {selectedModel && (
            <div style={{ marginTop: 8, fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              <div><span style={{ color: '#cbd5e1', fontWeight: 600 }}>Required: </span>{(selectedModel.required_fields || []).map(f => f.name).join(', ') || '—'}</div>
              <div><span style={{ color: '#cbd5e1', fontWeight: 600 }}>Optional: </span>{(selectedModel.optional_fields || []).map(f => f.name).join(', ') || '—'}</div>
            </div>
          )}
        </>
      )
    }
    if (fieldKey === 'ir_curve') {
      return (
        <select
          autoFocus
          value={localValue}
          onChange={(e) => setLocalValue(e.target.value)}
          style={{ width: '100%', padding: '6px 8px', fontSize: 14, background: '#1e293b', color: '#f1f5f9', border: '1px solid #444', borderRadius: 4 }}
        >
          <option value="">(select IR curve)</option>
          {irCurveOptions.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      )
    }
    if (fieldKey === 'cds_curve') {
      return (
        <select
          autoFocus
          value={localValue}
          onChange={(e) => setLocalValue(e.target.value)}
          style={{ width: '100%', padding: '6px 8px', fontSize: 14, background: '#1e293b', color: '#f1f5f9', border: '1px solid #444', borderRadius: 4 }}
        >
          <option value="">(select CDS curve)</option>
          {cdsCurveOptions.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      )
    }
    if (isDayCountKey(fieldKey)) {
      const opts = DAY_COUNT_OPTIONS.includes(localValue) ? DAY_COUNT_OPTIONS : [...DAY_COUNT_OPTIONS, localValue].filter(Boolean)
      return (
        <select
          autoFocus
          value={localValue}
          onChange={(e) => setLocalValue(e.target.value)}
          style={{ width: '100%', padding: '6px 8px', fontSize: 14, background: '#1e293b', color: '#f1f5f9', border: '1px solid #444', borderRadius: 4 }}
        >
          <option value="">(empty)</option>
          {opts.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      )
    }
    if (isDateKey(fieldKey)) {
      return (
        <input
          autoFocus
          type="date"
          value={toDateInputValue(localValue)}
          onChange={(e) => setLocalValue(e.target.value)}
          style={{ width: '100%', padding: '6px 8px', fontSize: 14, background: '#1e293b', color: '#f1f5f9', border: '1px solid #444', borderRadius: 4 }}
        />
      )
    }
    // For objects/arrays or long values: textarea
    const isLong = localValue.length > 60 || localValue.includes('\n')
    const isObj = localValue.trim().startsWith('{') || localValue.trim().startsWith('[')
    if (isLong || isObj) {
      return (
        <textarea
          autoFocus
          value={localValue}
          onChange={(e) => setLocalValue(e.target.value)}
          rows={Math.max(6, Math.min(20, localValue.split('\n').length + 2))}
          style={{ width: '100%', padding: '6px 8px', fontSize: 13, fontFamily: 'monospace', background: '#1e293b', color: '#f1f5f9', border: '1px solid #444', borderRadius: 4, resize: 'vertical', boxSizing: 'border-box' }}
        />
      )
    }
    return (
      <input
        autoFocus
        type="text"
        value={localValue}
        onChange={(e) => setLocalValue(e.target.value)}
        onKeyDown={handleKeyDown}
        style={{ width: '100%', padding: '6px 8px', fontSize: 14, background: '#1e293b', color: '#f1f5f9', border: '1px solid #444', borderRadius: 4, boxSizing: 'border-box' }}
      />
    )
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
        style={{
          background: '#1e293b', borderRadius: 8, padding: 24, minWidth: 380, maxWidth: 600, width: '90%',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12, color: '#94a3b8', fontFamily: 'monospace' }}>
          {fieldKey}
        </div>
        {renderInput()}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
          <button className="clear-btn" onClick={onClose}>Cancel</button>
          <button className="clear-btn" onClick={handleConfirm}>Save</button>
        </div>
      </div>
    </div>
  )
}

function DeleteFieldDialog({ fieldKey, onConfirm, onClose }) {
  return (
    <div className="delete-field-backdrop" onClick={onClose}>
      <div className="delete-field-modal" onClick={(e) => e.stopPropagation()}>
        <div className="delete-field-title">Delete field</div>
        <p className="delete-field-body">
          Are you sure you want to delete <code className="delete-field-key">{fieldKey}</code>?
          The field will be removed from the asset when you save.
        </p>
        <div className="delete-field-actions">
          <button className="clear-btn" onClick={onClose}>Cancel</button>
          <button className="clear-btn clear-btn--termsheet" onClick={onConfirm}>Delete</button>
        </div>
      </div>
    </div>
  )
}
