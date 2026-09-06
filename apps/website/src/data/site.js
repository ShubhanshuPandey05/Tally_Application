/* Facts about the business, kept in one place so no component invents one. */

export const CONTACT_EMAIL = 'gaurav2tally@gmail.com';

/**
 * The three hostnames the product answers on. They are separate names on one
 * backend: this site and the downloads, the API that connectors and phones
 * dial, and the management portal.
 *
 * API_HOST is the one that cannot change cheaply -- it is compiled into the
 * connector installer and the mobile app, so moving it means re-issuing both
 * to everyone who already installed them.
 */
export const SERVICE_HOST = 'tallyflow.jsrprimesolution.com';
export const API_HOST = 'api-tallyflow.jsrprimesolution.com';
export const PORTAL_HOST = 'pd-tallyflow.jsrprimesolution.com';

/**
 * Reports the app actually ships, grouped the way the app groups them.
 *
 * Each row carries its own glyph, matching the one the app draws for it. The
 * point of showing this index at all is that somebody can recognise a screen
 * here and then find it on their phone; repeating one icon down a group would
 * make five different reports look like five copies of the same thing.
 */
export const REPORTS = [
  {
    group: 'Money',
    items: [
      ['Receivables', 'Who owes you, and for how long', 'arrow-in'],
      ['Payables', 'What you owe your suppliers', 'arrow-out'],
      ['Debtors by group', 'Every party under Sundry Debtors, net of advances', 'users'],
      ['Creditors by group', 'Every party under Sundry Creditors, net of advances', 'truck'],
      ['Ledger balances', 'Closing balances across all accounts', 'bank'],
    ],
  },
  {
    group: 'Transactions',
    items: [['Day book', 'Every voucher, day by day', 'receipt']],
  },
  {
    group: 'Stock',
    items: [
      ['Stock summary', 'What you hold and what it is worth', 'box'],
      ['Running low', 'Items at or below reorder level', 'alert'],
      ['Negative stock', 'Sold more than the books say you received', 'warn-circle'],
      ['Slow movers', 'Stock on hand that is not selling', 'hourglass'],
    ],
  },
];

export const REPORT_COUNT = REPORTS.reduce((n, g) => n + g.items.length, 0);
