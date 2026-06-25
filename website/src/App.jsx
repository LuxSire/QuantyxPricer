import React, { useEffect, useState, useMemo } from 'react'
import logo from '../logo_q.png'
import Instrument from './Instrument'
import TimeSeries from './TimeSeries'
import Sidebar from './Sidebar'
import { usePrices } from './hooks/usePrices'
import { useAsset } from './hooks/useAsset'
import DataTable from './components/DataTable'

function fmt(v) {
  if (v == null) return ''
  if (typeof v === 'number') return v.toFixed(3)
  return String(v)
}

function fmtPct(v) {
  if (v == null) return ''
  if (typeof v === 'number') return (v * 100).toFixed(3) + '%'
  // try parse
  const n = Number(v)
  if (!isNaN(n)) return (n * 100).toFixed(3) + '%'
  return String(v)
}

export default function App() {
  // Prefer VITE_API_URL. In production mode, default to Azure backend.
  const apiBase = useMemo(() => {
    if (typeof import.meta === 'undefined' || !import.meta.env) return ''
    if (import.meta.env.VITE_API_URL) return String(import.meta.env.VITE_API_URL).replace(/\/$/, '')
    if (import.meta.env.MODE === 'production') {
      return 'https://lux-pricer-eta2cxamh7evctdv.switzerlandnorth-01.azurewebsites.net'
    }
    return ''
  }, [])
  const [missingInstrumentIds, setMissingInstrumentIds] = useState([])
  const [underlyingAssets, setUnderlyingAssets] = useState([])
  const [pricingIds, setPricingIds] = useState([])
  const [snack, setSnack] = useState({ visible: false, message: '', type: 'info' })
  const [snackHiding, setSnackHiding] = useState(false)
  const [filterInstrument, setFilterInstrument] = useState('')
  const [filterModel, setFilterModel] = useState('')
  const [filterCurrency, setFilterCurrency] = useState('')
  const [route, setRoute] = useState(() => {
    const h = window.location.hash || ''
    if (h.startsWith('#/instrument/')) return h.replace('#/instrument/', '')
    return null
  })
  const [tsRoute, setTsRoute] = useState(() => {
    const h = window.location.hash || ''
    if (h.startsWith('#/timeseries/')) return h.replace('#/timeseries/', '')
    return null
  })
  
  const { rows, error, pricingAll, updatingCurves, pricing_single_asset } = usePrices(apiBase)
  const { fetchNopricedAssets, fetchUnderlyingAssets } = useAsset(apiBase)

  const createOnPriceAll = async () => {
    // This will be implemented based on your pricing logic
    console.log('Price all triggered')
  }

  const createOnUpdateCurves = async () => {
    // This will be implemented based on your curve update logic
    console.log('Update curves triggered')
  }

  useEffect(() => {
    let mounted = true


    async function fetchMissing() {
      const ids = await fetchNopricedAssets()
      if (!mounted) return
      setMissingInstrumentIds(ids)
    }

    fetchMissing()
    return () => { mounted = false }
  }, [apiBase, rows, fetchNopricedAssets])

  useEffect(() => {
    let mounted = true

    async function fetchUnderlying() {
      const assets = await fetchUnderlyingAssets()
      if (!mounted) return
      setUnderlyingAssets(assets)
    }

    fetchUnderlying()
    return () => { 
      mounted = false
    }
  }, [apiBase, fetchUnderlyingAssets])

  useEffect(() => {
    function onHash() {
      const h = window.location.hash || ''
      if (h.startsWith('#/instrument/')) { setRoute(h.replace('#/instrument/', '')); setTsRoute(null) }
      else if (h.startsWith('#/timeseries/')) { setTsRoute(h.replace('#/timeseries/', '')); setRoute(null) }
      else { setRoute(null); setTsRoute(null) }
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // auto-hide snackbar with hide animation
  useEffect(() => {
    let hideTimer = null
    let removeTimer = null
    if (snack.visible) {
      // schedule start hiding after 4s
      hideTimer = setTimeout(() => setSnackHiding(true), 4000)
    }
    if (snackHiding) {
      // after hide animation duration, actually remove the snackbar
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

  const priceOne = async (id) => {
    if (!id) {
      setSnack({ visible: true, message: 'No instrument id available', type: 'error' })
      return
    }
    setPricingIds(prev => [...prev, id])
    // First download market data from providers and insert into DB
    try {
      await pricing_single_asset(id, setSnack)
    } catch (e) {
      console.error('pricing_single_asset error', e)
    }

  }

  if (error) return <div className="error">Error: {error}</div>
  if (!rows) return <div>Loading data...</div>

  if (tsRoute) {
    return <TimeSeries instrumentId={tsRoute} apiBase={apiBase} />
  }

  if (route) {
    return <Instrument instrumentId={route} apiBase={apiBase} />
  }

  return (
    <div>
      <h1>
        <img src={logo} alt="Quantyx" style={{ height: 32, verticalAlign: 'middle', marginRight: 8 }} />
        Quantyx Pricer
      </h1>
      <p style={{ marginTop: 4 }}>Click an Instrument ID to view its details.</p>
      <div className="app-layout">
        <Sidebar
          models={Array.from(new Set(rows.map(r => r.model || (r.result && r.result.model) || '').filter(Boolean)))}
          currencies={Array.from(new Set(rows.map(r => r.currency || (r.result && r.result.currency) || '').filter(Boolean)))}
          filterInstrument={filterInstrument}
          setFilterInstrument={setFilterInstrument}
          filterModel={filterModel}
          setFilterModel={setFilterModel}
          filterCurrency={filterCurrency}
          setFilterCurrency={setFilterCurrency}
          clearAll={() => { setFilterInstrument(''); setFilterModel(''); setFilterCurrency('') }}
          apiBase={apiBase}
          onPriceAll={createOnPriceAll}
          pricingAll={pricingAll}
          onUpdateCurves={createOnUpdateCurves}
          updatingCurves={updatingCurves}
        />
        <div className="main-panel">
          <datalist id="instrument-ids">
            {rows && rows.map((r, i) => {
              const id = r.instrument_id || (r.result && r.result.instrument_id) || r.bond_file || ''
              return id ? <option key={i} value={id} /> : null
            })}
          </datalist>

          
      
      
      <DataTable
        columns={[
          { key: 'instrument_id', label: 'Instrument ID', className: 'mono' },
          { key: 'currency', label: 'Currency' },
          { key: 'pv', label: 'PV', className: 'center', value: (r) => fmt(r.result?.price_pct?.pv_note ?? r.result?.pv_note ?? r.result?.selected_npv) },
          { key: 'pv_worst', label: 'PV to worst', className: 'center', value: (r) => fmt(r.result?.price_pct?.pv_note_to_worst ?? r.result?.price_pct?.pv_note_to_worst_call ?? r.result?.npv_to_worst_call ?? '') },
          { key: 'pv_mat', label: 'PV to maturity', className: 'center', value: (r) => fmt(r.result?.price_pct?.pv_note_to_maturity ?? r.result?.npv_to_maturity ?? '') },
          { key: 'ytm', label: 'YTM', className: 'center', value: (r) => fmtPct(r.result?.ytm ?? r.result?.ytm_expected ?? r.result?.model_ytm_to_maturity ?? r.result?.yield_to_maturity) },
          { key: 'model', label: 'Model' },
        ]}
        data={rows}
        filters={{
          instrument_id: filterInstrument,
          model: filterModel,
          currency: filterCurrency,
        }}
        onRowAction={(r) => {
          const id = r.instrument_id || (r.result && r.result.instrument_id) || ''
          const busy = id && pricingIds.includes(id)
          return (
            <button
              title="pricing"
              disabled={busy}
              onClick={async (e) => {
                e.preventDefault()
                await priceOne(id)
              }}
            >
              {busy ? '⏳' : '⏱️'}
            </button>
          )
        }}
        renderRow={(r) => {
          const res = r.result || {}
          const id = r.instrument_id || res.instrument_id || r.bond_file || ''
          return (
            <>
              <td className="mono">
                <a href={`#/instrument/${id}${r.bond_file ? '::' + r.bond_file : ''}`}>{id}</a>
              </td>
              <td>{r.currency || res.currency || ''}</td>
              <td className="center">{fmt(res.price_pct?.pv_note ?? res.pv_note ?? res.selected_npv)}</td>
              <td className="center">{fmt(res.price_pct?.pv_note_to_worst ?? res.price_pct?.pv_note_to_worst_call ?? res.npv_to_worst_call ?? '')}</td>
              <td className="center">{fmt(res.price_pct?.pv_note_to_maturity ?? res.npv_to_maturity ?? '')}</td>
              <td className="center">{fmtPct(res.ytm ?? res.ytm_expected ?? res.model_ytm_to_maturity ?? res.yield_to_maturity)}</td>
              <td>{r.model || res.model || ''}</td>
            </>
          )
        }}
      />
          <div style={{ marginTop: 24 }}>
            <h2 style={{ marginBottom: 8 }}>Not Priced Instruments</h2>
            <DataTable
              columns={[
                { key: 'instrument_id', label: 'Instrument ID', className: 'mono' },
              ]}
              data={missingInstrumentIds.map(id => ({ instrument_id: id }))}
              onRowAction={(item) => {
                const instrumentId = item.instrument_id
                return (
                  <button
                    title="pricing"
                    disabled={pricingIds.includes(instrumentId)}
                    onClick={async (e) => {
                      e.preventDefault()
                      await priceOne(instrumentId)
                    }}
                  >
                    {pricingIds.includes(instrumentId) ? '⏳' : '⏱️'}
                  </button>
                )
              }}
              renderRow={(item) => (
                <>
                  <td className="mono">
                    <a href={`#/instrument/${item.instrument_id}::${item.instrument_id}.json`}>{item.instrument_id}</a>
                  </td>
                </>
              )}
              emptyMessage="No missing instruments."
            />
          </div>
          <div style={{ marginTop: 24 }}>
            <h2 style={{ marginBottom: 8 }}>Underlying Assets</h2>
            <DataTable
              columns={[
                { key: 'instrument_id', label: 'Instrument ID', className: 'mono' },
                { key: 'name', label: 'Name' },
                { key: 'asset_type', label: 'Asset Type' },
                { key: 'currency', label: 'Currency' },
              ]}
              data={underlyingAssets}
              onRowAction={(asset) => {
                const instrumentId = asset.instrument_id
                const busy = instrumentId && pricingIds.includes(instrumentId)
                return (
                  <button
                    title="pricing"
                    disabled={busy}
                    onClick={async (e) => {
                      e.preventDefault()
                      await priceOne(instrumentId)
                    }}
                  >
                    {busy ? '⏳' : '⏱️'}
                  </button>
                )
              }}
              renderRow={(asset) => (
                <>
                  <td className="mono">
                    <a href={`#/timeseries/${asset.instrument_id}`}>{asset.instrument_id}</a>
                  </td>
                  <td>{asset.name || ''}</td>
                  <td>{asset.asset_type || ''}</td>
                  <td>{asset.currency || ''}</td>
                </>
              )}
              emptyMessage="No underlying assets found."
            />
          </div>
        </div>
      </div>
      {/* Snackbar */}
      {(snack.visible || snackHiding) && (
        <div className={`snackbar snackbar--${snack.type || 'info'} ${snackHiding ? 'hide' : 'show'}`}>{snack.message}</div>
      )}
    </div>
  )
}

// Snackbar styles: simple inline component can be used in App
