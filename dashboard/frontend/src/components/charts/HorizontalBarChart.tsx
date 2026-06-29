'use client'

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  LabelList,
} from 'recharts'
import { formatPercent, formatNumber } from '@/lib/utils'

interface HorizontalBarDataPoint {
  name: string
  value: number
}

interface HorizontalBarChartProps {
  data: HorizontalBarDataPoint[]
  height?: number
  color?: string
  format?: 'percent' | 'number'
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

export function HorizontalBarChart({
  data,
  height,
  color = '#2962FF',
  format = 'percent',
}: HorizontalBarChartProps) {
  // Sort by value descending (largest at top)
  const sortedData = [...data].sort((a, b) => b.value - a.value)

  const computedHeight = height ?? Math.max(200, sortedData.length * 35 + 80)

  const valueFormatter = (value: number) =>
    format === 'percent' ? formatPercent(value) : formatNumber(value, 2)

  const tooltipFormatter = (value: number, name: string) => [
    valueFormatter(value),
    name,
  ]

  return (
    <ResponsiveContainer width="100%" height={computedHeight}>
      <BarChart
        data={sortedData}
        layout="vertical"
        margin={{ top: 5, right: 50, bottom: 5, left: 100 }}
      >
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="hsl(var(--border))"
          opacity={0.5}
          horizontal={false}
        />
        <YAxis
          dataKey="name"
          type="category"
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={95}
        />
        <XAxis
          type="number"
          tickFormatter={
            format === 'percent'
              ? (v: number) => formatPercent(v, 1)
              : (v: number) => formatNumber(v, 1)
          }
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
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
        <Bar
          dataKey="value"
          name="Weight"
          fill={color}
          isAnimationActive={true}
          animationDuration={300}
          radius={[0, 3, 3, 0]}
        >
          <LabelList
            dataKey="value"
            position="right"
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={valueFormatter as any}
            style={{
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 10,
              fill: 'hsl(var(--muted-foreground))',
            }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
