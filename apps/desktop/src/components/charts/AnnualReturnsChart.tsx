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
  Cell,
} from 'recharts'
import { CHART_COLORS } from '@/lib/constants'
import { formatPercent } from '@/lib/utils'

interface SeriesConfig {
  key: string
  name: string
  color?: string
}

interface AnnualReturnsChartProps {
  data: Record<string, number | string>[]
  series: SeriesConfig[]
  height?: number | `${number}%`
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

export function AnnualReturnsChart({
  data,
  series,
  height = 350,
}: AnnualReturnsChartProps) {
  const tooltipFormatter = (value: number, name: string) => [
    formatPercent(value ?? 0),
    name ?? '',
  ]

  // For single-series, apply conditional coloring (red negative, green positive)
  const isSingleSeries = series.length === 1

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
          dataKey="year"
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
        />
        <YAxis
          tickFormatter={(v: number) => formatPercent(v, 0)}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={55}
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
        {series.map((s, idx) => {
          const defaultColor = s.color ?? CHART_COLORS[idx % CHART_COLORS.length]

          if (isSingleSeries) {
            return (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.name}
                isAnimationActive={true}
                animationDuration={300}
              >
                {data.map((entry, entryIdx) => {
                  const val = entry[s.key]
                  const numVal = typeof val === 'number' ? val : 0
                  return (
                    <Cell
                      key={`cell-${entryIdx}`}
                      fill={numVal >= 0 ? '#00C853' : '#EF5350'}
                    />
                  )
                })}
              </Bar>
            )
          }

          return (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.name}
              fill={defaultColor}
              isAnimationActive={true}
              animationDuration={300}
            />
          )
        })}
      </BarChart>
    </ResponsiveContainer>
  )
}
