import { useLanguage } from "@/contexts/LanguageContext"
import { translateOptions } from "./optionsText"

export function useOptionsI18n() {
  const { language } = useLanguage()
  return { language, t: (text: string) => translateOptions(text, language) }
}
