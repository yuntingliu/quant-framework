import { useCallback, useSyncExternalStore, type Dispatch, type SetStateAction } from "react"
import { defaultOptionsDraft, OPTIONS_DRAFT_KEY, parseOptionsDraft, type OptionsDraft } from "@/lib/optionsWorkspace"

let draft:OptionsDraft|undefined
const listeners=new Set<()=>void>()
function snapshot(){
  if(!draft){try{draft=parseOptionsDraft(localStorage.getItem(OPTIONS_DRAFT_KEY))}catch{draft={...defaultOptionsDraft,legs:[]}}}
  return draft
}
function subscribe(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener)}}
if(typeof window!=="undefined")window.addEventListener("storage",event=>{
  if(event.key===OPTIONS_DRAFT_KEY){draft=parseOptionsDraft(event.newValue);listeners.forEach(fn=>fn())}
})
export function useOptionsDraft<K extends keyof OptionsDraft>(key:K):[OptionsDraft[K],Dispatch<SetStateAction<OptionsDraft[K]>>]{
  const state=useSyncExternalStore(subscribe,snapshot,snapshot)
  const set=useCallback((value:SetStateAction<OptionsDraft[K]>)=>{
    const current=snapshot()
    const next=typeof value==="function"?(value as (v:OptionsDraft[K])=>OptionsDraft[K])(current[key]):value
    draft={...current,[key]:next}
    try{localStorage.setItem(OPTIONS_DRAFT_KEY,JSON.stringify(draft))}catch{/* The in-memory draft still works when browser storage is unavailable. */}
    listeners.forEach(fn=>fn())
  },[key])
  return[state[key],set]
}
