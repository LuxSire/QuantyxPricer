import { useState, useCallback } from 'react'

const STORAGE_KEY = 'quantyx_user'

function loadStoredUser() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function useUser(apiBase = '') {
  const [currentUser, setCurrentUser] = useState(() => loadStoredUser())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const base = String(apiBase || '').replace(/\/$/, '')

  const login = useCallback(async (email, password) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${base}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.detail || 'Login failed')
      localStorage.setItem(STORAGE_KEY, JSON.stringify(json))
      setCurrentUser(json)
      return json
    } catch (err) {
      setError(err.message)
      return null
    } finally {
      setLoading(false)
    }
  }, [base])

  const register = useCallback(async (email, password, firstname, lastname) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${base}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, firstname, lastname }),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.detail || 'Registration failed')
      return json
    } catch (err) {
      setError(err.message)
      return null
    } finally {
      setLoading(false)
    }
  }, [base])

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setCurrentUser(null)
    setError(null)
    window.location.hash = '#/login'
  }, [])

  const fetchUsers = useCallback(async () => {
    try {
      const res = await fetch(`${base}/users`)
      if (!res.ok) return []
      return await res.json()
    } catch {
      return []
    }
  }, [base])

  return { currentUser, loading, error, login, register, logout, fetchUsers }
}
