import { useEffect, useState } from "react"

export function useWorkspaceRefresh(): number {
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1)
    window.addEventListener("alphalab:refreshData", refresh)
    return () => window.removeEventListener("alphalab:refreshData", refresh)
  }, [])

  return revision
}
