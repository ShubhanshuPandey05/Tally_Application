/*
 * TEMPORARY PLACEHOLDER PRICING.
 *
 * None of these figures are commercially agreed. They exist so the pricing
 * section can be designed and reviewed. When billing is real this file is the
 * only thing that should need replacing -- swap it for a fetch against the
 * backend and keep the shape.
 */

export const BILLING_NOTE = 'Placeholder pricing — final plans are being finalised.';

/** Yearly is charged as 10 months, i.e. two months free. */
export const YEARLY_MONTHS = 10;

export const PLANS = [
  {
    id: 'starter',
    name: 'Starter',
    tagline: 'One shop, one owner, the numbers that matter.',
    monthly: 499,
    accent: 'var(--grad-cool)',
    cta: 'Start 14-day trial',
    limits: [
      ['Companies', '1'],
      ['Users', '2'],
      ['Connectors', '1 PC'],
      ['Auto-refresh', 'Every 60 min'],
    ],
    features: [
      'Dashboard: sales, cash, bank, receivables',
      'Day Book, Ledger and Outstanding reports',
      'Stock Summary with low-stock flags',
      'Pull-to-refresh live reads',
      'Email support',
    ],
  },
  {
    id: 'growth',
    name: 'Growth',
    tagline: 'Multiple branches, a team, and the full report set.',
    monthly: 1499,
    accent: 'var(--grad-brand)',
    popular: true,
    cta: 'Start 14-day trial',
    limits: [
      ['Companies', '5'],
      ['Users', '10'],
      ['Connectors', '3 PCs'],
      ['Auto-refresh', 'Every 15 min'],
    ],
    features: [
      'Everything in Starter',
      'All 17 reports incl. P&L, Balance Sheet, Trial Balance',
      'Sales & Purchase registers with trends',
      'Top customers, suppliers and products',
      'Role-based access for staff and accountants',
      'Priority email + WhatsApp support',
    ],
  },
  {
    id: 'scale',
    name: 'Scale',
    tagline: 'Groups of companies, with audit and control.',
    monthly: 3999,
    accent: 'var(--grad-warm)',
    cta: 'Talk to us',
    limits: [
      ['Companies', 'Unlimited'],
      ['Users', 'Unlimited'],
      ['Connectors', 'Unlimited'],
      ['Auto-refresh', 'Every 5 min'],
    ],
    features: [
      'Everything in Growth',
      'Group consolidation across companies',
      'Full read audit log with export',
      'SSO and device management',
      'Silent connector rollout across sites',
      'Named onboarding engineer',
    ],
  },
];

export const PRICING_FAQ = [
  {
    q: 'Can TallyFlow change anything inside Tally?',
    a: 'No. The connector can only issue Tally export requests — the request builder physically cannot emit an import, and a test asserts every shipped query is read-only. Even if someone got hold of your credentials, there is no write path to abuse.',
  },
  {
    q: 'Do I need a static IP or to open a port on my PC?',
    a: 'Neither. The connector dials out to TallyFlow over an outbound WebSocket, the same way your browser reaches a website. No inbound port is opened and TallyPrime is never exposed to the internet.',
  },
  {
    q: 'What happens when my Tally PC is switched off?',
    a: 'The app keeps working and shows the last figures we read, clearly labelled with when they were read and a note that your PC is offline. It reconnects on its own when the PC comes back.',
  },
  {
    q: 'Will this slow down Tally while my staff are billing?',
    a: 'Reads are served from snapshots refreshed in the background, so load on your PC scales with the number of companies, not the number of people looking. Ten staff on the dashboard cost your Tally the same as one.',
  },
  {
    q: 'How many PCs does one subscription cover?',
    a: 'Each PC running TallyPrime needs its own connector install, and each is paired separately — that is what lets the app tell you exactly which branch is offline. Your plan sets how many connectors you can pair.',
  },
  {
    q: 'Which TallyPrime versions are supported?',
    a: 'TallyPrime 2.x and later, with the HTTP gateway enabled on port 9000. The installer checks this and tells you what to change if it is off.',
  },
];
