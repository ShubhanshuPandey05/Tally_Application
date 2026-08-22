import Icon from './Icon.jsx';
import './security.css';

/*
 * Every claim here is true of the code that ships. If one stops being true, it
 * comes off this page before the release goes out -- a security page that has
 * drifted is worse than no security page.
 */
const ITEMS = [
  {
    title: 'There is no write path',
    body:
      'The connector builds Tally export requests and nothing else. An import request cannot be constructed, and unknown instructions are refused rather than guessed at.',
  },
  {
    title: 'Nothing is exposed on your PC',
    body:
      'No inbound port, no port forwarding, no static IP. The connector holds an outbound connection open and that is the only route in.',
  },
  {
    title: 'Passwords are hashed with Argon2id',
    body:
      'Sign-in tokens are short-lived, and the refresh token that renews them is single-use. Replaying a spent one revokes the whole chain, on the assumption that replay means it leaked.',
  },
  {
    title: 'Pairing secrets are encrypted at rest',
    body:
      'The key lives outside the database, so a copy of the database on its own yields no credential that can connect anything.',
  },
  {
    title: 'One business cannot see another',
    body:
      'Asking for a company that is not yours returns “not found”, never “forbidden”. The difference matters: “forbidden” confirms the identifier exists.',
  },
  {
    title: 'Reads are recorded',
    body:
      'In a read-only product the sensitive act is the read, so every one is logged with who asked, for what, and when.',
  },
];

export default function Security() {
  return (
    <section id="security">
      <div className="shell">
        <div className="head">
          <span className="label">Security</span>
          <h2>Six decisions taken before the first screen was built.</h2>
          <p>
            Each of these is expensive to retro-fit — most would mean asking every
            customer to re-pair every PC — so none of them was left for later.
          </p>
        </div>

        <div className="sec-grid">
          {ITEMS.map((item) => (
            <div className="sec-item" key={item.title}>
              <span className="tile tile-green">
                <Icon name="check" />
              </span>
              <div>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
