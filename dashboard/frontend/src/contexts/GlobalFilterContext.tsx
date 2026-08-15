import React, { createContext, useContext, useState, useCallback } from "react"
import type { FactorName } from "@/lib/constants"
import { DEFAULT_FACTORS } from "@/lib/constants"

interface GlobalFilterState {
  /** Start date filter (YYYY-MM format). Empty string = no filter. */
  startDate: string
  /** End date filter (YYYY-MM format). Empty string = no filter. */
  endDate: string
  /** Selected factors (checkboxes). */
  selectedFactors: FactorName[]
}

interface GlobalFilterContextType extends GlobalFilterState {
  setStartDate: (date: string) => void
  setEndDate: (date: string) => void
  toggleFactor: (factor: FactorName) => void
  setSelectedFactors: (factors: FactorName[]) => void
  resetFilters: () => void
}

const defaultState: GlobalFilterState = {
  startDate: "",
  endDate: "",
  selectedFactors: [...DEFAULT_FACTORS],
}

const GlobalFilterContext = createContext<GlobalFilterContextType | undefined>(
  undefined
)

interface GlobalFilterProviderProps {
  children: React.ReactNode
}

export function GlobalFilterProvider({ children }: GlobalFilterProviderProps) {
  const [startDate, setStartDate] = useState(defaultState.startDate)
  const [endDate, setEndDate] = useState(defaultState.endDate)
  const [selectedFactors, setSelectedFactors] = useState<FactorName[]>(
    defaultState.selectedFactors
  )

  const toggleFactor = useCallback((factor: FactorName) => {
    setSelectedFactors((prev) => {
      if (prev.includes(factor)) {
        // Don't allow deselecting all factors
        if (prev.length <= 1) return prev
        return prev.filter((f) => f !== factor)
      }
      return [...prev, factor]
    })
  }, [])

  const resetFilters = useCallback(() => {
    setStartDate(defaultState.startDate)
    setEndDate(defaultState.endDate)
    setSelectedFactors([...DEFAULT_FACTORS])
  }, [])

  return (
    <GlobalFilterContext.Provider
      value={{
        startDate,
        endDate,
        selectedFactors,
        setStartDate,
        setEndDate,
        toggleFactor,
        setSelectedFactors,
        resetFilters,
      }}
    >
      {children}
    </GlobalFilterContext.Provider>
  )
}

export function useGlobalFilter() {
  const context = useContext(GlobalFilterContext)
  if (!context) {
    throw new Error("useGlobalFilter must be used within GlobalFilterProvider")
  }
  return context
}
