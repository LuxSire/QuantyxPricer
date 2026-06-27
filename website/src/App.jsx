import React, { useEffect, useState, useMemo, useCallback } from 'react'
import logo from '../logo_q.png'
import Instrument from './Instrument'
import TimeSeries from './TimeSeries'
import Settings from './Settings'
import Sidebar from './Sidebar'
import Login from './Login'
import Register from './Register'
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
  const n = Number(v)
  if (!isNaN(n)) return (n * 100).toFixed(3) + '%'
  return String(v)
}

// All data hooks live here so they only mount when the user is authenticated.
function AuthenticatedApp({ apiBase, onLogout }) {
  const [missingInstrumentIds, setMissingInstrumentIds] = useState([])
  const [underlyingAssets, setUnderlyingAssets] = useState([])
  const [issuers, setIssuers] = useState([])
  const [issuerByInstrumentId, setIssuerByInstrumentId] = useState({})
  const [pricingIds, setPricingIds] = useState([])
  const [snack, setSnack] = useState({ visible: false, message: '', type: 'info' })
  const [snackHiding, setSnackHiding] = useState(false)
  const [filterInstrument, setFilterInstrument] = useState('')
  const [filterModel, setFilterModel] = useState('')
  const [filterCurrency, setFilterCurrency] = useState('')
  const [filterIssuer, setFilterIssuer] = useState('')
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
  const [settingsOpen, setSettingsOpen] = useState(() => window.location.hash === '#/settings')

  const { rows, error, pricingAll, updatingCurves, onPriceAll, onUpdateCurves, refreshPrices, pricing_single_asset, downloadPrice, downloadAndInsertPrices, downloadAndInsertAllPrices, downloadingAll } = usePrices(apiBase)
  const { fetchNopricedAssets, fetchUnderlyingAssets, fetchAllAssets } = useAsset(apiBase)

  const refreshMissing = useCallback(async () => {
    const ids = await fetchNopricedAssets()
    setMissingInstrumentIds(ids || [])
    await refreshPrices()
  }, [fetchNopricedAssets, refreshPrices])

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
    return () => { mounted = false }
  }, [apiBase, fetchUnderlyingAssets])

  useEffect(() => {
    let mounted = true
    fetchAllAssets().then((all) => {
      if (!mounted) return
      const unique = Array.from(new Set((all || []).map(a => a.issuer).filter(Boolean))).sort()
      setIssuers(unique)
      const map = {}
      ;(all || []).forEach(a => { if (a.instrument_id && a.issuer) map[a.instrument_id] = a.issuer })
      setIssuerByInstrumentId(map)
    })
    return () => { mounted = false }
  }, [fetchAllAssets])

  useEffect(() => {
    function onHash() {
      const h = window.location.hash || ''
      if (h.startsWith('#/instrument/')) { setRoute(h.replace('#/instrument/', '')); setTsRoute(null); setSettingsOpen(false) }
      else if (h.startsWith('#/timeseries/')) { setTsRoute(h.replace('#/timeseries/', '')); setRoute(null); setSettingsOpen(false) }
      else if (h === '#/settings') { setSettingsOpen(true); setRoute(null); setTsRoute(null) }
      else { setRoute(null); setTsRoute(null); setSettingsOpen(false) }
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

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

  const priceOne = async (id) => {
    if (!id) {
      setSnack({ visible: true, message: 'No instrument id available', type: 'error' })
      return
    }
    setPricingIds(prev => [...prev, id])
    try {
      await pricing_single_asset(id, setSnack)
    } catch (e) {
      console.error('pricing_single_asset error', e)
    }
  }

  if (error) return <div className="error">Error: {error}</div>
  if (!rows) return <div>Loading data...</div>

  if (settingsOpen) return <Settings apiBase={apiBase} />
  if (tsRoute) return <TimeSeries instrumentId={tsRoute} apiBase={apiBase} />
  if (route) return <Instrument instrumentId={route} apiBase={apiBase} />

  const visibleRows = filterIssuer
    ? rows.filter(r => {
        const id = r.instrument_id || (r.result && r.result.instrument_id) || ''
        return issuerByInstrumentId[id] === filterIssuer
      })
    : rows

  return (
    <div>
      <h1>
        <img src={logo} alt="Quantyx" style={{ height: 32, verticalAlign: 'middle', marginRight: 8 }} />
        Quantyx Pricer
        <button
          onClick={onLogout}
          style={{ marginLeft: 16, fontSize: 12, padding: '4px 10px', background: '#334155', color: '#94a3b8', border: '1px solid #475569', borderRadius: 4, cursor: 'pointer', verticalAlign: 'middle' }}
        >
          Sign out
        </button>
      </h1>
      <p style={{ marginTop: 4 }}>Click an Instrument ID to view its details.</p>
      <div className="app-layout">
        <Sidebar
          models={Array.from(new Set(rows.map(r => r.model || (r.result && r.result.model) || '').filter(Boolean)))}
          currencies={Array.from(new Set(rows.map(r => r.currency || (r.result && r.result.currency) || '').filter(Boolean)))}
          issuers={issuers}
          filterInstrument={filterInstrument}
          setFilterInstrument={setFilterInstrument}
          filterModel={filterModel}
          setFilterModel={setFilterModel}
          filterCurrency={filterCurrency}
          setFilterCurrency={setFilterCurrency}
          filterIssuer={filterIssuer}
          setFilterIssuer={setFilterIssuer}
          clearAll={() => { setFilterInstrument(''); setFilterModel(''); setFilterCurrency(''); setFilterIssuer('') }}
          apiBase={apiBase}
          onPriceAll={() => onPriceAll(setSnack)}
          pricingAll={pricingAll}
          onUpdateCurves={() => onUpdateCurves(setSnack)}
          updatingCurves={updatingCurves}
          onAssetSaved={refreshMissing}
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
              { key: '_datetime', label: 'Last', className: 'center' },
            ]}
            data={visibleRows}
            pageSize={10}
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
              const dt = r._datetime ? r._datetime.replace('T', ' ').slice(0, 16) : ''
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
                  <td className="center">{dt}</td>
                </>
              )
            }}
          />
          <div style={{ marginTop: 24 }}>
            <h2 style={{ marginBottom: 8 }}>Not Priced Instruments</h2>
            <DataTable
              columns={[{ key: 'instrument_id', label: 'Instrument ID', className: 'mono' }]}
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <h2 style={{ margin: 0 }}>Underlying Assets</h2>
              <button
                title="download and insert prices for all underlying assets"
                disabled={downloadingAll}
                onClick={async (e) => {
                  e.preventDefault()
                  await downloadAndInsertAllPrices(underlyingAssets, setSnack)
                }}
              >
                {downloadingAll ? '⏳' : '⬇️ all'}
              </button>
            </div>
            <DataTable
              columns={[
                { key: 'instrument_id', label: 'Instrument ID', className: 'mono' },
                { key: 'name', label: 'Name' },
                { key: 'asset_type', label: 'Asset Type' },
                { key: 'currency', label: 'Currency' },
                { key: 'last_close', label: 'Last Close', className: 'center' },
                { key: 'last_close_date', label: 'Date', className: 'center' },
              ]}
              data={underlyingAssets}
              onRowAction={(asset) => {
                const instrumentId = asset.instrument_id
                const busy = instrumentId && pricingIds.includes(instrumentId)
                return (
                  <button
                    title="download and insert prices"
                    disabled={busy}
                    onClick={async (e) => {
                      e.preventDefault()
                      await downloadAndInsertPrices(instrumentId, setSnack)
                    }}
                  >
                    {busy ? '⏳' : '⬇️'}
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
                  <td className="center">{asset.last_close != null ? Number(asset.last_close).toFixed(4) : ''}</td>
                  <td className="center">{asset.last_close_date || ''}</td>
                </>
              )}
              emptyMessage="No underlying assets found."
            />
          </div>
        </div>
      </div>
      {(snack.visible || snackHiding) && (
        <div className={`snackbar snackbar--${snack.type || 'info'} ${snackHiding ? 'hide' : 'show'}`}>{snack.message}</div>
      )}
      <footer style={{ marginTop: 40, padding: '12px 0', textAlign: 'left', color: '#475569', fontSize: 12, borderTop: '1px solid #1e293b' }}>
        v.0.3.0
      </footer>
    </div>
  )
}

