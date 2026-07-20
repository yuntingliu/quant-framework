import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Toaster } from "sonner"
import { ErrorBoundary } from "./components/shared/ErrorBoundary"
import { ThemeProvider } from "./contexts/ThemeContext"
import { LanguageProvider } from "./contexts/LanguageContext"
import { GlobalFilterProvider } from "./contexts/GlobalFilterContext"
import { AgentPromptProvider } from "./contexts/AgentPromptContext"
import { ConfirmProvider } from "./hooks/useConfirm"
import Workspace from "./Workspace"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

function App() {
  return (
    <ErrorBoundary fallbackTitle="Application Error">
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <ThemeProvider defaultTheme="light">
            <GlobalFilterProvider>
              <AgentPromptProvider>
                <ConfirmProvider>
                  <Toaster
                    position="top-right"
                    toastOptions={{
                      className: "bg-card text-card-foreground border-border",
                    }}
                  />
                  <div className="h-screen w-screen overflow-hidden">
                    <Workspace />
                  </div>
                </ConfirmProvider>
              </AgentPromptProvider>
            </GlobalFilterProvider>
          </ThemeProvider>
        </LanguageProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  )
}

export default App
