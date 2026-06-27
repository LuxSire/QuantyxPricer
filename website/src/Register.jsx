import React, { useState } from 'react'
import { useUser } from './hooks/useUser'

export default function Register({ apiBase = '' }) {
  const { register, loading, error } = useUser(apiBase)
  const [form, setForm] = useState({ firstname: '', lastname: '', email: '', password: '' })
  const [success, setSuccess] = useState(false)

  const set = (field) => (e) => setForm(prev => ({ ...prev, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    const result = await register(form.email, form.password, form.firstname, form.lastname)
    if (result) {
      setSuccess(true)
      setTimeout(() => { window.location.hash = '#/login' }, 1500)
    }
  }

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <h2 style={styles.title}>Create account</h2>
        {success ? (
          <div style={styles.successMsg}>Account created! Redirecting to sign in…</div>
        ) : (
          <form onSubmit={handleSubmit} style={styles.form}>
            <div style={styles.row}>
              <div style={styles.col}>
                <label style={styles.label}>First name</label>
                <input
                  type="text"
                  value={form.firstname}
                  onChange={set('firstname')}
                  required
                  autoFocus
                  style={styles.input}
                  placeholder="Jane"
                />
              </div>
              <div style={styles.col}>
                <label style={styles.label}>Last name</label>
                <input
                  type="text"
                  value={form.lastname}
                  onChange={set('lastname')}
                  required
                  style={styles.input}
                  placeholder="Doe"
                />
              </div>
            </div>
            <label style={styles.label}>Email</label>
            <input
              type="email"
              value={form.email}
              onChange={set('email')}
              required
              style={styles.input}
              placeholder="you@example.com"
            />
            <label style={styles.label}>Password</label>
            <input
              type="password"
              value={form.password}
              onChange={set('password')}
              required
              minLength={6}
              style={styles.input}
              placeholder="••••••••"
            />
            {error && <div style={styles.error}>{error}</div>}
            <button type="submit" disabled={loading} style={styles.button}>
              {loading ? 'Creating account…' : 'Register'}
            </button>
          </form>
        )}
        <p style={styles.footer}>
          Already have an account?{' '}
          <a href="#/login" style={styles.link}>Sign in</a>
        </p>
      </div>
    </div>
  )
}

const styles = {
  page: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#0f172a',
  },
  card: {
    width: 400,
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: 10,
    padding: '32px 28px',
  },
  title: {
    margin: '0 0 24px',
    color: '#f1f5f9',
    fontSize: 22,
    fontWeight: 700,
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  row: {
    display: 'flex',
    gap: 10,
  },
  col: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    gap: 4,
  },
  label: {
    color: '#94a3b8',
    fontSize: 12,
    fontWeight: 600,
    marginTop: 8,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  input: {
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: 6,
    color: '#f1f5f9',
    fontSize: 14,
    padding: '8px 10px',
    outline: 'none',
  },
  button: {
    marginTop: 20,
    padding: '10px',
    background: '#206095',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
  },
  error: {
    marginTop: 6,
    color: '#f87171',
    fontSize: 13,
  },
  successMsg: {
    padding: '12px',
    background: '#052e16',
    border: '1px solid #166534',
    borderRadius: 6,
    color: '#4ade80',
    fontSize: 14,
    textAlign: 'center',
  },
  footer: {
    marginTop: 20,
    textAlign: 'center',
    color: '#64748b',
    fontSize: 13,
  },
  link: {
    color: '#38bdf8',
    textDecoration: 'none',
  },
}
