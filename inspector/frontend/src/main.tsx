import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ThemeProvider } from '@reportportal/ui-kit';
// ui-kit first, app styles second: app.css must win where the two overlap.
import '@reportportal/ui-kit/style.css';
import './styles/app.css';
import App from './App';
import { AppProvider } from './app/state';

const container = document.getElementById('root');
if (!container) throw new Error('Root container not found');

createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      <AppProvider>
        <App />
      </AppProvider>
    </ThemeProvider>
  </StrictMode>,
);
