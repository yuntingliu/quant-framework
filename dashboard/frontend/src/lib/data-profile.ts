export type DataProfile = "runtime"

export function getDataProfile(): DataProfile {
  return "runtime"
}

export function useDataProfile(): readonly [DataProfile] {
  return ["runtime"] as const
}
