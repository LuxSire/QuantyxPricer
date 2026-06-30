import React, { useEffect, useState } from 'react'
import { usePrices } from './hooks/usePrices'

const DESC_KEYS = ['cfi_code', 'isin_code', 'common_code', 'bbgid_code', 'cusip_regs', 'bbgid',
  'bbgid_ticker', 'kind_name_eng', 'bond_rank_name_eng', 'document_eng']

const SKIP_KEYS = new Set(['instrument_id', 'provider', '_datetime', 'code', '_code',
  'emitent_full_name_eng', 'cupon_eng', ...DESC_KEYS])

function classify(key) {
  const k = key.toLowerCase()
  if (k.includes('ytc') || k.includes('yto') || k.includes('yield') || k.includes('profit') || k === 'curr_coupon_rate')
    return 'yields'
  if (k.includes('price') || k.includes('quote') || k === 'aci')
    return 'prices'
  if (k.includes('spread'))
    return 'spreads'
  if (k.includes('dur') || k.includes('pvbp') || k.includes('convexity') || k.includes('years_to') || k === 'margin')
    return 'risk'
  return 'general'
}

function formatValue(key, value) {
  if (value == null) return '—'
  // already a formatted string (e.g. "3.778%")
  if (typeof value === 'string' && value.endsWith('%')) return value
  if (typeof value === 'object') return JSON.stringify(value)
  const k = key.toLowerCase()
  const num = parseFloat(value)
  if (!isNaN(num)) {
    if (k.includes('ytc') || k.includes('yto') || k.includes('yield') || k.includes('profit'))
      return (num * 100).toFixed(3) + '%'
    if (k.includes('spread'))
      return num.toFixed(3)
    if (k === 'curr_coupon_rate')
      return num.toFixed(3) + '%'
    if (k.includes('dur') || k.includes('pvbp') || k.includes('convexity'))
      return num.toFixed(4)
  }
  return String(value)
}

function SectionTable({ entries }) {
  if (!entries.length) return null
  return (
    <table style={{ borderCollapse: 'collapse', width: '100%' }}>
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} style={{ borderBottom: '1px solid #1a2535' }}>
            <td style={{ padding: '5px 10px', fontSize: 12, color: '#6b7f99', fontWeight: 600, whiteSpace: 'nowrap', width: '50%', background: '#0c1520' }}>
              {k}
            </td>
            <td style={{ padding: '5px 10px', fontSize: 12, color: '#e6eef6', fontFamily: 'monospace', textAlign: 'right' }}>
              {formatValue(k, v)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Section({ title, entries, color = '#d4af37' }) {
  if (!entries.length) return null
  return (
    <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden' }}>
      <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
        <span style={{ fontSize: 10, fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          {title}
        </span>
      </div>
      <SectionTable entries={entries} />
    </div>
  )
}

export default function BondData({ instrumentId, apiBase }) {
  const { fetchPricesCbonds } = usePrices(apiBase)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let mounted = true
    fetchPricesCbonds().then(rows => {
      if (!mounted) return
      const match = (rows || []).find(r => r.instrument_id === instrumentId || r.code === instrumentId)
      if (match) { setData(match); setError(null) }
      else { setData(null); setError(`No cbonds data found for ${instrumentId}`) }
      setLoading(false)
    })
    return () => { mounted = false }
  }, [instrumentId])

  const grouped = { prices: [], yields: [], spreads: [], risk: [], general: [] }
  if (data) {
    Object.entries(data)
      .filter(([k]) => !SKIP_KEYS.has(k) && data[k] != null && data[k] !== '')
      .forEach(([k, v]) => grouped[classify(k)].push([k, v]))
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0a1320', color: '#e6eef6', fontFamily: 'inherit', padding: '20px 24px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <a href={`#/instrument/${instrumentId}`} style={{ color: '#9aa6b2', textDecoration: 'none', fontSize: 14 }}>
            ← Back
          </a>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>
            <span style={{ color: '#ffd886' }}>{instrumentId}</span>
          </h2>
          {data && data._datetime && (
            <span style={{ fontSize: 11, color: '#4a5568' }}>updated {data._datetime.slice(0, 10)}</span>
          )}
        </div>

        {data && (data.emitent_full_name_eng || data.cupon_eng) && (
          <div style={{ textAlign: 'right', maxWidth: 420, lineHeight: 1.6 }}>
            {data.emitent_full_name_eng && (
              <div style={{ fontSize: 15, fontWeight: 700, color: '#e6eef6' }}>
                {data.emitent_full_name_eng}
              </div>
            )}
            {data.cupon_eng && (
              <div style={{ fontSize: 11, color: '#7a8fa6', marginTop: 3 }}>
                {data.cupon_eng}
              </div>
            )}
          </div>
        )}
      </div>

      {loading && <div style={{ color: '#4a5568', fontSize: 14 }}>Loading…</div>}
      {error && <div style={{ color: '#f87171', fontSize: 14 }}>{error}</div>}

      {!loading && !error && data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* Description */}
          {DESC_KEYS.some(k => data[k] != null && data[k] !== '') && (
            <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden' }}>
              <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
                <span style={{ fontSize: 10, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Description</span>
              </div>
              <table style={{ borderCollapse: 'collapse' }}>
                <tbody>
                  {DESC_KEYS.map(k => {
                    const v = data[k]
                    if (v == null || v === '') return null
                    return (
                      <tr key={k} style={{ borderBottom: '1px solid #1a2535' }}>
                        <td style={{ padding: '5px 10px', fontSize: 12, color: '#6b7f99', fontWeight: 600, whiteSpace: 'nowrap', background: '#0c1520' }}>{k}</td>
                        <td style={{ padding: '5px 10px', fontSize: 12, color: '#e6eef6', whiteSpace: 'nowrap', fontFamily: 'monospace' }}>{String(v)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Prices + Yields side by side */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Section title="Prices" entries={grouped.prices} color="#d4af37" />
            <Section title="Yields" entries={grouped.yields} color="#60a5fa" />
          </div>

          {/* Spreads + Risk side by side */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Section title="Spreads" entries={grouped.spreads} color="#f472b6" />
            <Section title="Duration & Risk" entries={grouped.risk} color="#34d399" />
          </div>

          {/* General — full width, 2-column grid inside */}
          {grouped.general.length > 0 && (
            <div style={{ background: '#0d1a27', border: '1px solid #1a2d44', borderRadius: 6, overflow: 'hidden' }}>
              <div style={{ padding: '6px 10px', background: '#0b1520', borderBottom: '1px solid #1a2d44' }}>
                <span style={{ fontSize: 10, fontWeight: 700, color: '#9aa6b2', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  General
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
                {grouped.general.map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', borderBottom: '1px solid #1a2535' }}>
                    <div style={{ padding: '5px 10px', fontSize: 11, color: '#6b7f99', fontWeight: 600, width: 200, whiteSpace: 'nowrap', flexShrink: 0, background: '#0c1520' }}>
                      {k}
                    </div>
                    <div style={{ padding: '5px 10px', fontSize: 11, color: '#e6eef6', wordBreak: 'break-word', flex: 1 }}>
                      {formatValue(k, v)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
