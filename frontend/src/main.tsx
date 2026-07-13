import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import '@livekit/components-styles';
import './styles.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element #root not found in index.html');
}

createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
