import { useEffect } from "react"

import { useLanguage } from "@/contexts/LanguageContext"
import { useTheme } from "@/contexts/ThemeContext"

export function DesktopTitleBar() {
  const { language, t } = useLanguage()
  const { theme } = useTheme()
  const desktop = window.desktop

  useEffect(() => {
    void desktop?.setTheme(theme)
  }, [desktop, theme])

  if (!desktop) return null

  const menus = [
    { id: "file", label: language === "zh" ? "文件" : "File" },
    { id: "view", label: language === "zh" ? "视图" : "View" },
    { id: "tools", label: language === "zh" ? "工具" : "Tools" },
  ] as const

  return (
    <header className="desktop-titlebar shrink-0 select-none border-b border-border bg-card text-foreground">
      <div className="desktop-titlebar-content flex h-full min-w-0 items-center gap-3 px-3">
        <div className="flex shrink-0 items-center gap-2">
          <img
            src={`${import.meta.env.BASE_URL}alphalab-logo.png`}
            className="h-5 w-5"
            alt=""
            draggable={false}
          />
          <span className="text-xs font-semibold tracking-wide">AlphaLab</span>
        </div>
        <nav className="flex shrink-0 items-center gap-0.5" aria-label={language === "zh" ? "应用菜单" : "Application menu"}>
          {menus.map((menu) => (
            <button
              key={menu.id}
              type="button"
              className="desktop-titlebar-action flex h-7 items-center justify-center rounded px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-haspopup="menu"
              onClick={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect()
                void desktop.showMenu(menu.id, bounds.left, bounds.bottom, language)
              }}
            >
              {menu.label}
            </button>
          ))}
        </nav>
        <span className="min-w-0 flex-1 truncate text-center text-[11px] text-muted-foreground">
          {t("sidebar.workstation")}
        </span>
      </div>
    </header>
  )
}
