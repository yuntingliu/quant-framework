import { CalendarRange } from "lucide-react"

import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import type { DataProfile } from "@/lib/data-profile"

export function ProfileSelect({
  profile,
  onChange,
}: {
  profile: DataProfile
  onChange: (profile: DataProfile) => void
}) {
  return (
    <select
      aria-label="Data profile"
      className="analytics-select"
      value={profile}
      onChange={(event) => onChange(event.target.value as DataProfile)}
    >
      <option value="demo">Demo</option>
      <option value="runtime">Local RQ</option>
    </select>
  )
}

export function AnalyticsFilters({
  profile,
  onProfileChange,
  showDates = false,
}: {
  profile: DataProfile
  onProfileChange: (profile: DataProfile) => void
  showDates?: boolean
}) {
  const { startDate, endDate, setStartDate, setEndDate } = useGlobalFilter()
  return (
    <>
      {showDates && (
        <div className="analytics-date-filter" title="Shared analytics date range">
          <CalendarRange aria-hidden="true" />
          <input
            aria-label="Analytics start month"
            type="month"
            value={startDate}
            onChange={(event) => setStartDate(event.target.value)}
          />
          <span>to</span>
          <input
            aria-label="Analytics end month"
            type="month"
            value={endDate}
            onChange={(event) => setEndDate(event.target.value)}
          />
        </div>
      )}
      <ProfileSelect profile={profile} onChange={onProfileChange} />
    </>
  )
}
