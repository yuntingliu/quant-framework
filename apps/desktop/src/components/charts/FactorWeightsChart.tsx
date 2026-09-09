'use client'

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import { FACTOR_COLORS } from '@/lib/constants'
import { formatPercent } from '@/lib/utils'

interface FactorWeightsDataPoint {
  date: string
  MKT: number
  SMB: number
  HML: number
  [key: string]: number | string
}

interface FactorWeightsChartProps {
  data: FactorWeightsDataPoint[]
  height?: number
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

export function FactorWeightsChart({
  data,
  height = 380,
}: FactorWeightsChartProps) {
  const dateFormatter = (value: string) => {
    if (!value) return ''
    const d = new Date(value)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    return `${y}-${m}`
  }

  const tooltipFormatter = (value: number, name: string) => [
    formatPercent(value, 1),
    name,
  ]

  // Detect all factor keys dynamically (exclude 'date')
  const factorKeys =
    data.length > 0
      ? Object.keys(data[0] ?? {}).filter((k) => k !== 'date')
      : ['MKT', 'SMB', 'HML']

  const colorMap: Record<string, string> = {
    MKT: FACTOR_COLORS.MKT,
    SMB: FACTOR_COLORS.SMB,
    HML: FACTOR_COLORS.HML,
  }

  const fallbackColors = ['#AB47BC', '#26A69A', '#EC407A', '#7E57C2', '#5C6BC0']

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart
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
          minTickGap={40}
        />
        <YAxis
          tickFormatter={(v: number) => formatPercent(v, 0)}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={55}
          domain={[0, 1]}
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
        {factorKeys.map((key, idx) => {
          const color =
            colorMap[key] ?? fallbackColors[idx % fallbackColors.length]
          return (
            <Area
              key={key}
              type="monotone"
              dataKey={key}
              name={key}
              stackId="weights"
              stroke={color}
              fill={color}
              fillOpacity={0.7}
              strokeWidth={0.5}
              isAnimationActive={true}
              animationDuration={300}
            />
          )
        })}
      </AreaChart>
    </ResponsiveContainer>
  )
}
