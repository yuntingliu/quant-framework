import { CalendarRange } from "lucide-react"

import { useGlobalFilter } from "@/contexts/GlobalFilterContext"
import { useLanguage } from "@/contexts/LanguageContext"
import type { DataProfile } from "@/lib/data-profile"

export function ProfileSelect({
  profile,
  onChange,
}: {
  profile: DataProfile
  onChange: (profile: DataProfile) => void
}) {
  const { language } = useLanguage()
  const copy = language === "zh"
    ? { profile: "数据画像", demo: "演示数据", runtime: "本地 RQ" }
    : { profile: "Data profile", demo: "Demo", runtime: "Local RQ" }
  return (
    <select
      aria-label={copy.profile}
      className="analytics-select"
      value={profile}
      onChange={(event) => onChange(event.target.value as DataProfile)}
    >
      <option value="demo">{copy.demo}</option>
      <option value="runtime">{copy.runtime}</option>
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
  const { language } = useLanguage()
  const copy = language === "zh"
    ? { range: "共享分析区间", start: "分析开始月份", to: "至", end: "分析结束月份" }
    : { range: "Shared analytics date range", start: "Analytics start month", to: "to", end: "Analytics end month" }
  const { startDate, endDate, setStartDate, setEndDate } = useGlobalFilter()
  return (
    <>
      {showDates && (
        <div className="analytics-date-filter" title={copy.range}>
          <CalendarRange aria-hidden="true" />
          <input
            aria-label={copy.start}
            type="month"
            value={startDate}
            onChange={(event) => setStartDate(event.target.value)}
          />
          <span>{copy.to}</span>
          <input
            aria-label={copy.end}
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
