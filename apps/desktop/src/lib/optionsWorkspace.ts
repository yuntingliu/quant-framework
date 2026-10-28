import type { BasketLeg, OptionRow } from "./options"

export const optionSections = ["chain", "volatility", "models", "portfolio", "research", "data"] as const
export type OptionSection = typeof optionSections[number]
export const OPTIONS_DRAFT_KEY = "alphalab-options-workspace-v1"
export interface OptionsDraft {
  tab: OptionSection; symbol: string; selectedDay: string; field: "close" | "settlement"
  manualRate: boolean; rate: string; expiry: string; legs: BasketLeg[]
  surfaceModel: "ssvi" | "svi"; sampling: "standard" | "importance"; importanceTilt: number
  paths: number; daysForward: number; correlation: number; intensity: number; jumpMean: number; jumpVol: number
}
export const defaultOptionsDraft: OptionsDraft = {
  tab:"chain", symbol:"510050.XSHG", selectedDay:"", field:"close", manualRate:false, rate:"1.2", expiry:"", legs:[],
  surfaceModel:"ssvi", sampling:"standard", importanceTilt:-2, paths:100000, daysForward:0, correlation:.8,
  intensity:1, jumpMean:-.15, jumpVol:.15,
}
export function parseOptionsDraft(raw: string | null): OptionsDraft {
  const fallback: OptionsDraft = { ...defaultOptionsDraft, legs: [] }
  if (!raw) return fallback
  try {
    const d = JSON.parse(raw) as Record<string, unknown>
    if (!d || typeof d !== "object" || Array.isArray(d)) return fallback
    const result = { ...fallback }
    if (optionSections.includes(d.tab as OptionSection)) result.tab = d.tab as OptionSection
    if (typeof d.symbol === "string" && /^\d{6}\.(XSHG|XSHE)$/.test(d.symbol)) result.symbol = d.symbol
    for (const key of ["selectedDay", "expiry"] as const) if (typeof d[key] === "string" && /^(\d{4}-\d{2}-\d{2})?$/.test(d[key])) result[key] = d[key]
    if (d.field === "settlement") result.field = "settlement"
    if (d.surfaceModel === "svi") result.surfaceModel = "svi"
    if (d.sampling === "importance") result.sampling = "importance"
    if (typeof d.manualRate === "boolean") result.manualRate = d.manualRate
    if (typeof d.rate === "string" && d.rate.length < 20 && Number.isFinite(Number(d.rate))) result.rate = d.rate
    const bounds = { paths:[1000,250000], daysForward:[0,365], correlation:[0,1], intensity:[0,100], jumpMean:[-5,2], jumpVol:[0,2], importanceTilt:[-6,6] } as const
    for (const key of Object.keys(bounds) as Array<keyof typeof bounds>) {
      const value=d[key], [lo,hi]=bounds[key]
      if (typeof value === "number" && Number.isFinite(value) && value>=lo && value<=hi && (key!=="paths" || Number.isInteger(value))) result[key]=value
    }
    if (Array.isArray(d.legs)) {
      const unique=new Set<string>()
      result.legs=d.legs.filter((l):l is BasketLeg => {
        if (!l || typeof l!=="object" || typeof l.underlying!=="string" || !/^\d{6}\.(XSHG|XSHE)$/.test(l.underlying) || typeof l.contract!=="string" || !/^[A-Za-z0-9]{1,40}$/.test(l.contract) || !Number.isFinite(l.quantity) || Math.abs(l.quantity)>100000) return false
        const key=`${l.underlying}:${l.contract}`
        if(unique.has(key))return false
        unique.add(key);return true
      }).slice(0,20).map(l=>({underlying:l.underlying,contract:l.contract,quantity:l.quantity,label:typeof l.label==="string"?l.label:l.contract}))
    }
    return result
  } catch { return fallback }
}

export interface OptionPair { key:string; expiry:string; strike:number; multiplier:number; call?:OptionRow; put?:OptionRow; ambiguous?:boolean }
export function pairOptionChain(rows:OptionRow[]):OptionPair[] {
  const groups=new Map<string,{base:OptionPair;calls:OptionRow[];puts:OptionRow[]}>()
  for(const row of rows){
    const key=JSON.stringify([row.expiry,row.strike,row.multiplier])
    const group=groups.get(key)??{base:{key,expiry:row.expiry,strike:row.strike,multiplier:row.multiplier},calls:[],puts:[]}
    group[row.option_type==="C"?"calls":"puts"].push(row);groups.set(key,group)
  }
  return [...groups.values()].flatMap<OptionPair>(g=>g.calls.length>1||g.puts.length>1
    ? [...g.calls,...g.puts].map(row=>({...g.base,key:`${g.base.key}:${row.order_book_id}`,ambiguous:true,...(row.option_type==="C"?{call:row}:{put:row})}))
    : [{...g.base,call:g.calls[0],put:g.puts[0]}]
  ).sort((a,b)=>a.expiry.localeCompare(b.expiry)||a.strike-b.strike||a.multiplier-b.multiplier)
}
