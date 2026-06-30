import React, { useState, useEffect, useCallback } from 'react'
import { useAsset } from './hooks/useAsset'
import { useUser } from './hooks/useUser'

const FIELD_TYPES = ['string', 'float', 'int', 'list', 'object', 'bool']

function Switch({ checked, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      style={{
        width: 42, height: 24, borderRadius: 12,
        background: checked ? '#facc15' : '#374151',
        border: '2px solid ' + (checked ? '#ca8a04' : '#4b5563'),
        cursor: 'pointer', position: 'relative',
        transition: 'background 0.18s, border-color 0.18s',
        flexShrink: 0, outline: 'none', padding: 0,
      }}
    >
      <span style={{
        position: 'absolute',
        top: 2, left: checked ? 18 : 2,
        width: 16, height: 16, borderRadius: 8,
        background: checked ? '#1a1a1a' : '#9ca3af',
        transition: 'left 0.18s',
        display: 'block',
      }} />
    </button>
  )
}

function FieldRow({ field, idx, onToggle, onRemove }) {
  return (
    <tr style={{ borderBottom: '1px solid #1e293b' }}>
      <td style={{ ...td, color: field.required ? '#facc15' : '#e2e8f0', fontWeight: field.required ? 600 : 400 }}>
        {field.name}
      </td>
      <td style={td}>
        <span style={{
          fontSize: 11, background: '#0f172a', border: '1px solid #334155',
          borderRadius: 4, padding: '2px 7px', color: '#94a3b8',
        }}>
          {field.type || '—'}
        </span>
      </td>
      <td style={{ ...td, color: '#94a3b8', fontSize: 13 }}>{field.description || ''}</td>
      <td style={{ ...td, textAlign: 'center' }}>
        <Switch checked={field.required} onChange={() => onToggle(idx)} />
      </td>
      <td style={td}>
        <button
          onClick={() => onRemove(idx)}
          title="Remove field"
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: '#4b5563', fontSize: 16, padding: '2px 6px', lineHeight: 1,
          }}
        >
          ✕
        </button>
      </td>
    </tr>
  )
}

