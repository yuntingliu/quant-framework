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
  LabelList,
} from 'recharts'
import { CHART_COLORS } from '@/lib/constants'
import { formatPercent, formatNumber } from '@/lib/utils'

interface MetricConfig {
  key: string
  name: string
  color?: string
  format?: 'percent' | 'number' | 'ratio'
}

interface BarComparisonChartProps {
  data: Record<string, number | string>[]
  metrics: MetricConfig[]
  height?: number
  layout?: 'vertical' | 'horizontal'
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

function formatByType(value: number, format?: string): string {
  switch (format) {
    case 'percent':
      return formatPercent(value, 1)
    case 'ratio':
      return formatNumber(value, 2)
    case 'number':
    default:
      return formatNumber(value, 2)
  }
}

export function BarComparisonChart({
  data,
  metrics,
  height = 400,
  layout = 'vertical',
}: BarComparisonChartProps) {
  const tooltipFormatter = (value: number, name: string) => {
    const metric = metrics.find((m) => m.name === name || m.key === name)
    return [formatByType(value, metric?.format), name]
  }

  if (layout === 'horizontal') {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 5, right: 40, bottom: 5, left: 80 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="hsl(var(--border))"
            opacity={0.5}
          />
          <YAxis
            dataKey="name"
            type="category"
            tick={TICK_STYLE}
            stroke="hsl(var(--border))"
            width={80}
          />
          <XAxis
            type="number"
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
          <Legend
            verticalAlign="top"
            height={36}
            wrapperStyle={{
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 12,
            }}
          />
          {metrics.map((m, idx) => (
            <Bar
              key={m.key}
              dataKey={m.key}
              name={m.name}
              fill={m.color ?? CHART_COLORS[idx % CHART_COLORS.length]}
              isAnimationActive={true}
              animationDuration={300}
            >
              <LabelList
                dataKey={m.key}
                position="right"
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                formatter={((v: string | number) => formatByType(Number(v), m.format)) as any}
                style={{
                  fontFamily: 'JetBrains Mono, monospace',
                  fontSize: 10,
                  fill: 'hsl(var(--muted-foreground))',
                }}
              />
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    )
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
          dataKey="name"
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
        />
        <YAxis
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
        {metrics.map((m, idx) => (
          <Bar
            key={m.key}
            dataKey={m.key}
            name={m.name}
            fill={m.color ?? CHART_COLORS[idx % CHART_COLORS.length]}
            isAnimationActive={true}
            animationDuration={300}
          >
            <LabelList
              dataKey={m.key}
              position="top"
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              formatter={((v: string | number) => formatByType(Number(v), m.format)) as any}
              style={{
                fontFamily: 'JetBrains Mono, monospace',
                fontSize: 10,
                fill: 'hsl(var(--muted-foreground))',
              }}
            />
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
