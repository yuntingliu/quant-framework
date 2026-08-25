import { useEffect, useState } from "react"

export type DataProfile = "demo" | "runtime"

const STORAGE_KEY = "alphalab-data-profile"
const EXPLICIT_CHOICE_KEY = "alphalab-data-profile-explicit-v1"
export const DATA_PROFILE_EVENT = "alphalab:data-profile"

export function getDataProfile(): DataProfile {
  if (typeof window === "undefined") return "demo"
  return window.localStorage.getItem(STORAGE_KEY) === "runtime" ? "runtime" : "demo"
}

export function setDataProfile(profile: DataProfile): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(STORAGE_KEY, profile)
  window.localStorage.setItem(EXPLICIT_CHOICE_KEY, "true")
  window.dispatchEvent(new CustomEvent(DATA_PROFILE_EVENT, { detail: profile }))
}

export function setDetectedDataProfile(profile: DataProfile): void {
  if (typeof window === "undefined") return
  if (window.localStorage.getItem(EXPLICIT_CHOICE_KEY) === "true") return
  window.localStorage.setItem(STORAGE_KEY, profile)
  window.dispatchEvent(new CustomEvent(DATA_PROFILE_EVENT, { detail: profile }))
}

export function hasExplicitDataProfile(): boolean {
  return typeof window !== "undefined"
    && window.localStorage.getItem(EXPLICIT_CHOICE_KEY) === "true"
}

export function useDataProfile(): readonly [DataProfile, (profile: DataProfile) => void] {
  const [profile, updateProfile] = useState<DataProfile>(getDataProfile)
  useEffect(() => {
    const handleProfile = (event: Event) => {
      updateProfile((event as CustomEvent<DataProfile>).detail)
    }
    window.addEventListener(DATA_PROFILE_EVENT, handleProfile)
    return () => window.removeEventListener(DATA_PROFILE_EVENT, handleProfile)
  }, [])
  return [
    profile,
    (value: DataProfile) => {
      updateProfile(value)
      setDataProfile(value)
    },
  ] as const
}
