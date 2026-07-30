import { Column, Text } from "@nmmty/dotmatrix";
import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { authApi } from "./api/auth";
import { ApiError } from "./api/client";
import { AdminEndpointsPage } from "./pages/AdminEndpoints";
import { AdminInvitesPage } from "./pages/AdminInvites";
import { LoginPage } from "./pages/Login";
import { RegisterPage } from "./pages/Register";
import { WorkbenchPage } from "./pages/Workbench";
import { useAuthStore } from "./store/auth";

function LoadingScreen() {
  return (
    <Column as="main" height="screen" alignItems="center" justifyContent="center">
      <Text color="weak">Завантаження...</Text>
    </Column>
  );
}

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { user, isLoading } = useAuthStore();
  if (isLoading) return <LoadingScreen />;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function RequireAdmin({ children }: { children: React.ReactElement }) {
  const { user, isLoading } = useAuthStore();
  if (isLoading) return <LoadingScreen />;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "admin") return <Navigate to="/" replace />;
  return children;
}

export function App() {
  const setUser = useAuthStore((s) => s.setUser);
  const setLoading = useAuthStore((s) => s.setLoading);

  useEffect(() => {
    authApi
      .me()
      .then(setUser)
      .catch((err) => {
        if (!(err instanceof ApiError)) throw err;
      })
      .finally(() => setLoading(false));
  }, [setUser, setLoading]);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <WorkbenchPage />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/invites"
        element={
          <RequireAdmin>
            <AdminInvitesPage />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/endpoints"
        element={
          <RequireAdmin>
            <AdminEndpointsPage />
          </RequireAdmin>
        }
      />
    </Routes>
  );
}
