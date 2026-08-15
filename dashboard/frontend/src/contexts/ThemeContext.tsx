import React, { createContext, useContext, useLayoutEffect, useState } from "react"

type Theme = "light" | "dark"

const THEME_KEY = "alphalab-theme"
const THEME_DEFAULT_VERSION_KEY = "alphalab-theme-default-version"
const THEME_DEFAULT_VERSION = "3"

interface ThemeContextType {
  theme: Theme
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

interface ThemeProviderProps {
  children: React.ReactNode
  defaultTheme?: Theme
}

function normalizeTheme(value: unknown, fallback: Theme): Theme {
  return value === "light" || value === "dark" ? value : fallback
}

export function ThemeProvider({
  children,
  defaultTheme = "dark",
}: ThemeProviderProps) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      let stored = localStorage.getItem(THEME_KEY)
      const version = localStorage.getItem(THEME_DEFAULT_VERSION_KEY)
      if (version !== THEME_DEFAULT_VERSION) {
        stored = defaultTheme
        localStorage.setItem(THEME_KEY, stored)
        localStorage.setItem(THEME_DEFAULT_VERSION_KEY, THEME_DEFAULT_VERSION)
      }
      return normalizeTheme(stored, defaultTheme)
    } catch {
      return defaultTheme
    }
  })

  useLayoutEffect(() => {
    const root = document.documentElement
    if (theme === "dark") {
      root.classList.add("dark")
    } else {
      root.classList.remove("dark")
    }
    localStorage.setItem(THEME_KEY, theme)
    localStorage.setItem(THEME_DEFAULT_VERSION_KEY, THEME_DEFAULT_VERSION)
  }, [theme])

  const toggleTheme = () => {
    setTheme((prev) => (prev === "light" ? "dark" : "light"))
  }

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error("useTheme must be used within ThemeProvider")
  }
  return context
}
