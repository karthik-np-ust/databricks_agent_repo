import { FluentProvider } from "@fluentui/react-components";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/auth";
import { AppShell } from "./components/AppShell";
import { ModeContext } from "./mode";
import { Audit } from "./pages/Audit";
import { CommandCenter } from "./pages/CommandCenter";
import { Costs } from "./pages/Costs";
import { Approvals, Fixes } from "./pages/Fixes";
import { IncidentDetail } from "./pages/IncidentDetail";
import { Incidents } from "./pages/Incidents";
import { LiveRuns } from "./pages/LiveRuns";
import { Login } from "./pages/Login";
import { Observability } from "./pages/Observability";
import { PipelineDetail } from "./pages/PipelineDetail";
import { Pipelines } from "./pages/Pipelines";
import { Settings } from "./pages/Settings";
import { darkTheme, lightTheme } from "./theme";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000, refetchOnWindowFocus: true } },
});

function Routed({ dark, toggleDark }: { dark: boolean; toggleDark: () => void }) {
  const { user } = useAuth();
  if (!user) return <Login />;
  return (
    <AppShell dark={dark} toggleDark={toggleDark}>
      <Routes>
        <Route path="/" element={<CommandCenter />} />
        <Route path="/pipelines" element={<Pipelines />} />
        <Route path="/pipelines/:platform/:id" element={<PipelineDetail />} />
        <Route path="/runs" element={<LiveRuns />} />
        <Route path="/incidents" element={<Incidents />} />
        <Route path="/incidents/:id" element={<IncidentDetail />} />
        <Route path="/fixes" element={<Fixes />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/costs" element={<Costs />} />
        <Route path="/observability" element={<Observability />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

export function App() {
  // Dark mode is a chosen theme (own validated chart steps), not an automatic flip.
  const [dark, setDark] = useState(() => localStorage.getItem("ops-theme") === "dark" ||
    (localStorage.getItem("ops-theme") === null && window.matchMedia?.("(prefers-color-scheme: dark)").matches));
  useEffect(() => {
    localStorage.setItem("ops-theme", dark ? "dark" : "light");
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);
  return (
    <FluentProvider theme={dark ? darkTheme : lightTheme} style={{ minHeight: "100vh" }}>
      <ModeContext.Provider value={dark ? "dark" : "light"}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <BrowserRouter>
              <Routed dark={dark} toggleDark={() => setDark((d) => !d)} />
            </BrowserRouter>
          </AuthProvider>
        </QueryClientProvider>
      </ModeContext.Provider>
    </FluentProvider>
  );
}
