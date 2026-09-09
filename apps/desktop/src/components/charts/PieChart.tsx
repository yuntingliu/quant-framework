'use client'

import {
  ResponsiveContainer,
  PieChart as RechartsPieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
} from 'recharts'
import { CHART_COLORS } from '@/lib/constants'

interface PieDataPoint {
  name: string
  value: number
  color?: string
}

interface PieChartProps {
  data: PieDataPoint[]
  height?: number
  innerRadius?: number
  showLabels?: boolean
  title?: string
}

interface CustomLabelProps {
  cx?: number
  cy?: number
  midAngle?: number
  innerRadius?: number
  outerRadius?: number
  percent?: number
  name?: string
  index?: number
}

const RADIAN = Math.PI / 180

function renderCustomLabel({
  cx = 0,
  cy = 0,
  midAngle = 0,
  outerRadius = 0,
  percent = 0,
  name = '',
}: CustomLabelProps) {
  const radius = outerRadius + 25
  const x = cx + radius * Math.cos(-midAngle * RADIAN)
  const y = cy + radius * Math.sin(-midAngle * RADIAN)

  if (percent < 0.03) return null

  return (
    <text
      x={x}
      y={y}
      fill="hsl(var(--foreground))"
      textAnchor={x > cx ? 'start' : 'end'}
      dominantBaseline="central"
      fontFamily="JetBrains Mono, monospace"
      fontSize={11}
    >
      {`${name}: ${(percent * 100).toFixed(1)}%`}
    </text>
  )
}

export function PieChart({
  data,
  height = 350,
  innerRadius = 60,
  showLabels = true,
  title,
}: PieChartProps) {
  // Normalize values to percentages for display
  const total = data.reduce((sum, d) => sum + Math.abs(d.value), 0)

  const tooltipFormatter = (value: number, name: string) => {
    const pct = total > 0 ? (value / total) * 100 : 0
    return [`${pct.toFixed(1)}%`, name]
  }

  return (
    <div style={{ width: '100%', position: 'relative' }}>
      {title && (
        <div
          style={{
            textAlign: 'center',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 13,
            fontWeight: 600,
            color: 'hsl(var(--foreground))',
            marginBottom: 4,
          }}
        >
          {title}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <RechartsPieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={innerRadius}
            outerRadius={innerRadius + 50}
            paddingAngle={2}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            label={showLabels ? (renderCustomLabel as any) : false}
            isAnimationActive={true}
            animationDuration={300}
          >
            {data.map((entry, idx) => (
              <Cell
                key={`cell-${idx}`}
                fill={entry.color ?? CHART_COLORS[idx % CHART_COLORS.length]}
                stroke="hsl(var(--background))"
                strokeWidth={2}
              />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: 'hsl(var(--card))',
              border: '1px solid hsl(var(--border))',
              borderRadius: '8px',
              color: 'hsl(var(--foreground))',
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 12,
            }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={tooltipFormatter as any}
          />
          <Legend
            verticalAlign="bottom"
            height={36}
            wrapperStyle={{
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 12,
            }}
          />
        </RechartsPieChart>
      </ResponsiveContainer>
    </div>
  )
}
