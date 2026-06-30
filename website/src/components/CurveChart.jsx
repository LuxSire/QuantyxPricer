import React from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

export default function CurveChart({ data = [], curveName = '', width = 380, height = 300 }) {
  if (!data.length) {
    return (
      <div style={{ padding: 8, color: '#6b7280', fontSize: 12 }}>
        IR curve unavailable.
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 24, left: 16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis
          dataKey="tenor"
          tick={{ fill: '#94a3b8', fontSize: 10 }}
          label={{ value: curveName, position: 'insideBottom', offset: -12, fill: '#94a3b8', fontSize: 11 }}
        />
        <YAxis
          dataKey="rate"
          tickFormatter={v => `${v.toFixed(2)}%`}
          tick={{ fill: '#94a3b8', fontSize: 11 }}
          width={56}
          domain={['auto', 'auto']}
        />
        <Tooltip
          formatter={(value) => [`${Number(value).toFixed(4)}%`, 'Rate']}
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', fontSize: 11 }}
          labelStyle={{ color: '#94a3b8' }}
        />
        <Line
          type="monotone"
          dataKey="rate"
          stroke="#34d399"
          strokeWidth={2}
          dot={{ fill: '#34d399', r: 3 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
