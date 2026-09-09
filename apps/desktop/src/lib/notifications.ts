export interface AlertEventPayload {
  id?: string
  symbol?: string
  rule_type?: string
  threshold?: number
  observed?: number
  message?: string
  triggered_at?: string
}

export function notifyAlert(event: AlertEventPayload): void {
  console.info("AlphaLab alert notification disabled in barebone GUI", event)
}

export function useAlertNotifications(): void {
  return
}
