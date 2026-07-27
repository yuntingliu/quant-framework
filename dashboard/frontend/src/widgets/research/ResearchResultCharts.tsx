import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { CHART_COLORS } from "@/lib/constants"
import { formatNumber, formatPercent } from "@/lib/utils"
import type {
  ResearchResultChart,
  ResearchResultChartFormat,
  ResearchResultChartSeries,
} from "@/workspace/researchResults"

const TICK_STYLE = {
  fontFamily: "JetBrains Mono, monospace",
  fontSize: 10,
  fill: "hsl(var(--muted-foreground))",
}

const TOOLTIP_STYLE = {
  backgroundColor: "hsl(var(--card))",
  border: "1px solid hsl(var(--border))",
  borderRadius: "8px",
  color: "hsl(var(--foreground))",
  fontFamily: "JetBrains Mono, monospace",
  fontSize: 12,
}

function color(series: ResearchResultChartSeries, index: number): string {
  return series.color ?? CHART_COLORS[index % CHART_COLORS.length] ?? "#2962ff"
}

function formatValue(value: number, format: ResearchResultChartFormat): string {
  return format === "percent" ? formatPercent(value) : formatNumber(value, 4)
}

function seriesFormat(chart: ResearchResultChart, name: string): ResearchResultChartFormat {
  return chart.series.find((item) => item.key === name || item.label === name)?.format ?? "number"
}

function CartesianAxes({ chart }: { chart: ResearchResultChart }) {
  return (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.5} />
      <XAxis
        dataKey={chart.xKey}
        tick={TICK_STYLE}
        stroke="hsl(var(--border))"
        minTickGap={28}
        label={chart.xLabel ? {
          value: chart.xLabel,
          position: "insideBottom",
          offset: -8,
          fill: "hsl(var(--muted-foreground))",
        } : undefined}
      />
      <YAxis
        tick={TICK_STYLE}
        stroke="hsl(var(--border))"
        width={68}
        tickFormatter={(value: number) => formatValue(value, chart.series[0]?.format ?? "number")}
        label={chart.yLabel ? {
          value: chart.yLabel,
          angle: -90,
          position: "insideLeft",
          fill: "hsl(var(--muted-foreground))",
        } : undefined}
      />
      <Tooltip
        contentStyle={TOOLTIP_STYLE}
        // Recharts exposes a broader runtime value union than its formatter generic.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter={((value: number, name: string) => [formatValue(Number(value), seriesFormat(chart, name)), name]) as any}
      />
      <Legend wrapperStyle={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11 }} />
    </>
  )
}

function LineResultChart({ chart }: { chart: ResearchResultChart }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={chart.rows} margin={{ top: 8, right: 20, bottom: chart.xLabel ? 24 : 8, left: 8 }}>
        <CartesianAxes chart={chart} />
        {chart.series.map((item, index) => (
          <Line
            key={item.key}
            type="monotone"
            dataKey={item.key}
            name={item.label}
            stroke={color(item, index)}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

function AreaResultChart({ chart }: { chart: ResearchResultChart }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <AreaChart data={chart.rows} margin={{ top: 8, right: 20, bottom: chart.xLabel ? 24 : 8, left: 8 }}>
        <CartesianAxes chart={chart} />
        {chart.series.map((item, index) => (
          <Area
            key={item.key}
            type="monotone"
            dataKey={item.key}
            name={item.label}
            stroke={color(item, index)}
            fill={color(item, index)}
            fillOpacity={0.18}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

function BarResultChart({ chart }: { chart: ResearchResultChart }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={chart.rows} margin={{ top: 8, right: 20, bottom: chart.xLabel ? 24 : 8, left: 8 }}>
        <CartesianAxes chart={chart} />
        {chart.series.map((item, index) => (
          <Bar
            key={item.key}
            dataKey={item.key}
            name={item.label}
            fill={color(item, index)}
            maxBarSize={56}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

function ScatterResultChart({ chart }: { chart: ResearchResultChart }) {
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ScatterChart margin={{ top: 8, right: 20, bottom: 28, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.5} />
        <XAxis
          type="number"
          dataKey="x"
          name={chart.xLabel ?? chart.xKey}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          label={chart.xLabel ? {
            value: chart.xLabel,
            position: "insideBottom",
            offset: -12,
            fill: "hsl(var(--muted-foreground))",
          } : undefined}
        />
        <YAxis
          type="number"
          dataKey="y"
          name={chart.yLabel ?? chart.series[0]?.label}
          tick={TICK_STYLE}
          stroke="hsl(var(--border))"
          width={68}
          tickFormatter={(value: number) => formatValue(value, chart.series[0]?.format ?? "number")}
        />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          contentStyle={TOOLTIP_STYLE}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter={((value: number, name: string) => [formatValue(Number(value), seriesFormat(chart, name)), name]) as any}
        />
        <Legend wrapperStyle={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11 }} />
        {chart.series.map((item, index) => (
          <Scatter
            key={item.key}
            name={item.label}
            fill={color(item, index)}
            data={chart.rows
              .filter((row) => typeof row[chart.xKey] === "number" && typeof row[item.key] === "number")
              .map((row) => ({ x: row[chart.xKey], y: row[item.key] }))}
            isAnimationActive={false}
          />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  )
}

function PieResultChart({ chart }: { chart: ResearchResultChart }) {
  const series = chart.series[0]!
  return (
    <ResponsiveContainer width="100%" height={340}>
      <PieChart>
        <Pie
          data={chart.rows}
          dataKey={series.key}
          nameKey={chart.xKey}
          cx="50%"
          cy="48%"
          innerRadius={58}
          outerRadius={108}
          paddingAngle={2}
          isAnimationActive={false}
        >
          {chart.rows.map((_row, index) => (
            <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length] ?? "#2962ff"} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter={((value: number) => [formatValue(Number(value), series.format), series.label]) as any}
        />
        <Legend wrapperStyle={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  )
}

function ResearchChart({ chart }: { chart: ResearchResultChart }) {
  return (
    <section className="rounded border border-border bg-background p-3">
      <div className="text-sm font-semibold text-foreground">{chart.title}</div>
      {chart.description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{chart.description}</p> : null}
      <div className="mt-3">
        {chart.type === "line" ? <LineResultChart chart={chart} /> : null}
        {chart.type === "area" ? <AreaResultChart chart={chart} /> : null}
        {chart.type === "bar" ? <BarResultChart chart={chart} /> : null}
        {chart.type === "scatter" ? <ScatterResultChart chart={chart} /> : null}
        {chart.type === "pie" ? <PieResultChart chart={chart} /> : null}
      </div>
    </section>
  )
}

export function ResearchResultCharts({ charts }: { charts: ResearchResultChart[] }) {
  return (
    <div className="mx-auto grid max-w-6xl gap-4">
      {charts.map((chart) => <ResearchChart key={chart.id} chart={chart} />)}
    </div>
  )
}
