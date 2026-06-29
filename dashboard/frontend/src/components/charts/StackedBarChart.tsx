'use client'

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from 'recharts'
import { CHART_COLORS } from '@/lib/constants'
import { formatPercent } from '@/lib/utils'

interface SeriesConfig {
  key: string
  name: string
  color?: string
}

interface StackedBarChartProps {
  data: Record<string, number | string>[]
  series: SeriesConfig[]
  height?: number
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

export function StackedBarChart({
  data,
  series,
  height = 420,
}: StackedBarChartProps) {
  const dateFormatter = (value: string) => {
    if (!value) return ''
    const d = new Date(value)
    if (isNaN(d.getTime())) return value
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    return `${y}-${m}`
  }

  const tooltipFormatter = (value: number, name: string) => [
    formatPercent(value, 2),
    name,
  ]

  // Factor-specific colors
  const colorMap: Record<string, string> = {
    MKT: '#2962FF',
    SMB: '#00C853',
    HML: '#FF6D00',
    alpha: '#9C27B0',
    residual: '#BDBDBD',
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
      >
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
          minTickGap={30}
        />
        <YAxis
          tickFormatter={(v: number) => formatPercent(v, 1)}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={60}
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
        <ReferenceLine y={0} stroke="hsl(var(--muted-foreground))" strokeDasharray="3 3" />
        {series.map((s, idx) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.name}
            stackId="contributions"
            fill={
              s.color ??
              colorMap[s.key] ??
              CHART_COLORS[idx % CHART_COLORS.length]
            }
            isAnimationActive={true}
            animationDuration={300}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