function loadStoredUser() {
  try { return JSON.parse(localStorage.getItem('quantyx_user') || 'null') } catch { return null }
}

export default function App() {
  const apiBase = useMemo(() => {
    if (typeof import.meta === 'undefined' || !import.meta.env) return ''
    if (import.meta.env.VITE_API_URL) return String(import.meta.env.VITE_API_URL).replace(/\/$/, '')
    if (import.meta.env.MODE === 'production') {
      return 'https://lux-pricer-eta2cxamh7evctdv.switzerlandnorth-01.azurewebsites.net'
    }
    return ''
  }, [])

  const [currentUser, setCurrentUser] = useState(() => loadStoredUser())
  const [authRoute, setAuthRoute] = useState(() => {
    const h = window.location.hash || ''
    if (h === '#/register') return 'register'
    return null
  })

  useEffect(() => {
    function onHash() {
      const h = window.location.hash || ''
      if (h === '#/login') setAuthRoute('login')
      else if (h === '#/register') setAuthRoute('register')
      else setAuthRoute(null)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleLogin = (user) => {
    setCurrentUser(user)
    setAuthRoute(null)
    window.location.hash = ''
  }

  const handleLogout = () => {
    localStorage.removeItem('quantyx_user')
    setCurrentUser(null)
    window.location.hash = '#/login'
  }

  if (authRoute === 'register') return <Register apiBase={apiBase} />

  if (!currentUser || authRoute === 'login') {
    return <Login apiBase={apiBase} onLogin={handleLogin} />
  }

  return <AuthenticatedApp apiBase={apiBase} onLogout={handleLogout} />
}
