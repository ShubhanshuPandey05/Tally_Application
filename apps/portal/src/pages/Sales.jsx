import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { Empty, Loading, Segmented, useLoad } from '../components/ui.jsx';
import { fmtCount, fmtRupees } from '../format.js';
import './overview.css';
import './usage.css';

/** The financial year containing `day`, as `[from, to]` ISO dates. */
function financialYear(day, offset = 0) {
  const start = (day.getMonth() >= 3 ? day.getFullYear() : day.getFullYear() - 1) + offset;
  return [`${start}-04-01`, `${start + 1}-03-31`];
}

function iso(day) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

function windows() {
  const today = new Date();
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  const lastMonthStart = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const lastMonthEnd = new Date(today.getFullYear(), today.getMonth(), 0);
  const [fyFrom, fyTo] = financialYear(today);
  const [lastFrom, lastTo] = financialYear(today, -1);
  return {
    month: { label: 'This month', from: iso(monthStart), to: iso(today) },
    last: { label: 'Last month', from: iso(lastMonthStart), to: iso(lastMonthEnd) },
    fy: { label: 'This financial year', from: fyFrom, to: fyTo },
    lastfy: { label: 'Last financial year', from: lastFrom, to: lastTo },
  };
}

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function monthLabel(key) {
  const [year, month] = key.split('-').map(Number);
  return `${MONTH_NAMES[month - 1]} ${year}`;
}

/**
 * Who sold how many of which plan, and for how much.
 *
 * Every number on this page comes from the record of plans sold, with the
 * price and partner copied in at the moment of sale -- so a closed month reads
 * the same next year, whatever has changed since. A partner sees only their
 * own line; an owner sees everybody, plus sales made directly.
 *
 * Revenue is shown before GST first, because that is what the business earned;
 * GST is collected on the government's behalf and sits beside it.
 */
export default function Sales({ me }) {
  const ranges = windows();
  const [range, setRange] = useState('fy');
  const chosen = ranges[range];
  const report = useLoad(
    (signal) => api(`/reports/sales?from=${chosen.from}&to=${chosen.to}`, { signal }),
    [chosen.from, chosen.to],
  );
  const data = report.data;
  const owner = me.role === 'owner';

  return (
    <>
      <div className="page-head">
        <h1>Sales</h1>
        <p className="muted">
          {owner
            ? 'Plans sold by every partner, and what each one has to look after next.'
            : 'The plans you have sold, and the accounts that need you next.'}
        </p>
      </div>

      <div className="row wrap" style={{ gap: 10 }}>
        <Segmented
          ariaLabel="Period"
          options={Object.entries(ranges).map(([value, item]) => ({ value, label: item.label }))}
          value={range}
          onChange={setRange}
        />
        {report.loading && data ? <span className="spinner" aria-label="Refreshing" /> : null}
      </div>

      {report.loading && !data ? (
        <div className="card"><Loading /></div>
      ) : report.error ? (
        <div className="card"><Empty title="Could not load sales">{report.error.message}</Empty></div>
      ) : (
        <div className={`usage-body${report.loading ? ' is-stale' : ''}`}>
          <Headline data={data} />
          <PartnerTable data={data} owner={owner} />
          <Months data={data} />
        </div>
      )}
    </>
  );
}

function Headline({ data }) {
  const total = data.totals.total;
  return (
    <div className="stat-grid">
      <div className="stat card stat-ok">
        <span className="stat-value">{fmtRupees(total.price_paise)}</span>
        <span className="stat-label">Revenue before GST</span>
        <span className="stat-note">
          {fmtRupees(total.gst_paise)} GST · {fmtRupees(total.total_paise)} billed
        </span>
      </div>
      {data.plans.map((plan) => {
        const figures = data.totals.plans[plan.code] || { count: 0, new: 0, renewals: 0 };
        return (
          <div className="stat card" key={plan.code}>
            <span className="stat-value">{fmtCount(figures.count)}</span>
            <span className="stat-label">{plan.name} plans sold</span>
            <span className="stat-note">
              {figures.new} new · {figures.renewals} renewed
            </span>
          </div>
        );
      })}
      <Link className="stat card stat-wait" to="/accounts?status=active">
        <span className="stat-value">{fmtCount(data.totals.expiring_soon)}</span>
        <span className="stat-label">Ending in {data.expiring_within_days} days</span>
        <span className="stat-note">Renewals to chase</span>
      </Link>
      <Link className="stat card stat-accent" to="/accounts?status=trial">
        <span className="stat-value">{fmtCount(data.totals.on_trial)}</span>
        <span className="stat-label">On free trial</span>
        <span className="stat-note">Ready to sell a plan</span>
      </Link>
    </div>
  );
}

