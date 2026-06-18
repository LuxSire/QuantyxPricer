const PERCENTAGE_KEYS = new Set([
  'ytm',
  'ytc',
  'ytd',
  'yield_to_maturity',
  'yield_to_call',
  'model_ytm_to_maturity',
  'model_ytc_to_first_call',
  'ytm_expected',
  'ytm_promised',
  'fixed_coupon_rate',
  'coupon_rate',
])

export function isPercentageKey(key) {
  const lowerKey = String(key || '').toLowerCase()
  return PERCENTAGE_KEYS.has(lowerKey) || lowerKey.includes('rate')
}

export function formatNumberForDisplay(value, options = {}) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value)

  const scale = options.scale ?? 1
  const suffix = options.suffix ?? ''
  const n = value * scale

  let rendered
  if (Math.abs(n) > 1000) {
    // Do not force fixed decimals for large values.
    rendered = n.toLocaleString(undefined, { maximumFractionDigits: 3 })
  } else {
    rendered = n.toFixed(3)
  }

  return `${rendered}${suffix}`
}
