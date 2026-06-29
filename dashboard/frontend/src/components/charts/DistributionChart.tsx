'use client'

import { useMemo } from 'react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import { formatNumber } from '@/lib/utils'

interface DistributionChartProps {
  data: number[]
  bins?: number
  color?: string
  meanLine?: number
  title?: string
  height?: number
}

interface BinData {
  binLabel: string
  binStart: number
  binEnd: number
  count: number
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

function computeHistogram(values: number[], nBins: number): BinData[] {
  if (values.length === 0) return []

  const sorted = [...values].sort((a, b) => a - b)
  const min = sorted[0] ?? 0
  const max = sorted[sorted.length - 1] ?? 0

  if (min === max) {
    return [
      {
        binLabel: formatNumber(min, 4),
        binStart: min,
        binEnd: max,
        count: values.length,
      },
    ]
  }

  const binWidth = (max - min) / nBins
  const bins: BinData[] = []

  for (let i = 0; i < nBins; i++) {
    const binStart = min + i * binWidth
    const binEnd = min + (i + 1) * binWidth
    const midpoint = (binStart + binEnd) / 2
    bins.push({
      binLabel: formatNumber(midpoint, 4),
      binStart,
      binEnd,
      count: 0,
    })
  }

  for (const v of values) {
    let idx = Math.floor((v - min) / binWidth)
    if (idx >= nBins) idx = nBins - 1
    if (idx < 0) idx = 0
    const bin = bins[idx]
    if (bin) bin.count++
  }

  return bins
}

export function DistributionChart({
  data,
  bins = 30,
  color = '#2962FF',
  meanLine,
  title,
  height = 300,
}: DistributionChartProps) {
  const histogramData = useMemo(() => computeHistogram(data, bins), [data, bins])

  const computedMean = meanLine ?? (data.length > 0 ? data.reduce((a, b) => a + b, 0) / data.length : undefined)

  const tooltipFormatter = (value: number, name: string) => [
    `${value}`,
    name,
  ]

  return (
    <div style={{ width: '100%' }}>
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
        <BarChart
          data={histogramData}
          margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="hsl(var(--border))"
            opacity={0.5}
          />
          <XAxis
            dataKey="binLabel"
            tick={TICK_STYLE}
            stroke="hsl(var(--border))"
            interval="preserveStartEnd"
            minTickGap={40}
          />
          <YAxis
            tick={TICK_STYLE}
            stroke="hsl(var(--border))"
            width={45}
            label={{
              value: 'Frequency',
              angle: -90,
              position: 'insideLeft',
              fill: 'hsl(var(--muted-foreground))',
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 11,
            }}
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
            labelFormatter={(label) => `Value: ${label}`}
          />
          {computedMean !== undefined && (
            <ReferenceLine
              x={formatNumber(computedMean, 4)}
              stroke="#FF6D00"
              strokeWidth={2}
              strokeDasharray="5 3"
              label={{
                value: `Mean: ${formatNumber(computedMean, 4)}`,
                position: 'top',
                fill: '#FF6D00',
                fontFamily: 'JetBrains Mono, monospace',
                fontSize: 10,
              }}
            />
          )}
          <Bar
            dataKey="count"
            name="Count"
            fill={color}
            fillOpacity={0.8}
            isAnimationActive={true}
            animationDuration={300}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
