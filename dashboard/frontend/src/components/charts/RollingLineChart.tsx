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
  ReferenceLine,
} from 'recharts'
import { CHART_COLORS } from '@/lib/constants'
import { formatPercent, formatNumber } from '@/lib/utils'

interface SeriesConfig {
  key: string
  name: string
  color?: string
  dashed?: boolean
}

interface RollingLineChartProps {
  data: Record<string, number | string>[]
  series: SeriesConfig[]
  height?: number
  referenceLine?: number
  yAxisFormat?: 'percent' | 'decimal'
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

export function RollingLineChart({
  data,
  series,
  height = 380,
  referenceLine,
  yAxisFormat = 'decimal',
}: RollingLineChartProps) {
  const yFormatter = (value: number) =>
    yAxisFormat === 'percent' ? formatPercent(value, 1) : formatNumber(value, 3)

  const tooltipFormatter = (value: number, name: string) => {
    const formatted =
      yAxisFormat === 'percent' ? formatPercent(value) : formatNumber(value, 4)
    return [formatted, name]
  }

  const dateFormatter = (value: string) => {
    if (!value) return ''
    const d = new Date(value)
    if (isNaN(d.getTime())) return value
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    return `${y}-${m}`
  }

  // Factor-specific colors
  const colorMap: Record<string, string> = {
    MKT: '#2962FF',
    SMB: '#00C853',
    HML: '#FF6D00',
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="hsl(var(--border))"
          opacity={0.5}
        />
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
          wrapperStyle={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 12,
          }}
        />
        {referenceLine !== undefined && (
          <ReferenceLine
            y={referenceLine}
            stroke="hsl(var(--muted-foreground))"
            strokeDasharray="5 3"
            opacity={0.6}
          />
        )}
        {series.map((s, idx) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.name}
            stroke={
              s.color ??
              colorMap[s.key] ??
              CHART_COLORS[idx % CHART_COLORS.length]
            }
            strokeWidth={2}
            strokeDasharray={s.dashed ? '5 3' : undefined}
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
