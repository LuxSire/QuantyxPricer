import React, { useEffect, useState } from 'react'
import { usePrices } from './hooks/usePrices'

function fmtNum(v) {
  if (v == null) return '-'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(4) : String(v)
}

function computeVolatility(bars) {
  const sorted = [...bars].sort((a, b) => a.date < b.date ? -1 : 1)
  if (sorted.length < 2) return null
  const closes = sorted.map(b => Number(b.close)).filter(Number.isFinite)
  if (closes.length < 2) return null
  const returns = []
  for (let i = 1; i < closes.length; i++) {
    returns.push(closes[i] / closes[i - 1] - 1)
  }
  const mean = returns.reduce((s, r) => s + r, 0) / returns.length
  const variance = returns.reduce((s, r) => s + (r - mean) ** 2, 0) / (returns.length - 1)
  return Math.sqrt(variance) * Math.sqrt(252)
}

export default function TimeSeries({ instrumentId, apiBase = '' }) {
  const [bars, setBars] = useState(null)
  const [error, setError] = useState(null)
  const { fetchAssetTimeSeries } = usePrices(apiBase)

  useEffect(() => {
    let mounted = true
    async function load() {
      const data = await fetchAssetTimeSeries(instrumentId)
      if (!mounted) return
      if (data == null) {
        setError(`No time series found for ${instrumentId}`)
      } else {
        setBars(data)
      }
    }
    load()
    return () => { mounted = false }
  }, [instrumentId, apiBase])

  const back = (e) => {
    e.preventDefault()
    window.location.hash = ''
  }

  if (error) return (
    <div>
      <a href="#" onClick={back}>&larr; Back</a>
      <div className="error" style={{ marginTop: 12 }}>{error}</div>
    </div>
  )

  if (!bars) return (
    <div>
      <a href="#" onClick={back}>&larr; Back</a>
      <div style={{ marginTop: 12 }}>Loading time series for {instrumentId}…</div>
    </div>
  )

  const sorted = [...bars].sort((a, b) => a.date > b.date ? -1 : 1)
  const vol = computeVolatility(bars)

  return (
    <div>
      <a href="#" onClick={back}>&larr; Back</a>
      <h2 style={{ marginTop: 10, marginBottom: 4 }}>{instrumentId}</h2>
      <p style={{ marginBottom: 16, color: '#9ea7ad' }}>
        {sorted.length} bars
        {vol != null && (
          <span style={{ marginLeft: 16 }}>
            Annualised volatility: <strong>{(vol * 100).toFixed(2)}%</strong>
          </span>
        )}
      </p>
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th className="center">Open</th>
            <th className="center">High</th>
            <th className="center">Low</th>
            <th className="center">Close</th>
            <th className="center">Volume</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((bar, i) => (
            <tr key={i}>
              <td className="mono">{bar.date}</td>
              <td className="center">{fmtNum(bar.open)}</td>
              <td className="center">{fmtNum(bar.high)}</td>
              <td className="center">{fmtNum(bar.low)}</td>
              <td className="center">{fmtNum(bar.close)}</td>
              <td className="center">{bar.volume != null ? Number(bar.volume).toLocaleString() : '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
