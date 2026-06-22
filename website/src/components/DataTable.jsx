import React from 'react'

export default function DataTable({
  columns,
  data,
  filters = {},
  onRowAction,
  actionLabel = 'Action',
  emptyMessage = 'No data available',
  renderRow,
}) {
  const filteredData = data.filter((item) => {
    for (const [key, value] of Object.entries(filters)) {
      if (!value) continue
      const itemValue = item[key] || (item.result && item.result[key]) || ''
      if (String(itemValue) !== value) return false
    }
    return true
  })

  return (
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
        {filteredData.length > 0 ? (
          filteredData.map((item, i) => (
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
            <td colSpan={columns.length + (onRowAction ? 1 : 0)}>{emptyMessage}</td>
          </tr>
        )}
      </tbody>
    </table>
  )
}