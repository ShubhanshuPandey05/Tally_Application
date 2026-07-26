import './marquee.css';

const ITEMS = [
  'Day Book',
  'Outstanding Receivables',
  'Stock Summary',
  'Profit & Loss',
  'Balance Sheet',
  'Sales Register',
  'Purchase Register',
  'Trial Balance',
  'Cash & Bank Book',
  'Top Customers',
  'Top Products',
  'Negative Stock',
  'GST Summary',
  'Ledger Statement',
];

export default function Marquee() {
  return (
    <div className="marquee" aria-hidden="true">
      <div className="marquee-track">
        {[0, 1].map((copy) => (
          <div className="marquee-set" key={copy}>
            {ITEMS.map((item) => (
              <span key={item + copy}>
                <i />
                {item}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
