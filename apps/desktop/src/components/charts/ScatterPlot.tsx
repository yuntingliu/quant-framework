'use client'

import {
  ResponsiveContainer,
  ComposedChart,
  Scatter,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ZAxis,
} from 'recharts'
import { formatPercent, formatNumber } from '@/lib/utils'

interface ScatterDataPoint {
  x: number
  y: number
  name?: string
  color?: string
  size?: number
  shape?: string
}

interface FrontierLine {
  data: { x: number; y: number }[]
  color?: string
  name?: string
}

interface ScatterPlotProps {
  data: ScatterDataPoint[]
  line?: FrontierLine
  xLabel?: string
  yLabel?: string
  xFormat?: 'percent' | 'number'
  yFormat?: 'percent' | 'number'
  height?: number
}

const TICK_STYLE = {
  fontFamily: 'JetBrains Mono, monospace',
  fontSize: 11,
  fill: 'hsl(var(--muted-foreground))',
}

const MODEL_COLORS: Record<string, string> = {
  'Min Variance': '#00BFA5',
  'Max Sharpe': '#FF6D00',
  'Factor-Constrained': '#AB47BC',
  'Risk Parity': '#26A69A',
  HRP: '#EC407A',
  'Mean-Variance (Max Sharpe)': '#FF6D00',
}

interface CustomTooltipProps {
  active?: boolean
  payload?: Array<{ payload: ScatterDataPoint }>
  xFormat: 'percent' | 'number'
  yFormat: 'percent' | 'number'
  xLabel: string
  yLabel: string
}

function CustomTooltip({
  active,
  payload,
  xFormat,
  yFormat,
  xLabel,
  yLabel,
}: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null

  const firstPayload = payload[0]
  if (!firstPayload) return null
  const point = firstPayload.payload
  const xFormatted =
    xFormat === 'percent' ? formatPercent(point.x) : formatNumber(point.x, 4)
  const yFormatted =
    yFormat === 'percent' ? formatPercent(point.y) : formatNumber(point.y, 4)

  return (
    <div
      style={{
        backgroundColor: 'hsl(var(--card))',
        border: '1px solid hsl(var(--border))',
        borderRadius: '8px',
        color: 'hsl(var(--foreground))',
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 12,
        padding: '8px 12px',
      }}
    >
      {point.name && (
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{point.name}</div>
      )}
      <div>
        {xLabel}: {xFormatted}
      </div>
      <div>
        {yLabel}: {yFormatted}
      </div>
    </div>
  )
}

export function ScatterPlot({
  data,
  line,
  xLabel = 'X',
  yLabel = 'Y',
  xFormat = 'number',
  yFormat = 'number',
  height = 500,
}: ScatterPlotProps) {
  const xFormatter = (v: number) =>
    xFormat === 'percent' ? formatPercent(v, 1) : formatNumber(v, 2)

  const yFormatter = (v: number) =>
    yFormat === 'percent' ? formatPercent(v, 1) : formatNumber(v, 2)

  // Group data by name (for different shapes/colors in legend)
  const groups: Record<string, ScatterDataPoint[]> = {}
  const ungrouped: ScatterDataPoint[] = []

  for (const point of data) {
    if (point.name) {
      if (!groups[point.name]) {
        groups[point.name] = []
      }
      groups[point.name]!.push(point)
    } else {
      ungrouped.push(point)
    }
  }

  const groupNames = Object.keys(groups)

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart margin={{ top: 10, right: 20, bottom: 30, left: 20 }}>
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="hsl(var(--border))"
          opacity={0.5}
        />
        <XAxis
          dataKey="x"
          type="number"
          name={xLabel}
          tickFormatter={xFormatter}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          label={{
            value: xLabel,
            position: 'insideBottom',
            offset: -20,
            fill: 'hsl(var(--muted-foreground))',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 12,
          }}
        />
        <YAxis
          dataKey="y"
          type="number"
          name={yLabel}
          tickFormatter={yFormatter}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={65}
          label={{
            value: yLabel,
            angle: -90,
            position: 'insideLeft',
            offset: 0,
            fill: 'hsl(var(--muted-foreground))',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 12,
          }}
        />
        <ZAxis dataKey="size" range={[60, 200]} />
        <Tooltip
          content={
            <CustomTooltip
              xFormat={xFormat}
              yFormat={yFormat}
              xLabel={xLabel}
              yLabel={yLabel}
            />
          }
        />
        <Legend
          verticalAlign="top"
          height={36}
          wrapperStyle={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: 12,
          }}
        />

        {/* Frontier curve */}
        {line && line.data.length > 0 && (
          <Line
            data={line.data}
            dataKey="y"
            name={line.name ?? 'Efficient Frontier'}
            stroke={line.color ?? '#2962FF'}
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={true}
            animationDuration={300}
            legendType="line"
          />
        )}

        {/* Ungrouped scatter points */}
        {ungrouped.length > 0 && (
          <Scatter
            data={ungrouped}
            fill="#888888"
            name="Points"
            isAnimationActive={true}
            animationDuration={300}
          />
        )}

        {/* Grouped scatter points with different colors */}
        {groupNames.map((name) => {
          const points = groups[name] ?? []
          const firstPoint = points[0]
          const color =
            firstPoint?.color ??
            MODEL_COLORS[name] ??
            '#888888'

          return (
            <Scatter
              key={name}
              data={points}
              name={name}
              fill={color}
              isAnimationActive={true}
              animationDuration={300}
            />
          )
        })}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
