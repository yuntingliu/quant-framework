import { ProfileSettings } from "./configuration/ProfileSettings"

export function SearchProviderSettings({ onSaved }: { onSaved: () => void }) {
  return <ProfileSettings service="search" onSaved={onSaved} />
}
