import React, { useState, useCallback } from 'react'
import { useAsset } from './hooks/useAsset'
import FileUploaderDialog from './components/FileUploaderDialog'

export default function Sidebar({
  models = [],
  currencies = [],
  issuers = [],
  filterInstrument,
  setFilterInstrument,
  filterModel,
  setFilterModel,
  filterCurrency,
  setFilterCurrency,
  filterIssuer,
  setFilterIssuer,
  clearAll,
  onPriceAll,
  pricingAll,
  onUpdateCurves,
  updatingCurves,
  onCurves,
  apiBase,
  onAssetSaved,
}) {
  const [showUploader, setShowUploader] = useState(false)
  const [showLookup, setShowLookup] = useState(false)
  const [lookupCode, setLookupCode] = useState('')
  const [lookupResult, setLookupResult] = useState(null)
  const [lookupError, setLookupError] = useState('')
  const [lookupLoading, setLookupLoading] = useState(false)
  const { fetchAsset } = useAsset(apiBase)

  const runLookup = useCallback(async () => {
    const query = String(lookupCode || '').trim()
    if (!query) {
      setLookupError('Enter an instrument code')
      return
    }
    setLookupLoading(true)
    setLookupError('')
    setLookupResult(null)
    try {
      const result = await fetchAsset(query)
      if (result === null) {
        setLookupError(`No asset found for ${query}`)
      } else {
        setLookupResult(result)
      }
    } catch (err) {
      setLookupError(String(err))
    } finally {
      setLookupLoading(false)
    }
  }, [fetchAsset, lookupCode])

  return (
    <aside className="sidebar">

      {/* TOP: primary action */}
      <div className="sidebar-top">
        <div className="sidebar-btn-row">
          <button
            className="clear-btn"
            onClick={onPriceAll}
            disabled={pricingAll}
            title="Price all instruments"
          >
            {pricingAll ? '⏳ Pricing...' : '⏱️ Price All'}
          </button>
        </div>
      </div>

      {/* SCROLLABLE MIDDLE: filters + tools */}
      <div className="sidebar-scroll">
        <h3 className="sidebar-title">Filters</h3>

        <div className="filter-group">
          <label>Instrument ID</label>
          <input
            list="instrument-ids"
            value={filterInstrument}
            onChange={(e) => setFilterInstrument(e.target.value)}
            placeholder="Type or pick..."
          />
        </div>

        <div className="filter-group">
          <label>Model</label>
          <select value={filterModel} onChange={(e) => setFilterModel(e.target.value)}>
            <option value="">(all)</option>
            {models.map((m, i) => <option key={i} value={m}>{m}</option>)}
          </select>
        </div>

        <div className="filter-group">
          <label>Currency</label>
          <select value={filterCurrency} onChange={(e) => setFilterCurrency(e.target.value)}>
            <option value="">(all)</option>
            {currencies.map((c, i) => <option key={i} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="filter-group">
          <label>Issuer</label>
          <select value={filterIssuer} onChange={(e) => setFilterIssuer(e.target.value)}>
            <option value="">(all)</option>
            {issuers.map((s, i) => <option key={i} value={s}>{s}</option>)}
          </select>
        </div>

        <div className="sidebar-actions">
          <div className="sidebar-btn-row">
            <button className="clear-btn" onClick={() => setShowLookup(true)} title="Lookup asset">🔍 Lookup</button>
            <button className="clear-btn" onClick={() => setShowUploader(true)}>Add</button>
            <button className="clear-btn" onClick={clearAll}>Clear</button>
          </div>
        </div>
      </div>

      {/* CURVES SECTION */}
      <div className="sidebar-curves">
        <div className="sidebar-section-title">📈 Curves</div>
        <div className="sidebar-btn-row">
          <button className="clear-btn" onClick={onCurves} title="View swap curves">
            View
          </button>
          <button
            className="clear-btn clear-btn--update-curves"
            onClick={onUpdateCurves}
            disabled={updatingCurves}
            title="Update swap curves (ECB, Fed, CDS)"
          >
            {updatingCurves ? '⏳ Updating...' : '💾 Update'}
          </button>
        </div>
      </div>

      {/* BOTTOM: links */}
      <div className="sidebar-bottom">
        <a
          className="clear-btn clear-btn--api"
          href={`${(apiBase || '').replace(/\/$/, '')}/docs`}
          target="_blank"
          rel="noreferrer"
          title="Open API documentation"
        >
          API
        </a>
        <a
          className="clear-btn"
          href="#/settings"
          title="Model settings"
        >
          Settings
        </a>
      </div>

      {/* MODALS */}
      {showLookup && (
        <div className="lookup-backdrop" onClick={() => setShowLookup(false)}>
          <div className="lookup-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Lookup Asset</h3>
            <div className="filter-group">
              <label>Instrument Code</label>
              <input
                value={lookupCode}
                onChange={(e) => setLookupCode(e.target.value)}
                placeholder="Enter code"
              />
            </div>
            <div className="lookup-modal-actions">
              <button
                type="button"
                className="clear-btn"
                onClick={runLookup}
                disabled={lookupLoading}
              >
                {lookupLoading ? 'Searching...' : 'Search'}
              </button>
              <button
                type="button"
                className="clear-btn clear-btn--cancel"
                onClick={() => {
                  setShowLookup(false)
                  setLookupError('')
                  setLookupCode('')
                  setLookupResult(null)
                }}
              >
                Cancel
              </button>
            </div>
            {lookupError && <div className="error">{lookupError}</div>}
            {lookupResult !== null && (
              <div className="lookup-result">
                <h4>Result</h4>
                <pre>{JSON.stringify(lookupResult, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>
      )}

      {showUploader && (
        <FileUploaderDialog
          apiBase={apiBase}
          onClose={() => setShowUploader(false)}
          onAssetSaved={onAssetSaved}
        />
      )}
    </aside>
  )
}
