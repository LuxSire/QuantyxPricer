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

  const fetchNopricedAssets = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const base = String(apiBase || '').replace(/\/$/, '')
      const endpoint = `${base}/fetch_noprice_assets`
      const r = await fetch(endpoint)
      if (!r.ok) {
        setLoading(false)
        return []
      }
      const j = await r.json()
      setLoading(false)
      return Array.isArray(j.missing_instrument_ids) ? j.missing_instrument_ids : []
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      return []
    }
  }, [apiBase])

  const fetchUnderlyingAssets = useCallback(async () => {
    console.log('[useAsset] fetchUnderlyingAssets called')
    setLoading(true)
    setError(null)
    try {
      const base = String(apiBase || '').replace(/\/$/, '')
      const endpoint = `${base}/fetch_underlying_assets`
      console.log('[useAsset] fetching from:', endpoint)
      const r = await fetch(endpoint)
      console.log('[useAsset] response status:', r.status)
      if (!r.ok) {
        setLoading(false)
        return []
      }
      const j = await r.json()
      console.log('[useAsset] fetchUnderlyingAssets response type:', typeof j, 'isArray:', Array.isArray(j))
      console.log('[useAsset] fetchUnderlyingAssets response:', JSON.stringify(j))
      setLoading(false)
      // Response is already a JSON array
      if (Array.isArray(j)) {
        console.log('[useAsset] returning array with', j.length, 'items')
        return j
      }
      console.log('[useAsset] response is not an array, type:', typeof j)
      return []
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      console.log('[useAsset] fetchUnderlyingAssets error:', errMsg)
      return []
    }
  }, [apiBase])

  const fetchModels = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const base = String(apiBase || '').replace(/\/$/, '')
      const r = await fetch(`${base}/fetch_models`)
      if (!r.ok) {
        setLoading(false)
        return []
      }
      const j = await r.json()
      setLoading(false)
      return Array.isArray(j) ? j : []
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      return []
    }
  }, [apiBase])

  const fetchAssetFields = useCallback(async (instrumentId) => {
    setLoading(true)
    setError(null)
    try {
      const [assetData, allModels] = await Promise.all([
        fetchAsset(instrumentId),
        fetchModels(),
      ])

      if (!assetData) {
        setLoading(false)
        return null
      }

      const modelName = String(assetData.model || '').trim().toLowerCase()
      const modelSpec = allModels.find(m => (m.name || '').toLowerCase() === modelName)
        || { required_fields: [], optional_fields: [] }

      const requiredNames = new Set((modelSpec.required_fields || []).map(f => f.name))
      const optionalNames = new Set((modelSpec.optional_fields || []).map(f => f.name))

      const fields = []

      // 1. Required fields in schema order, with values from the asset
      for (const fd of (modelSpec.required_fields || [])) {
        fields.push({
          name: fd.name,
          value: assetData[fd.name] ?? null,
          type: fd.type || 'string',
          description: fd.description || '',
          enum: fd.enum || null,
          required: true,
          default: null,
          inAsset: fd.name in assetData,
        })
      }

      // 2. Optional fields that are present in the asset, in schema order
      for (const fd of (modelSpec.optional_fields || [])) {
        if (fd.name in assetData) {
          fields.push({
            name: fd.name,
            value: assetData[fd.name],
            type: fd.type || 'string',
            description: fd.description || '',
            enum: fd.enum || null,
            required: false,
            default: fd.default ?? null,
            inAsset: true,
          })
        }
      }

      // 3. Extra fields from the asset not covered by the schema
      for (const [name, value] of Object.entries(assetData)) {
        if (!requiredNames.has(name) && !optionalNames.has(name)) {
          fields.push({ name, value, type: null, description: null, enum: null, required: null, default: null, inAsset: true })
        }
      }

      setLoading(false)
      return { asset: assetData, model: modelSpec, fields, allModels }
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      return null
    }
  }, [fetchAsset, fetchModels])

  const updateModel = useCallback(async (modelName, requiredFields, optionalFields) => {
    setLoading(true)
    setError(null)
    try {
      const base = String(apiBase || '').replace(/\/$/, '')
      const r = await fetch(`${base}/update_model`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: modelName,
          required_fields: requiredFields,
          optional_fields: optionalFields,
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
      const j = await r.json()
      setLoading(false)
      return j
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      throw e
    }
  }, [apiBase])

  const updateAsset = useCallback(async (updatedData, bondFile, isin) => {
    setLoading(true)
    setError(null)
    try {
      const fileNameRaw = bondFile || `${isin}.json`
      const fileName = String(fileNameRaw).split('/').pop() || `${isin}.json`
      const jsonBlob = new Blob([JSON.stringify(updatedData, null, 2)], { type: 'application/json' })
      const form = new FormData()
      form.append('file', jsonBlob, fileName)

      const base = String(apiBase || '').replace(/\/$/, '')
      const endpoint = `${base}/update_asset`
      const resp = await fetch(endpoint, { method: 'POST', body: form })
      if (!resp.ok) {
        const msg = await resp.text().catch(() => 'Unknown error')
        throw new Error(`Save failed: ${msg}`)
      }
      setLoading(false)
      return { success: true }
    } catch (e) {
      const errMsg = String(e)
      setError(errMsg)
      setLoading(false)
      throw e
    }
  }, [apiBase])

  return {
    loading,
    error,
    uploadJson,
    uploadTermsheet,
    fetchAsset,
    fetchNopricedAssets,
    fetchUnderlyingAssets,
    fetchModels,
    fetchAssetFields,
    updateModel,
    updateAsset,
  }
}
