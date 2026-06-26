import { useState, useEffect } from 'react'

export function usePrices(apiBase) {
  const [pricingAll, setPricingAll] = useState(false)
  const [updatingCurves, setUpdatingCurves] = useState(false)
  const [downloadingAll, setDownloadingAll] = useState(false)
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  const fetchPrices = async () => {
    const endpoint = (apiBase ? `${apiBase}` : '') + '/fetch_prices'
    try {
      const resp = await fetch(endpoint)
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '<no body>')
        const msg = `Could not fetch prices from API: ${txt}`
        setError(msg)
        setRows(null)
        return null
      }
      const data = await resp.json()
      setError(null)
      setRows(data)
      return data
    } catch (err) {
      const msg = `Could not fetch prices from API: ${String(err)}`
      setError(msg)
      setRows(null)
      return null
    }
  }

  useEffect(() => {
    let mounted = true
    if (!mounted) return

    const load = async () => {
      await fetchPrices()
    }

    load()
    return () => { mounted = false }
  }, [apiBase])

  const refreshPrices = async () => {
    await fetchPrices()
  }

  const onPriceAll = async (setSnack) => {
    if (pricingAll) return
    setPricingAll(true)
    setSnack({ visible: true, message: 'Starting price all...', type: 'info' })
    try {
      const resp = await fetch((apiBase ? `${apiBase}` : '') + '/price_all', { method: 'POST' })
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '<no body>')
        setSnack({ visible: true, message: `Price all failed: ${txt}`, type: 'error' })
        setPricingAll(false)
        return
      }
      const jobObj = await resp.json().catch(() => null)
      const jobId = jobObj && jobObj.job_id
      if (!jobId) {
        setSnack({ visible: true, message: 'Price all completed', type: 'success' })
        setPricingAll(false)
        await refreshPrices()
        return
      }

      const statusUrl = (apiBase ? `${apiBase}` : '') + `/jobs/${jobId}`
      let done = false
      while (!done) {
        await new Promise(r => setTimeout(r, 2000))
        try {
          const sresp = await fetch(statusUrl)
          if (!sresp.ok) continue
          const s = await sresp.json()
          if (s.status === 'pending' || s.status === 'running') continue
          done = true
          if (s.status === 'succeeded') {
            setSnack({ visible: true, message: 'Price all succeeded', type: 'success' })
            await refreshPrices()
          } else {
            setSnack({ visible: true, message: `Price all failed: ${s.error || 'unknown'}`, type: 'error' })
          }
        } catch (e) {
          console.error('Polling job status error', e)
        }
      }
    } catch (err) {
      setSnack({ visible: true, message: `Error calling price_all: ${String(err)}`, type: 'error' })
    }
    setPricingAll(false)
  }

  const onUpdateCurves = async (setSnack) => {
    if (updatingCurves) return
    setUpdatingCurves(true)
    setSnack({ visible: true, message: 'Starting curve update...', type: 'info' })
    try {
      const resp = await fetch((apiBase ? `${apiBase}` : '') + '/update_curve', { method: 'POST' })
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '<no body>')
        setSnack({ visible: true, message: `Update curves failed: ${txt}`, type: 'error' })
        setUpdatingCurves(false)
        return
      }
      const jobObj = await resp.json().catch(() => null)
      const jobId = jobObj && jobObj.job_id
      if (!jobId) {
        setSnack({ visible: true, message: 'Update curves completed', type: 'success' })
        setUpdatingCurves(false)
        return
      }

      const statusUrl = (apiBase ? `${apiBase}` : '') + `/jobs/${jobId}`
      let done = false
      while (!done) {
        await new Promise(r => setTimeout(r, 2000))
        try {
          const sresp = await fetch(statusUrl)
          if (!sresp.ok) continue
          const s = await sresp.json()
          if (s.status === 'pending' || s.status === 'running') continue
          done = true
          if (s.status === 'succeeded') {
            setSnack({ visible: true, message: 'Update curves succeeded', type: 'success' })
          } else {
            setSnack({ visible: true, message: `Update curves failed: ${s.error || 'unknown'}`, type: 'error' })
          }
        } catch (e) {
          console.error('Polling update_curve job status error', e)
        }
      }
    } catch (err) {
      setSnack({ visible: true, message: `Error calling update_curve: ${String(err)}`, type: 'error' })
    }
    setUpdatingCurves(false)
  }

  const downloadPrice = async (instrumentId, setSnack) => {
    if (!instrumentId) {
      if (setSnack) setSnack({ visible: true, message: 'instrumentId required', type: 'error' })
      return null
    }
    const endpoint = (apiBase ? `${apiBase}` : '') + `/download_prices?instrument_id=${encodeURIComponent(instrumentId)}`
    try {
      const resp = await fetch(endpoint)
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '<no body>')
        if (setSnack) setSnack({ visible: true, message: `Download failed: ${txt}`, type: 'error' })
        return null
      }
      const data = await resp.json().catch(() => null)
      if (setSnack) setSnack({ visible: true, message: `Downloaded ${instrumentId}`, type: 'success' })
      // refresh local prices cache
      await refreshPrices()
      return data
    } catch (err) {
      if (setSnack) setSnack({ visible: true, message: `Error downloading price: ${String(err)}`, type: 'error' })
      console.error('downloadPrice error', err)
      return null
    }
  }

  const downloadAllPrices = async (setSnack) => {
    const endpoint = (apiBase ? `${apiBase}` : '') + '/download_all_prices'
    try {
      if (setSnack) setSnack({ visible: true, message: 'Starting download of all prices...', type: 'info' })
      const resp = await fetch(endpoint)
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '<no body>')
        if (setSnack) setSnack({ visible: true, message: `Download all failed: ${txt}`, type: 'error' })
        return null
      }
      const data = await resp.json().catch(() => null)
      if (setSnack) setSnack({ visible: true, message: 'Downloaded all prices', type: 'success' })
      await refreshPrices()
      return data
    } catch (err) {
      if (setSnack) setSnack({ visible: true, message: `Error downloading all prices: ${String(err)}`, type: 'error' })
      console.error('downloadAllPrices error', err)
      return null
    }
  }

  const downloadAndInsertPrices = async (instrumentId, setSnack) => {
    const downloaded = await downloadPrice(instrumentId, setSnack)
    if (!downloaded) return null
    const payload = downloaded.provider_result || downloaded
    return await insertPrices(payload, setSnack)
  }

  const downloadAndInsertAllPrices = async (assets, setSnack) => {
    if (downloadingAll || !assets || assets.length === 0) return
    setDownloadingAll(true)
    if (setSnack) setSnack({ visible: true, message: `Downloading prices for ${assets.length} underlying(s)...`, type: 'info' })
    let ok = 0
    let fail = 0
    for (const asset of assets) {
      const result = await downloadAndInsertPrices(asset.instrument_id, null)
      if (result) ok++; else fail++
    }
    setDownloadingAll(false)
    if (setSnack) setSnack({
      visible: true,
      message: `Done: ${ok} inserted, ${fail} failed`,
      type: fail === 0 ? 'success' : 'error',
    })
  }

  const insertPrices = async (payload, setSnack) => {
    const endpoint = (apiBase ? `${apiBase}` : '') + '/insert_prices'
    try {
      const resp = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '<no body>')
        if (setSnack) setSnack({ visible: true, message: `Insert prices failed: ${txt}`, type: 'error' })
        return null
      }
      const data = await resp.json().catch(() => null)
      if (setSnack) setSnack({ visible: true, message: `Inserted prices id=${data && data.inserted_id}`, type: 'success' })
      await refreshPrices()
      return data
    } catch (err) {
      if (setSnack) setSnack({ visible: true, message: `Error inserting prices: ${String(err)}`, type: 'error' })
      console.error('insertPrices error', err)
      return null
    }
  }

  const pricing_single_asset = async (instrumentId, setSnack) => {
    if (!instrumentId) {
      if (setSnack) setSnack({ visible: true, message: 'instrumentId required', type: 'error' })
      return null
    }

    // Download market data first (best-effort, do not block pricing)
    try {
      if (setSnack) setSnack({ visible: true, message: `Downloading market data for ${instrumentId}...`, type: 'info' })
      const downloaded = await downloadPrice(instrumentId, setSnack)
      if (downloaded) {
        const payload = downloaded.provider_result || downloaded
        await insertPrices(payload, setSnack)
      } else {
        if (setSnack) setSnack({ visible: true, message: `No market data for ${instrumentId}, proceeding to price...`, type: 'info' })
      }
    } catch (err) {
      if (setSnack) setSnack({ visible: true, message: `Market data download failed, proceeding to price: ${String(err)}`, type: 'info' })
      console.error('pricing_single_asset market data error', err)
      // Do not return - continue to pricing step below
    }

    // Always call /price regardless of market data result
    const payload = { instrument_id: instrumentId }
    console.debug('[UI] pricing request', payload)
    try {
      const resp = await fetch((apiBase ? `${apiBase}` : '') + '/price', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      console.debug('[UI] pricing response status', resp.status)
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '<no body>')
        if (setSnack) setSnack({ visible: true, message: `Pricing failed: ${txt}`, type: 'error' })
        return null
      }
      const data = await resp.json().catch(() => null)
      if (setSnack) setSnack({ visible: true, message: `Pricing completed for ${instrumentId}`, type: 'success' })
      await refreshPrices()
      return data
    } catch (err) {
      if (setSnack) setSnack({ visible: true, message: `Error pricing single asset: ${String(err)}`, type: 'error' })
      console.error('pricing_single_asset error', err)
      return null
    }
  }

  const fetchAssetTimeSeries = async (instrumentId) => {
    if (!instrumentId) return null
    const endpoint = (apiBase ? `${apiBase}` : '') + `/fetch_asset_timeseries?instrument_id=${encodeURIComponent(instrumentId)}`
    try {
      const resp = await fetch(endpoint)
      if (!resp.ok) return null
      return await resp.json()
    } catch (err) {
      console.error('fetchAssetTimeSeries error', err)
      return null
    }
  }

  return {
    rows,
    error,
    refreshPrices,
    pricingAll,
    updatingCurves,
    onPriceAll,
    onUpdateCurves,
    downloadPrice,
    downloadAllPrices,
    downloadAndInsertPrices,
    downloadAndInsertAllPrices,
    downloadingAll,
    insertPrices,
    pricing_single_asset,
    fetchAssetTimeSeries,
  }
}
