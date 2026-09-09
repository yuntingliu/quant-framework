'use client'

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import { CHART_COLORS } from '@/lib/constants'
import { formatPercent, formatNumber } from '@/lib/utils'

interface SeriesConfig {
  key: string
  name: string
  color?: string
}

interface CumulativeReturnsChartProps {
  data: Record<string, number | string>[]
  series: SeriesConfig[]
  height?: number | `${number}%`
  showGrid?: boolean
  yAxisFormat?: 'percent' | 'decimal'
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

export function CumulativeReturnsChart({
  data,
  series,
  height = 400,
  showGrid = true,
  yAxisFormat = 'decimal',
}: CumulativeReturnsChartProps) {
  const yFormatter = (value: number) =>
    yAxisFormat === 'percent' ? formatPercent(value, 1) : formatNumber(value, 2)

  const tooltipFormatter = (value: number, name: string) => {
    const formatted =
      yAxisFormat === 'percent' ? formatPercent(value) : formatNumber(value, 4)
    return [formatted, name]
  }

  const dateFormatter = (value: unknown) => {
    const str = String(value ?? '')
    if (!str) return ''
    const d = new Date(str)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    return `${y}-${m}`
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
        {showGrid && (
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="hsl(var(--border))"
            opacity={0.5}
          />
        )}
        <XAxis
          dataKey="date"
          tickFormatter={dateFormatter}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          minTickGap={40}
        />
        <YAxis
          tickFormatter={yFormatter}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={65}
        />
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
          labelFormatter={dateFormatter as any}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter={tooltipFormatter as any}
        />
        <Legend
          verticalAlign="top"
          height={36}
          wrapperStyle={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}
        />
        {series.map((s, idx) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.name}
            stroke={s.color ?? CHART_COLORS[idx % CHART_COLORS.length]}
            strokeWidth={2}
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