function PartnerTable({ data, owner }) {
  const rows = data.partners;
  if (!rows.length) {
    return (
      <section className="card">
        <Empty title="No partners yet">Add a partner to start crediting sales.</Empty>
      </section>
    );
  }
  return (
    <section className="card">
      <div className="card-pad">
        <h2>{owner ? 'By partner' : 'Your sales'}</h2>
        <span className="muted" style={{ fontSize: 13 }}>
          Sales in the period. Accounts, trials and renewals due are as of today.
        </span>
      </div>
      <hr className="divider" />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Partner</th>
              {data.plans.map((plan) => (
                <th key={plan.code} className="right">{plan.name}</th>
              ))}
              <th className="right">Revenue</th>
              <th className="right">GST</th>
              <th className="right">Billed</th>
              <th className="right">Live plans</th>
              <th className="right">Ending soon</th>
              <th className="right">On trial</th>
              <th className="right">Waiting</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.partner_id || 'direct'}>
                <td>
                  <div className="stack">
                    <strong>{row.partner_name}</strong>
                    {row.partner_code ? (
                      <span className="dim mono">{row.partner_code}</span>
                    ) : null}
                  </div>
                </td>
                {data.plans.map((plan) => {
                  const figures = row.plans[plan.code] || { count: 0, renewals: 0 };
                  return (
                    <td key={plan.code} className="right nowrap">
                      {figures.count}
                      {figures.renewals ? (
                        <span className="dim" style={{ fontSize: 12 }}>
                          {' '}
                          ({figures.renewals} renewed)
                        </span>
                      ) : null}
                    </td>
                  );
                })}
                <td className="right nowrap">{fmtRupees(row.total.price_paise)}</td>
                <td className="right nowrap muted">{fmtRupees(row.total.gst_paise)}</td>
                <td className="right nowrap">{fmtRupees(row.total.total_paise)}</td>
                <td className="right">{row.active_accounts}</td>
                <td className="right">{row.expiring_soon}</td>
                <td className="right">{row.on_trial}</td>
                <td className="right">{row.awaiting_approval}</td>
              </tr>
            ))}
          </tbody>
          {rows.length > 1 ? (
            <tfoot>
              <tr>
                <td><strong>All</strong></td>
                {data.plans.map((plan) => (
                  <td key={plan.code} className="right">
                    <strong>{data.totals.plans[plan.code]?.count ?? 0}</strong>
                  </td>
                ))}
                <td className="right nowrap"><strong>{fmtRupees(data.totals.total.price_paise)}</strong></td>
                <td className="right nowrap muted">{fmtRupees(data.totals.total.gst_paise)}</td>
                <td className="right nowrap"><strong>{fmtRupees(data.totals.total.total_paise)}</strong></td>
                <td className="right">{data.totals.active_accounts}</td>
                <td className="right">{data.totals.expiring_soon}</td>
                <td className="right">{data.totals.on_trial}</td>
                <td className="right">{data.totals.awaiting_approval}</td>
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
    </section>
  );
}

function Months({ data }) {
  if (!data.months.length) {
    return (
      <section className="card">
        <Empty title="No plans sold in this period">
          Sales appear here as soon as an account is approved or renewed on a plan.
        </Empty>
      </section>
    );
  }
  return (
    <section className="card">
      <div className="card-pad">
        <h2>Month by month</h2>
      </div>
      <hr className="divider" />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Month</th>
              {data.plans.map((plan) => (
                <th key={plan.code} className="right">{plan.name}</th>
              ))}
              <th className="right">Billed</th>
            </tr>
          </thead>
          <tbody>
            {data.months.map((month) => (
              <tr key={month.month}>
                <td>{monthLabel(month.month)}</td>
                {data.plans.map((plan) => (
                  <td key={plan.code} className="right">{month.plans[plan.code] ?? 0}</td>
                ))}
                <td className="right nowrap">{fmtRupees(month.total_paise)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
