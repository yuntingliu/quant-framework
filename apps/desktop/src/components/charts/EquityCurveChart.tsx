'use client'

import {
  ResponsiveContainer,
  LineChart,
  AreaChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import { formatPercent, formatNumber } from '@/lib/utils'
import { toDateLabel, toNamedValue } from '@/components/charts/recharts-formatters'

interface EquityDataPoint {
  date: string
  strategy: number
  benchmark: number
}

interface DrawdownDataPoint {
  date: string
  strategy: number
  benchmark: number
}

interface EquityCurveChartProps {
  equityData: EquityDataPoint[]
  drawdownData: DrawdownDataPoint[]
  height?: number
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

const TOOLTIP_STYLE = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: '8px',
  color: 'hsl(var(--foreground))',
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 12,
}

const dateFormatter = (value: string) => {
  if (!value) return ''
  const d = new Date(value)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  return `${y}-${m}`
}

export function EquityCurveChart({
  equityData,
  drawdownData,
  height = 500,
}: EquityCurveChartProps) {
  const topHeight = Math.round(height * 0.65)
  const bottomHeight = Math.round(height * 0.35)

  return (
    <div style={{ width: '100%', height }}>
      {/* Equity Curve (top panel) */}
      <ResponsiveContainer width="100%" height={topHeight}>
        <LineChart
          data={equityData}
          margin={{ top: 5, right: 20, bottom: 0, left: 10 }}
          syncId="equityCurve"
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
            hide
          />
          <YAxis
            tickFormatter={(v: number) => formatNumber(v, 2)}
            tick={TICK_STYLE}
            stroke="hsl(var(--border))"
            width={65}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelFormatter={toDateLabel(dateFormatter)}
            formatter={toNamedValue((value) => formatNumber(value, 3))}
          />
          <Legend
            verticalAlign="top"
            height={30}
            wrapperStyle={{
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 12,
            }}
          />
          <Line
            type="monotone"
            dataKey="strategy"
            name="Strategy"
            stroke="#2962FF"
            strokeWidth={2}
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
          />
          <Line
            type="monotone"
            dataKey="benchmark"
            name="MKT Benchmark"
            stroke="#FF6D00"
            strokeWidth={1.5}
            strokeDasharray="5 3"
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
          />
        </LineChart>
      </ResponsiveContainer>

      {/* Drawdown (bottom panel) */}
      <ResponsiveContainer width="100%" height={bottomHeight}>
        <AreaChart
          data={drawdownData}
          margin={{ top: 0, right: 20, bottom: 5, left: 10 }}
          syncId="equityCurve"
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
            width={65}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelFormatter={toDateLabel(dateFormatter)}
            formatter={toNamedValue((value) => formatPercent(value))}
          />
          <Area
            type="monotone"
            dataKey="strategy"
            name="Strategy DD"
            stroke="#2962FF"
            fill="rgba(41,98,255,0.15)"
            strokeWidth={1}
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
          />
          <Area
            type="monotone"
            dataKey="benchmark"
            name="Benchmark DD"
            stroke="#FF6D00"
            fill="transparent"
            strokeWidth={1}
            strokeDasharray="5 3"
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
