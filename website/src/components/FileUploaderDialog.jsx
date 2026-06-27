import React, { useState, useEffect } from 'react'
import { useAsset } from '../hooks/useAsset'

export default function FileUploaderDialog({ apiBase, onClose, onAssetSaved }) {
  const [tab, setTab] = useState('web')
  const [isin, setIsin] = useState('')
  const [sources, setSources] = useState({})
  const [source, setSource] = useState('')
  const [fetchedData, setFetchedData] = useState(null)
  const [fetchError, setFetchError] = useState('')
  const [fetchLoading, setFetchLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const { loading: uploading, error: uploadError, uploadJson, uploadTermsheet, fetchWebSources } = useAsset(apiBase)

  useEffect(() => {
    fetchWebSources().then((data) => {
      setSources(data)
      setSource(Object.keys(data)[0] || '')
    })
  }, [fetchWebSources])

  const pageUrl = source && isin.trim() ? sources[source]?.replace('{isin}', isin.trim().toUpperCase()) : null

  const handleFetch = async () => {
    const id = isin.trim().toUpperCase()
    if (!id || !source) return
    setFetchLoading(true)
    setFetchError('')
    setFetchedData(null)
    try {
      const res = await fetch(`${apiBase}/fetch_asset_web?isin=${encodeURIComponent(id)}&source=${encodeURIComponent(source)}`)
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || res.statusText)
      }
      setFetchedData(await res.json())
    } catch (e) {
      setFetchError(String(e.message || e))
    } finally {
      setFetchLoading(false)
    }
  }

  const handleSave = async () => {
    if (!fetchedData) return
    setSaving(true)
    try {
      await uploadJson(fetchedData)
      onAssetSaved && onAssetSaved()
      onClose()
    } catch (e) {
      setFetchError(String(e.message || e))
    } finally {
      setSaving(false)
    }
  }

  const TAB_STYLE = (active) => ({
    padding: '7px 18px',
    background: active ? '#1e3a5f' : 'transparent',
    color: active ? '#93c5fd' : '#64748b',
    border: 'none',
    borderBottom: active ? '2px solid #3b82f6' : '2px solid transparent',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: active ? 600 : 400,
  })

  return (
    <div className="uploader-backdrop" onClick={onClose}>
      <div
        className="uploader-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 620, maxWidth: '95vw', maxHeight: '90vh', overflowY: 'auto' }}
      >
        <h3 style={{ marginBottom: 14 }}>Add Asset</h3>

        <div style={{ display: 'flex', borderBottom: '1px solid #334155', marginBottom: 18 }}>
          <button style={TAB_STYLE(tab === 'web')} onClick={() => setTab('web')}>Web</button>
          <button style={TAB_STYLE(tab === 'json')} onClick={() => setTab('json')}>Upload JSON</button>
          <button style={TAB_STYLE(tab === 'pdf')} onClick={() => setTab('pdf')}>Termsheet PDF</button>
        </div>

        {tab === 'web' && (
          <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <input
                value={isin}
                onChange={(e) => setIsin(e.target.value.toUpperCase())}
                onKeyDown={(e) => e.key === 'Enter' && handleFetch()}
                placeholder="Instrument ID / ISIN"
                style={{ flex: 1, minWidth: 0 }}
              />
              <select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                style={{ minWidth: 180 }}
              >
                {Object.keys(sources).map((k) => (
                  <option key={k} value={k}>{k.replace(/_/g, ' ')}</option>
                ))}
              </select>
              <button
                className="clear-btn"
                onClick={handleFetch}
                disabled={!isin.trim() || !source || fetchLoading}
              >
                {fetchLoading ? 'Fetching…' : 'Fetch'}
              </button>
              {pageUrl && (
                <a
                  href={pageUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="clear-btn"
                  style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
                  title="Open source page"
                >
                  🔗
                </a>
              )}
            </div>

            {fetchError && (
              <div style={{ color: '#dc2626', marginBottom: 10, fontSize: 13 }}>{fetchError}</div>
            )}

            {fetchedData && (
              <>
                <textarea
                  readOnly
                  value={JSON.stringify(fetchedData, null, 2)}
                  style={{
                    width: '100%',
                    height: 300,
                    fontFamily: 'monospace',
                    fontSize: 12,
                    background: '#0f172a',
                    color: '#e2e8f0',
                    border: '1px solid #334155',
                    borderRadius: 4,
                    padding: 10,
                    boxSizing: 'border-box',
                    resize: 'vertical',
                  }}
                />
                <button className="clear-btn" onClick={handleSave} disabled={saving} style={{ marginTop: 10 }}>
                  {saving ? 'Saving…' : 'Save Asset'}
                </button>
              </>
            )}
          </div>
        )}

        {tab === 'json' && (
          <div>
            <div style={{ marginBottom: 10, color: '#94a3b8', fontSize: 12 }}>
              Upload an asset JSON file directly.
            </div>
            <input
              type="file"
              accept=".json"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (!f) return
                f.text()
                  .then((txt) => JSON.parse(txt))
                  .then((obj) => uploadJson(obj))
                  .then(() => { onAssetSaved && onAssetSaved(); onClose() })
                  .catch((err) => console.error(err))
              }}
            />
            {uploadError && <div style={{ color: '#dc2626', marginTop: 10 }}>{uploadError}</div>}
            {uploading && <div style={{ color: '#0891b2', marginTop: 10 }}>Uploading…</div>}
          </div>
        )}

        {tab === 'pdf' && (
          <div>
            <div style={{ marginBottom: 10, color: '#94a3b8', fontSize: 12 }}>
              Upload a termsheet PDF to extract and save asset data.
            </div>
            <input
              type="file"
              accept=".pdf"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (!f) return
                uploadTermsheet(f)
                  .then(() => { onAssetSaved && onAssetSaved(); onClose() })
                  .catch(() => {})
              }}
            />
            {uploadError && <div style={{ color: '#dc2626', marginTop: 10 }}>{uploadError}</div>}
            {uploading && <div style={{ color: '#0891b2', marginTop: 10 }}>Uploading…</div>}
          </div>
        )}

        <button onClick={onClose} style={{ marginTop: 18, display: 'block' }}>
          Close
        </button>
      </div>
    </div>
  )
}
