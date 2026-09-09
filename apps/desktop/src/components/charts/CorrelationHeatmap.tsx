'use client'

import { useMemo, useRef, useEffect, useState } from 'react'

import { useLanguage } from '@/contexts/LanguageContext'

interface CorrelationHeatmapProps {
  labels: string[]
  matrix: Array<Array<number | null>>
  height?: number
}

const LABEL_AREA = 70
const RIGHT_MARGIN = 20
const TOP_LABEL_HEIGHT = 70
const BOTTOM_MARGIN = 10

function interpolateColor(value: number): string {
  // Blue (-1) -> White (0) -> Red (+1)
  const clamped = Math.max(-1, Math.min(1, value))

  if (clamped < 0) {
    // Blue for negative
    const t = Math.abs(clamped)
    const r = Math.round(255 * (1 - t) + 41 * t)
    const g = Math.round(255 * (1 - t) + 98 * t)
    const b = Math.round(255 * (1 - t) + 255 * t)
    return `rgb(${r},${g},${b})`
  } else {
    // Red for positive
    const t = clamped
    const r = Math.round(255 * (1 - t) + 239 * t)
    const g = Math.round(255 * (1 - t) + 83 * t)
    const b = Math.round(255 * (1 - t) + 80 * t)
    return `rgb(${r},${g},${b})`
  }
}

function getTextColor(value: number): string {
  return Math.abs(value) > 0.5 ? '#ffffff' : '#333333'
}

export function CorrelationHeatmap({
  labels,
  matrix,
  height: propHeight,
}: CorrelationHeatmapProps) {
  const { language } = useLanguage()
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = useState(500)

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

  const n = labels.length

  const computed = useMemo(() => {
    const availableWidth = containerWidth - LABEL_AREA - RIGHT_MARGIN
    const cellSize = Math.max(30, Math.min(60, availableWidth / n))
    const gridWidth = cellSize * n
    const gridHeight = cellSize * n
    const svgWidth = LABEL_AREA + gridWidth + RIGHT_MARGIN
    const svgHeight = TOP_LABEL_HEIGHT + gridHeight + BOTTOM_MARGIN
    return { cellSize, gridWidth, gridHeight, svgWidth, svgHeight }
  }, [containerWidth, n])

  const { cellSize, svgWidth, svgHeight } = computed
  const finalHeight = propHeight ?? svgHeight

  return (
    <div ref={containerRef} style={{ width: '100%' }}>
      <svg
        width={svgWidth}
        height={finalHeight}
        style={{ display: 'block' }}
        role="img"
        aria-label={language === 'zh' ? '相关性矩阵热力图' : 'Correlation matrix heatmap'}
      >
        {/* Top labels (rotated) */}
        {labels.map((label, i) => (
          <text
            key={`top-${i}`}
            x={LABEL_AREA + i * cellSize + cellSize / 2}
            y={TOP_LABEL_HEIGHT - 8}
            textAnchor="end"
            fill="hsl(var(--muted-foreground))"
            fontFamily="JetBrains Mono, monospace"
            fontSize={11}
            transform={`rotate(-45, ${LABEL_AREA + i * cellSize + cellSize / 2}, ${TOP_LABEL_HEIGHT - 8})`}
          >
            {label}
          </text>
        ))}

        {/* Left labels */}
        {labels.map((label, i) => (
          <text
            key={`left-${i}`}
            x={LABEL_AREA - 8}
            y={TOP_LABEL_HEIGHT + i * cellSize + cellSize / 2 + 4}
            textAnchor="end"
            fill="hsl(var(--muted-foreground))"
            fontFamily="JetBrains Mono, monospace"
            fontSize={11}
          >
            {label}
          </text>
        ))}

        {/* Cells */}
        {matrix.map((row, rowIdx) =>
          row.map((value, colIdx) => {
            const x = LABEL_AREA + colIdx * cellSize
            const y = TOP_LABEL_HEIGHT + rowIdx * cellSize
            const isDiagonal = rowIdx === colIdx
            const hasValue = value != null && Number.isFinite(value)
            const numericValue = hasValue ? value as number : 0

            return (
              <g key={`cell-${rowIdx}-${colIdx}`}>
                <rect
                  x={x}
                  y={y}
                  width={cellSize}
                  height={cellSize}
                  fill={hasValue ? interpolateColor(numericValue) : 'hsl(var(--muted))'}
                  stroke={isDiagonal ? 'hsl(var(--foreground))' : 'hsl(var(--border))'}
                  strokeWidth={isDiagonal ? 2 : 0.5}
                  rx={2}
                />
                <text
                  x={x + cellSize / 2}
                  y={y + cellSize / 2 + 4}
                  textAnchor="middle"
                  fill={hasValue ? getTextColor(numericValue) : 'hsl(var(--muted-foreground))'}
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={cellSize > 40 ? 11 : 9}
                  fontWeight={isDiagonal ? 700 : 400}
                >
                  {hasValue ? numericValue.toFixed(2) : '—'}
                </text>
              </g>
            )
          })
        )}
      </svg>
    </div>
  )
}
