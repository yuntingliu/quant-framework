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
import { formatPercent } from '@/lib/utils'
import { useLanguage } from '@/contexts/LanguageContext'

interface DrawdownDataPoint {
  date: string
  strategy: number
  benchmark?: number
}

interface DrawdownChartProps {
  data: DrawdownDataPoint[]
  height?: number | `${number}%`
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

export function DrawdownChart({ data, height = 300 }: DrawdownChartProps) {
  const { language } = useLanguage()
  const hasBenchmark = data.some((d) => d.benchmark !== undefined)

  const dateFormatter = (value: string) => {
    if (!value) return ''
    const d = new Date(value)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    return `${y}-${m}`
  }

  const tooltipFormatter = (value: number, name: string) => [
    formatPercent(value),
    name,
  ]

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
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
        <Area
          type="monotone"
          dataKey="strategy"
          name={language === 'zh' ? '策略' : 'Strategy'}
          stroke="rgba(220, 53, 69, 0.8)"
          fill="rgba(220, 53, 69, 0.25)"
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={true}
          animationDuration={300}
        />
        {hasBenchmark && (
          <Area
            type="monotone"
            dataKey="benchmark"
            name={language === 'zh' ? '基准' : 'Benchmark'}
            stroke="#2962FF"
            fill="rgba(41, 98, 255, 0.1)"
            strokeWidth={1}
            strokeDasharray="4 3"
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  )
}
