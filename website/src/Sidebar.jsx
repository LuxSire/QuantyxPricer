import React, { useState, useCallback } from 'react'
import { useAsset } from './hooks/useAsset'

export default function Sidebar({
  models = [],
  currencies = [],
  filterInstrument,
  setFilterInstrument,
  filterModel,
  setFilterModel,
  filterCurrency,
  setFilterCurrency,
  clearAll,
  onPriceAll,
  pricingAll,
  onUpdateCurves,
  updatingCurves,
  apiBase,
}) {
  const [showUploader, setShowUploader] = useState(false)
  const [uploadMode, setUploadMode] = useState('json')
  const [showLookup, setShowLookup] = useState(false)
  const [lookupCode, setLookupCode] = useState('')
  const [lookupResult, setLookupResult] = useState(null)
  const [lookupError, setLookupError] = useState('')
  const [lookupLoading, setLookupLoading] = useState(false)
  const { loading: uploading, error: uploadError, uploadJson, uploadTermsheet, fetchAsset } = useAsset(apiBase)

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
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="clear-btn"
            onClick={onPriceAll}
            disabled={pricingAll}
            title="Price all instruments"
          >
            {pricingAll ? '⏳ Pricing...' : '⏱️ Price All'}
          </button>
          <button
            className="clear-btn clear-btn--update-curves"
            onClick={onUpdateCurves}
            disabled={updatingCurves}
            title="Update swap curves (ECB)"
          >
            {updatingCurves ? '⏳ Updating...' : '💾 Update Curves'}
          </button>
        </div>
      </div>
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

      <div style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="clear-btn" onClick={() => setShowLookup(true)} title="Lookup asset">🔍 Lookup</button>
          <button className="clear-btn" onClick={() => setShowUploader(true)}>Add</button>
          <button className="clear-btn" onClick={clearAll}>Clear filters</button>
        </div>
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
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
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
              {lookupError && <div className="error" style={{ marginTop: 10 }}>{lookupError}</div>}
              {lookupResult !== null && (
                <div className="lookup-result" style={{ marginTop: 16 }}>
                  <h4>Result</h4>
                  <pre>{JSON.stringify(lookupResult, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        )}
        {showUploader && (
          <div className="uploader-backdrop" onClick={() => setShowUploader(false)}>
            <div className="uploader-modal" onClick={(e) => e.stopPropagation()}>
              <h3>Upload Asset</h3>
              <div style={{ marginBottom: 10, display: 'flex', gap: 14 }}>
                <label>
                  <input
                    type="radio"
                    name="upload-mode"
                    value="json"
                    checked={uploadMode === 'json'}
                    onChange={() => setUploadMode('json')}
                  />{' '}
                  JSON
                </label>
                <label>
                  <input
                    type="radio"
                    name="upload-mode"
                    value="termsheet"
                    checked={uploadMode === 'termsheet'}
                    onChange={() => setUploadMode('termsheet')}
                  />{' '}
                  Termsheet (PDF)
                </label>
              </div>
              <input
                type="file"
                accept={uploadMode === 'termsheet' ? '.pdf' : '.json'}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (!f) return
                  if (uploadMode === 'termsheet') {
                    uploadTermsheet(f)
                      .then(() => {
                        console.log('Termsheet uploaded')
                        setShowUploader(false)
                      })
                      .catch(() => {})
                  } else {
                    f.text()
                      .then((txt) => JSON.parse(txt))
                      .then((obj) => uploadJson(obj))
                      .then(() => {
                        console.log('JSON uploaded')
                        setShowUploader(false)
                      })
                      .catch((err) => console.error(err))
                  }
                }}
              />
              {uploadError && <div style={{ color: '#dc2626', marginTop: 8 }}>{uploadError}</div>}
              {uploading && <div style={{ color: '#0891b2', marginTop: 8 }}>Uploading...</div>}
              <button onClick={() => setShowUploader(false)} style={{ marginTop: 12 }}>Close</button>
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <a
          className="clear-btn clear-btn--api"
          href={`${(apiBase || '').replace(/\/$/, '')}/docs`}
          target="_blank"
          rel="noreferrer"
          style={{ display: 'inline-block', textDecoration: 'none' }}
          title="Open API documentation"
        >
          API
        </a>
      </div>
    </aside>
  )
}
