import Nav from './components/Nav.jsx';
import Hero from './components/Hero.jsx';
import Marquee from './components/Marquee.jsx';
import Features from './components/Features.jsx';
import HowItWorks from './components/HowItWorks.jsx';
import Security from './components/Security.jsx';
import Downloads from './components/Downloads.jsx';
import Pricing from './components/Pricing.jsx';
import Faq from './components/Faq.jsx';
import CtaFooter from './components/CtaFooter.jsx';
import { useScrollReveal, usePointerGlow } from './hooks.js';

export default function App() {
  useScrollReveal();
  usePointerGlow();

  return (
    <>
      <Nav />
      <main>
        <Hero />
        <Marquee />
        <Features />
        <HowItWorks />
        <Security />
        <Downloads />
        <Pricing />
        <Faq />
        <CtaFooter />
      </main>
    </>
  );
}
