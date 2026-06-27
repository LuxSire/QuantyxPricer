import React, { useState, useEffect } from 'react'

export default function DataTable({
  columns,
  data,
  filters = {},
  onRowAction,
  actionLabel = 'Action',
  emptyMessage = 'No data available',
  renderRow,
  pageSize,
}) {
  const [page, setPage] = useState(0)

  const filteredData = data.filter((item) => {
    for (const [key, value] of Object.entries(filters)) {
      if (!value) continue
      const itemValue = item[key] || (item.result && item.result[key]) || ''
      if (String(itemValue) !== value) return false
    }
    return true
  })

  const totalPages = pageSize ? Math.ceil(filteredData.length / pageSize) : 1
  const pagedData = pageSize ? filteredData.slice(page * pageSize, (page + 1) * pageSize) : filteredData

  // Reset to page 0 when filters change or data length changes
  useEffect(() => { setPage(0) }, [filteredData.length, JSON.stringify(filters)])

  const colSpan = columns.length + (onRowAction ? 1 : 0)

  return (
    <div>
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} className={col.className || ''} title={col.title || ''}>
                {col.label}
              </th>
            ))}
            {onRowAction && <th className="center">{actionLabel}</th>}
          </tr>
        </thead>
        <tbody>
          {pagedData.length > 0 ? (
            pagedData.map((item, i) => (
              <tr key={i}>
                {renderRow ? renderRow(item, i) : columns.map((col) => <td key={col.key}>{col.value ? col.value(item) : (item[col.key] || '')}</td>)}
                {onRowAction && (
                  <td style={{ textAlign: 'center' }}>
                    {onRowAction(item, i)}
                  </td>
                )}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={colSpan}>{emptyMessage}</td>
            </tr>
          )}
        </tbody>
      </table>

      {pageSize && totalPages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, fontSize: 13, color: '#94a3b8' }}>
          <button
            className="clear-btn"
            onClick={() => setPage(0)}
            disabled={page === 0}
            style={{ padding: '2px 8px' }}
          >
            «
          </button>
          <button
            className="clear-btn"
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            style={{ padding: '2px 8px' }}
          >
            ‹
          </button>
          <span>
            Page {page + 1} of {totalPages} &nbsp;({filteredData.length} total)
          </span>
          <button
            className="clear-btn"
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            style={{ padding: '2px 8px' }}
          >
            ›
          </button>
          <button
            className="clear-btn"
            onClick={() => setPage(totalPages - 1)}
            disabled={page >= totalPages - 1}
            style={{ padding: '2px 8px' }}
          >
            »
          </button>
        </div>
      )}
    </div>
  )
}
