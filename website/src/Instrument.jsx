import React, { useEffect, useState } from 'react'

export default function Instrument({ instrumentId }) {
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

  useEffect(() => {
    let mounted = true
    async function fetchJson() {
      try {
        const paths = []
        if (bondFile) {
          // try explicit bond file first (both absolute and relative)
          paths.push(`/assets/${bondFile}`)
          paths.push(`assets/${bondFile}`)
          paths.push(`./assets/${bondFile}`)
          paths.push(`/assets/${bondFile.replace(/\\s+/g, '')}`)
        }
        // fallbacks based on isin (try .json and no-leading-slash variants)
        if (isin) {
          paths.push(`/assets/${isin}.json`)
          paths.push(`assets/${isin}.json`)
          paths.push(`./assets/${isin}.json`)
          paths.push(`/assets/${isin.toUpperCase()}.json`)
          paths.push(`/assets/${isin}.JSON`)
        }
        for (const p of paths) {
          try {
            const r = await fetch(p)
            if (!r.ok) continue
            const j = await r.json()
            if (!mounted) return
            setData(j)
            return
          } catch (e) {
            continue
          }
        }
        if (mounted) setError('Could not fetch asset JSON for ' + (bondFile || isin))
      } catch (e) {
        if (mounted) setError(String(e))
      }
    }
    fetchJson()
    return () => { mounted = false }
  }, [instrumentId])

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

  // Render properties elegantly as definition list
  const entries = Object.entries(data)
  const nestedKeys = new Set(['collateral', 'swap', 'csa', 'valuation_adjustments'])

  return (
    <div>
      <a href="#" onClick={(e)=>{e.preventDefault(); window.location.hash=''}}>&larr; Back</a>
      <h2>{data.description || instrumentId}</h2>
      <dl className="instrument-details">
        {entries.map(([k,v], idx) => (
          <div key={k} className="detail-row">
            <dt className="detail-key">{k}</dt>
            <dd className="detail-value">
              {v && typeof v === 'object' && nestedKeys.has(k) ? (
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
              ) : (k === 'call_dates' && Array.isArray(v) ? (
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
              ) : (
                <pre>{typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)}</pre>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
