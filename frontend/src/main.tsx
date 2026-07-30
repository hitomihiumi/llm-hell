import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LayoutProvider, ThemeProvider, ToastProvider } from "@nmmty/dotmatrix";
import "@nmmty/dotmatrix/styles.css";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import "./index.css";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme="dark" palette="blue" border="rounded" density="normal">
      <LayoutProvider>
        <ToastProvider>
          <QueryClientProvider client={queryClient}>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </QueryClientProvider>
        </ToastProvider>
      </LayoutProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
