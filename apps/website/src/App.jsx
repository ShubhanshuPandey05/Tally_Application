import { useEffect } from 'react';

import Nav from './components/Nav.jsx';
import Footer from './components/Footer.jsx';
import Home from './pages/Home.jsx';
import Docs from './pages/Docs.jsx';
import { useRoute } from './router.jsx';

export default function App() {
  const route = useRoute();

  // A hash in the URL on first load points at a section that has not rendered
  // yet, so the browser's own anchor jump lands nowhere. One frame is enough.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    requestAnimationFrame(() => {
      document.getElementById(id)?.scrollIntoView();
    });
  }, []);

  useEffect(() => {
    document.title =
      route === '/docs'
        ? 'Setup guide — TallyFlow'
        : 'TallyFlow — your TallyPrime numbers, on your phone';
  }, [route]);

  return (
    <>
      <Nav />
      {route === '/docs' ? <Docs /> : <Home />}
      <Footer />
    </>
  );
}
