import * as React from "react"
import { motion } from "framer-motion"

import { cn } from "@/lib/utils"

export type GaugeTone = "ok" | "warn" | "danger" | "primary"

const TONE_TEXT: Record<GaugeTone, string> = {
  ok: "text-emerald-500",
  warn: "text-amber-500",
  danger: "text-rose-500",
  primary: "text-primary",
}

export interface ScoreGaugeProps {
  /** Current value (same units as max). */
  value: number
  max?: number
  /** Outer diameter in px. */
  size?: number
  /** Ring thickness in px. */
  thickness?: number
  tone?: GaugeTone
  /** Small caption under the number. */
  label?: React.ReactNode
  /** Override the centred value text (defaults to rounded value). */
  display?: React.ReactNode
  className?: string
}

/**
 * Animated radial score gauge. The arc sweeps from empty to the value on mount
 * and re-animates whenever the value changes. Colour follows `tone`; the track
 * uses the muted token so it reads in both themes.
 */
export function ScoreGauge({
  value,
  max = 100,
  size = 72,
  thickness = 6,
  tone = "primary",
  label,
  display,
  className,
}: ScoreGaugeProps) {
  const pct = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0
  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - pct)

  return (
    <div
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={thickness}
          className="stroke-muted"
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={circumference}
          className={TONE_TEXT[tone]}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="font-mono text-sm font-bold tabular-nums text-foreground">
          {display ?? Math.round(value)}
        </span>
        {label ? (
          <span className="mt-0.5 text-[9px] uppercase tracking-wide text-muted-foreground">{label}</span>
        ) : null}
      </div>
    </div>
  )
}
