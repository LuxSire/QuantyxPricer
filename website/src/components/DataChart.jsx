import React from 'react'
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

export default function DataChart({ data = [], width = 380, height = 300, xLabel = 'Spread (bp)' }) {
  if (!data.length) {
    return (
      <div style={{ padding: 8, color: '#6b7280' }}>
        Sensitivity chart unavailable: price the asset first.
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis
          dataKey="x"
          type="number"
          name="Spread"
          domain={['auto', 'auto']}
          tickFormatter={v => `${v.toFixed(0)} bp`}
          label={{ value: xLabel, position: 'insideBottom', offset: -12, fill: '#94a3b8', fontSize: 11 }}
          tick={{ fill: '#94a3b8', fontSize: 11 }}
        />
        <YAxis
          dataKey="y"
          type="number"
          name="Price"
          domain={['auto', 'auto']}
          tickFormatter={v => `${v.toFixed(2)}%`}
          tick={{ fill: '#94a3b8', fontSize: 11 }}
          width={56}
        />
        <Tooltip
          cursor={{ strokeDasharray: '3 3' }}
          formatter={(value, name) =>
            name === 'Spread'
              ? [`${Number(value).toFixed(2)} bp`, 'Spread']
              : [`${Number(value).toFixed(4)}%`, 'Price']
          }
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', fontSize: 11 }}
          labelStyle={{ color: '#94a3b8' }}
        />
        <Scatter
          data={data}
          fill="#206095"
          line={{ stroke: '#206095', strokeWidth: 2 }}
          lineType="joint"
        />
      </ScatterChart>
    </ResponsiveContainer>
  )
}
