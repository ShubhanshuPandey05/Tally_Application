import Hero from '../components/Hero.jsx';
import Features from '../components/Features.jsx';
import HowItWorks from '../components/HowItWorks.jsx';
import Security from '../components/Security.jsx';
import Downloads from '../components/Downloads.jsx';
import Access from '../components/Access.jsx';
import Faq from '../components/Faq.jsx';

export default function Home() {
  return (
    <main>
      <Hero />
      <Features />
      <HowItWorks />
      <Security />
      <Downloads />
      <Access />
      <Faq />
    </main>
  );
}
