import { useOptionsI18n } from "@/lib/useOptionsI18n"
export function Metric({label,value,detail}:{label:string;value:string;detail?:string}){
  const {t}=useOptionsI18n()
  return <div className="rounded-lg border border-border bg-card p-3"><div className="text-xs text-muted-foreground">{t(label)}</div><div className="mt-1 font-mono text-lg font-semibold tabular-nums">{value}</div>{detail&&<div className="mt-1 text-[11px] text-muted-foreground">{t(detail)}</div>}</div>
}
