import { useState, useCallback } from 'react'

export function useAsset(apiBase = '') {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const uploadJson = useCallback(async (obj) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${apiBase}/assets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(obj)
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(txt || 'Upload failed')
      }
      const j = await res.json()
      setLoading(false)
      return j
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      throw e
    }
  }, [apiBase])

  const uploadTermsheet = useCallback(async (file) => {
    setLoading(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${apiBase}/termsheet_asset`, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(txt || 'Termsheet upload failed')
      }
      const j = await res.json()
      setLoading(false)
      return j
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      throw e
    }
  }, [apiBase])

  const fetchAsset = useCallback(async (instrumentId) => {
    setLoading(true)
    setError(null)
    try {
      const base = String(apiBase || '').replace(/\/$/, '')
      const endpoint = `${base}/fetch_asset?instrument_id=${encodeURIComponent(instrumentId)}`
      const r = await fetch(endpoint)
      if (!r.ok) {
        setLoading(false)
        return null
      }
      const j = await r.json()
      setLoading(false)
      return j
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      return null
    }
  }, [apiBase])

  return {
    loading,
    error,
    uploadJson,
    uploadTermsheet,
    fetchAsset,
  }
}