export default function Settings({ apiBase }) {
  const [activeTab, setActiveTab] = useState('models')
  const { fetchModels, updateModel } = useAsset(apiBase)
  const { fetchUsers } = useUser(apiBase)

  // Models tab state
  const [models, setModels] = useState([])
  const [selectedName, setSelectedName] = useState('')
  const [fields, setFields] = useState([])
  const [saving, setSaving] = useState(false)
  const [snack, setSnack] = useState(null)
  const [addOpen, setAddOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newType, setNewType] = useState('string')
  const [newDesc, setNewDesc] = useState('')
  const [newRequired, setNewRequired] = useState(true)

  // Users tab state
  const [userList, setUserList] = useState(null)

  useEffect(() => {
    fetchModels().then(data => setModels(Array.isArray(data) ? data : []))
  }, [fetchModels])

  useEffect(() => {
    if (activeTab === 'users' && userList === null) {
      fetchUsers().then(data => setUserList(Array.isArray(data) ? data : []))
    }
  }, [activeTab, fetchUsers])

  const selectModel = useCallback((name) => {
    setSelectedName(name)
    setAddOpen(false)
    const m = models.find(m => m.name === name)
    if (!m) { setFields([]); return }
    const rf = (m.required_fields || []).map(f => ({ ...f, required: true }))
    const of_ = (m.optional_fields || []).map(f => ({ ...f, required: false }))
    setFields([...rf, ...of_])
  }, [models])

  const toggleRequired = (idx) =>
    setFields(prev => prev.map((f, i) => i === idx ? { ...f, required: !f.required } : f))

  const removeField = (idx) =>
    setFields(prev => prev.filter((_, i) => i !== idx))

  const addField = () => {
    if (!newName.trim()) return
    setFields(prev => [...prev, {
      name: newName.trim(), type: newType,
      description: newDesc, required: newRequired,
    }])
    setNewName(''); setNewType('string'); setNewDesc(''); setNewRequired(true)
    setAddOpen(false)
  }

  const handleUpdate = async () => {
    if (!selectedName) return
    const omitFlag = (f) => {
      const copy = { ...f }
      delete copy.required
      return copy
    }
    const required = fields.filter(f => f.required).map(omitFlag)
    const optional = fields.filter(f => !f.required).map(omitFlag)
    setSaving(true)
    try {
      await updateModel(selectedName, required, optional)
      setSnack({ type: 'success', msg: `Model "${selectedName}" updated.` })
    } catch (e) {
      setSnack({ type: 'error', msg: String(e) })
    } finally {
      setSaving(false)
      setTimeout(() => setSnack(null), 4000)
    }
  }

  const requiredRows = fields.map((f, idx) => ({ f, idx })).filter(({ f }) => f.required)
  const optionalRows = fields.map((f, idx) => ({ f, idx })).filter(({ f }) => !f.required)

  return (
    <div style={{ minHeight: '100vh', background: '#111', color: '#e2e8f0', fontFamily: 'inherit' }}>
      <div style={{ maxWidth: 900, margin: '0 auto', padding: '28px 24px' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <a href="#" style={{ color: '#94a3b8', textDecoration: 'none', fontSize: 14 }}>← Back</a>
            <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Settings</h2>
          </div>
          {activeTab === 'models' && (
            <button
              onClick={handleUpdate}
              disabled={saving || !selectedName}
              style={{
                padding: '8px 22px', borderRadius: 6, border: 'none',
                cursor: !selectedName ? 'default' : 'pointer',
                background: !selectedName ? '#1e293b' : '#facc15',
                color: !selectedName ? '#4b5563' : '#1a1a1a',
                fontWeight: 600, fontSize: 14,
                opacity: saving ? 0.7 : 1,
                transition: 'background 0.15s',
              }}
            >
              {saving ? 'Updating…' : 'Update'}
            </button>
          )}
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', borderBottom: '1px solid #334155', marginBottom: 28 }}>
          {[['models', 'Models'], ['users', 'Users'], ['docs', 'Docs']].map(([tab, label]) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: '8px 20px', border: 'none', cursor: 'pointer',
                background: 'transparent', fontSize: 14, fontWeight: 600,
                color: activeTab === tab ? '#facc15' : '#64748b',
                borderBottom: activeTab === tab ? '2px solid #facc15' : '2px solid transparent',
                transition: 'color 0.15s',
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Users tab */}
        {activeTab === 'users' && (
          <div>
            {userList === null ? (
              <div style={{ color: '#64748b', fontSize: 14 }}>Loading users…</div>
            ) : userList.length === 0 ? (
              <div style={{ color: '#64748b', fontSize: 14 }}>No users found.</div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #1e293b' }}>
                    <th style={th}>Email</th>
                    <th style={th}>First name</th>
                    <th style={th}>Last name</th>
                  </tr>
                </thead>
                <tbody>
                  {userList.map(u => (
                    <tr key={u.email} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={td}>{u.email}</td>
                      <td style={td}>{u.firstname || '—'}</td>
                      <td style={td}>{u.lastname || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* Docs tab */}
        {activeTab === 'docs' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {[
              { name: 'QP Overview', file: 'QP_overview.pdf' },
              { name: 'QP Technicals', file: 'QP_Technicals.pdf' },
            ].map(({ name, file }) => (
              <a
                key={file}
                href={`${import.meta.env.BASE_URL}docs/${file}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ textDecoration: 'none' }}
              >
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 14,
                  padding: '14px 18px', borderRadius: 8,
                  background: '#0d1a27', border: '1px solid #1a2d44',
                  transition: 'border-color 0.15s',
                }}>
                  <span style={{ fontSize: 22, lineHeight: 1 }}>📄</span>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: '#e6eef6' }}>{name}</div>
                    <div style={{ fontSize: 11, color: '#4a5568', marginTop: 2 }}>{file}</div>
                  </div>
                  <span style={{ marginLeft: 'auto', fontSize: 12, color: '#4a5568' }}>Open ↗</span>
                </div>
              </a>
            ))}
          </div>
        )}

        {/* Models tab */}
        {activeTab === 'models' && (
          <div>
            {/* Model selector */}
            <div style={{ marginBottom: 32 }}>
              <label style={{ display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6 }}>Model</label>
              <select
                value={selectedName}
                onChange={e => selectModel(e.target.value)}
                style={{
                  width: '100%', maxWidth: 380, padding: '8px 12px',
                  background: '#1e293b', border: '1px solid #334155',
                  borderRadius: 6, color: '#e2e8f0', fontSize: 14,
                }}
              >
                <option value="">— select a model —</option>
                {models.map(m => <option key={m.name} value={m.name}>{m.name}</option>)}
              </select>
            </div>

            {selectedName && (
              <>
                <Section title="Required Fields" count={requiredRows.length} accent="#facc15">
                  <FieldTable rows={requiredRows} onToggle={toggleRequired} onRemove={removeField} />
                </Section>

                <Section title="Optional Fields" count={optionalRows.length} accent="#94a3b8">
                  <FieldTable rows={optionalRows} onToggle={toggleRequired} onRemove={removeField} />
                </Section>

                {addOpen ? (
                  <div style={{
                    background: '#1e293b', border: '1px solid #334155',
                    borderRadius: 8, padding: '16px 20px', marginTop: 16,
                  }}>
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                      <div>
                        <label style={lbl}>Name</label>
                        <input
                          value={newName}
                          onChange={e => setNewName(e.target.value)}
                          placeholder="field_name"
                          onKeyDown={e => e.key === 'Enter' && addField()}
                          style={inp}
                          autoFocus
                        />
                      </div>
                      <div>
                        <label style={lbl}>Type</label>
                        <select value={newType} onChange={e => setNewType(e.target.value)} style={inp}>
                          {FIELD_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </div>
                      <div style={{ flex: 1, minWidth: 180 }}>
                        <label style={lbl}>Description</label>
                        <input
                          value={newDesc}
                          onChange={e => setNewDesc(e.target.value)}
                          placeholder="optional description"
                          style={{ ...inp, width: '100%', boxSizing: 'border-box' }}
                        />
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingBottom: 1 }}>
                        <Switch checked={newRequired} onChange={() => setNewRequired(p => !p)} />
                        <span style={{ fontSize: 13, color: newRequired ? '#facc15' : '#94a3b8' }}>
                          {newRequired ? 'Required' : 'Optional'}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 8, paddingBottom: 1 }}>
                        <button onClick={addField} style={btnPrimary}>Add</button>
                        <button onClick={() => setAddOpen(false)} style={btnSecondary}>Cancel</button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <button onClick={() => setAddOpen(true)} style={{ ...btnSecondary, marginTop: 16 }}>
                    + Add Field
                  </button>
                )}
              </>
            )}
          </div>
        )}

      </div>

      {/* Snackbar */}
      {snack && (
        <div style={{
          position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
          background: snack.type === 'success' ? '#14532d' : '#7f1d1d',
          color: '#fff', padding: '10px 22px', borderRadius: 8,
          fontSize: 14, boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
          border: '1px solid ' + (snack.type === 'success' ? '#166534' : '#991b1b'),
        }}>
          {snack.msg}
        </div>
      )}
    </div>
  )
}

function Section({ title, count, accent, children }) {
  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10,
        paddingBottom: 8, borderBottom: '1px solid #334155',
      }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: accent, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {title}
        </span>
        <span style={{
          fontSize: 11, background: '#1e293b', border: '1px solid #334155',
          borderRadius: 10, padding: '1px 8px', color: '#64748b',
        }}>
          {count}
        </span>
      </div>
      {children}
    </div>
  )
}

function FieldTable({ rows, onToggle, onRemove }) {
  if (rows.length === 0) {
    return <div style={{ fontSize: 13, color: '#4b5563', padding: '8px 0' }}>None</div>
  }
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid #1e293b' }}>
          <th style={th}>Field</th>
          <th style={th}>Type</th>
          <th style={th}>Description</th>
          <th style={{ ...th, textAlign: 'center', width: 90 }}>Required</th>
          <th style={{ ...th, width: 40 }}></th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ f, idx }) => (
          <FieldRow key={idx} field={f} idx={idx} onToggle={onToggle} onRemove={onRemove} />
        ))}
      </tbody>
    </table>
  )
}

const th = {
  padding: '6px 12px', textAlign: 'left', fontSize: 11,
  color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em',
}
const td = { padding: '10px 12px', fontSize: 14, verticalAlign: 'middle' }
const lbl = { display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 5 }
const inp = {
  padding: '6px 10px', background: '#0f172a', border: '1px solid #334155',
  borderRadius: 5, color: '#e2e8f0', fontSize: 13, outline: 'none',
}
const btnPrimary = {
  padding: '7px 16px', borderRadius: 5, border: 'none', cursor: 'pointer',
  background: '#facc15', color: '#1a1a1a', fontWeight: 600, fontSize: 13,
}
const btnSecondary = {
  padding: '7px 16px', borderRadius: 5, border: '1px solid #334155', cursor: 'pointer',
  background: 'transparent', color: '#94a3b8', fontSize: 13,
}
