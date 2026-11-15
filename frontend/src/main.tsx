import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./components/shared/ErrorBoundary";
import { DisclaimerGate } from "./components/shared/Disclaimer";
import { ModeProvider } from "./contexts/ModeContext";
import { ThemeProvider } from "./contexts/ThemeContext";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ModeProvider>
        <ErrorBoundary>
          <DisclaimerGate>
            <App />
          </DisclaimerGate>
        </ErrorBoundary>
      </ModeProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
