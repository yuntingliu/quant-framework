'use client'

import { useMemo, useRef, useEffect, useState } from 'react'

interface MonthlyHeatmapDataPoint {
  year: number
  month: number
  value: number
}

interface MonthlyHeatmapProps {
  data: MonthlyHeatmapDataPoint[]
  height?: number
  title?: string
}

const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

const CELL_PADDING = 2
const HEADER_HEIGHT = 28
const YEAR_LABEL_WIDTH = 50
const RIGHT_MARGIN = 10

function interpolateColor(value: number, minVal: number, maxVal: number): string {
  // Normalize to [-1, 1] range centered on 0
  const absMax = Math.max(Math.abs(minVal), Math.abs(maxVal), 0.001)
  const normalized = Math.max(-1, Math.min(1, value / absMax))

  if (normalized < 0) {
    // Red for negative: interpolate from white to red
    const t = Math.abs(normalized)
    const r = 255
    const g = Math.round(255 * (1 - t) + 68 * t)
    const b = Math.round(255 * (1 - t) + 68 * t)
    return `rgb(${r},${g},${b})`
  } else {
    // Green for positive: interpolate from white to green
    const t = normalized
    const r = Math.round(255 * (1 - t) + 46 * t)
    const g = Math.round(255 * (1 - t) + 184 * t)
    const b = Math.round(255 * (1 - t) + 80 * t)
    return `rgb(${r},${g},${b})`
  }
}

function getTextColor(value: number, minVal: number, maxVal: number): string {
  const absMax = Math.max(Math.abs(minVal), Math.abs(maxVal), 0.001)
  const normalized = Math.abs(value / absMax)
  return normalized > 0.55 ? '#ffffff' : '#333333'
}

export function MonthlyHeatmap({
  data,
  height: propHeight,
  title,
}: MonthlyHeatmapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = useState(700)

  useEffect(() => {
    if (!containerRef.current) return
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width)
      }
    })
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])

  const { years, grid, minVal, maxVal } = useMemo(() => {
    const yearSet = new Set<number>()
    const gridMap: Record<string, number> = {}
    let min = 0
    let max = 0

    for (const d of data) {
      yearSet.add(d.year)
      gridMap[`${d.year}-${d.month}`] = d.value
      if (d.value < min) min = d.value
      if (d.value > max) max = d.value
    }

    return {
      years: Array.from(yearSet).sort((a, b) => a - b),
      grid: gridMap,
      minVal: min,
      maxVal: max,
    }
  }, [data])

  const cellWidth = Math.max(
    30,
    (containerWidth - YEAR_LABEL_WIDTH - RIGHT_MARGIN) / 12 - CELL_PADDING
  )
  const cellHeight = 32
  const totalHeight =
    propHeight ??
    HEADER_HEIGHT + years.length * (cellHeight + CELL_PADDING) + 10 + (title ? 28 : 0)
  const svgWidth = containerWidth

  const titleOffset = title ? 28 : 0

  return (
    <div ref={containerRef} style={{ width: '100%' }}>
      <svg
        width={svgWidth}
        height={totalHeight}
        style={{ display: 'block' }}
        role="img"
        aria-label={title ?? 'Monthly returns heatmap'}
      >
        {/* Title */}
        {title && (
          <text
            x={svgWidth / 2}
            y={18}
            textAnchor="middle"
            fill="hsl(var(--foreground))"
            fontFamily="JetBrains Mono, monospace"
            fontSize={13}
            fontWeight={600}
          >
            {title}
          </text>
        )}

        {/* Month headers */}
        {MONTH_LABELS.map((label, i) => (
          <text
            key={`header-${i}`}
            x={YEAR_LABEL_WIDTH + i * (cellWidth + CELL_PADDING) + cellWidth / 2}
            y={HEADER_HEIGHT - 6 + titleOffset}
            textAnchor="middle"
            fill="hsl(var(--muted-foreground))"
            fontFamily="JetBrains Mono, monospace"
            fontSize={11}
          >
            {label}
          </text>
        ))}

        {/* Rows */}
        {years.map((year, rowIdx) => {
          const y = HEADER_HEIGHT + rowIdx * (cellHeight + CELL_PADDING) + titleOffset
          return (
            <g key={`row-${year}`}>
              {/* Year label */}
              <text
                x={YEAR_LABEL_WIDTH - 8}
                y={y + cellHeight / 2 + 4}
                textAnchor="end"
                fill="hsl(var(--muted-foreground))"
                fontFamily="JetBrains Mono, monospace"
                fontSize={11}
              >
                {year}
              </text>

              {/* Month cells */}
              {Array.from({ length: 12 }, (_, monthIdx) => {
                const cellKey = `${year}-${monthIdx + 1}`
                const value = grid[cellKey]
                const hasValue = value !== undefined && !isNaN(value)
                const cx =
                  YEAR_LABEL_WIDTH + monthIdx * (cellWidth + CELL_PADDING)

                return (
                  <g key={cellKey}>
                    <rect
                      x={cx}
                      y={y}
                      width={cellWidth}
                      height={cellHeight}
                      rx={3}
                      fill={
                        hasValue
                          ? interpolateColor(value, minVal, maxVal)
                          : 'hsl(var(--muted))'
                      }
                      opacity={hasValue ? 1 : 0.3}
                    />
                    {hasValue && (
                      <text
                        x={cx + cellWidth / 2}
                        y={y + cellHeight / 2 + 4}
                        textAnchor="middle"
                        fill={getTextColor(value, minVal, maxVal)}
                        fontFamily="JetBrains Mono, monospace"
                        fontSize={10}
                      >
                        {`${(value * 100).toFixed(1)}%`}
                      </text>
                    )}
                  </g>
                )
              })}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
