'use client'

import { useMemo } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceArea,
} from 'recharts'
import { formatPercent } from '@/lib/utils'

interface VolatilityDataPoint {
  date: string
  volatility: number
  regime?: 'low' | 'normal' | 'high'
}

interface VolatilityChartProps {
  data: VolatilityDataPoint[]
  height?: number
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

interface RegionBand {
  x1: string
  x2: string
  regime: 'low' | 'high'
}

export function VolatilityChart({
  data,
  height = 380,
}: VolatilityChartProps) {
  // Compute regime bands: contiguous date ranges of the same regime
  const regimeBands = useMemo(() => {
    const bands: RegionBand[] = []
    let currentRegime: 'low' | 'high' | null = null
    let startDate: string | null = null

    for (let i = 0; i < data.length; i++) {
      const point = data[i]
      if (!point) continue
      const regime = point.regime

      if (regime === 'low' || regime === 'high') {
        if (currentRegime !== regime) {
          // Close previous band if any
          if (currentRegime !== null && startDate !== null) {
            bands.push({
              x1: startDate,
              x2: data[i - 1]!.date,
              regime: currentRegime,
            })
          }
          currentRegime = regime
          startDate = point.date
        }
      } else {
        // Normal regime: close current band
        if (currentRegime !== null && startDate !== null) {
          bands.push({
            x1: startDate,
            x2: data[i - 1]!.date,
            regime: currentRegime,
          })
          currentRegime = null
          startDate = null
        }
      }
    }

    // Close trailing band
    if (currentRegime !== null && startDate !== null && data.length > 0) {
      bands.push({
        x1: startDate,
        x2: data[data.length - 1]!.date,
        regime: currentRegime,
      })
    }

    return bands
  }, [data])

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

  // Compute Y domain with some headroom
  const maxVol = Math.max(...data.map((d) => d.volatility), 0)
  const yMax = Math.ceil(maxVol * 120) / 100

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
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
          tickFormatter={(v: number) => formatPercent(v, 1)}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={60}
          domain={[0, yMax]}
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

        {/* Regime shading bands */}
        {regimeBands.map((band, idx) => (
          <ReferenceArea
            key={`band-${idx}`}
            x1={band.x1}
            x2={band.x2}
            fill={band.regime === 'low' ? '#00C853' : '#EF5350'}
            fillOpacity={0.08}
            strokeOpacity={0}
          />
        ))}

        <Line
          type="monotone"
          dataKey="volatility"
          name="Volatility"
          stroke="#2962FF"
          strokeWidth={2}
          dot={false}
          isAnimationActive={true}
          animationDuration={300}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
