import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.jsx';
import './styles/base.css';

// Derived from vite's `base` rather than repeated as a literal. The two have to
// agree -- the failure when they do not is a page that renders once and then
// 404s on the first navigation -- and the only way to guarantee agreement is
// not to write the value down twice. vite sets BASE_URL from `base` at build
// time; React Router wants it without the trailing slash.
const BASENAME = import.meta.env.BASE_URL.replace(/\/$/, '');

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter basename={BASENAME}>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
