/**
 * SparkLine — minimal inline chart for KPI cards and watchlist rows.
 * Pure SVG, no external charting library overhead.
 */

interface SparkLineProps {
  data: number[]
  width?: number
  height?: number
  color?: string
  className?: string
}

export function SparkLine({
  data,
  width = 80,
  height = 24,
  color,
  className = "",
}: SparkLineProps) {
  if (!data || data.length < 2) return null

  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1

  // Determine color from trend if not specified
  const trend = data[data.length - 1] - data[0]
  const strokeColor = color || (trend >= 0 ? "oklch(0.72 0.18 152)" : "oklch(0.62 0.22 27)")

  const padding = 1
  const innerW = width - padding * 2
  const innerH = height - padding * 2

  const points = data.map((val, i) => {
    const x = padding + (i / (data.length - 1)) * innerW
    const y = padding + innerH - ((val - min) / range) * innerH
    return `${x},${y}`
  })

  const polyline = points.join(" ")

  // Gradient fill area
  const areaPath = `M ${points[0]} ${points.map((_, i) => `L ${points[i]}`).join(" ")} L ${padding + innerW},${padding + innerH} L ${padding},${padding + innerH} Z`

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
    >
      <defs>
        <linearGradient id={`spark-grad-${strokeColor.replace(/[^a-z0-9]/g, "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity={0.15} />
          <stop offset="100%" stopColor={strokeColor} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path
        d={areaPath}
        fill={`url(#spark-grad-${strokeColor.replace(/[^a-z0-9]/g, "")})`}
      />
      <polyline
        points={polyline}
        fill="none"
        stroke={strokeColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Dot on last value */}
      <circle
        cx={padding + innerW}
        cy={padding + innerH - ((data[data.length - 1] - min) / range) * innerH}
        r={2}
        fill={strokeColor}
      />
    </svg>
  )
}
