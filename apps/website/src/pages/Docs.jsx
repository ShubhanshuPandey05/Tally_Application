import { useManifest, mb } from '../hooks.js';
import { CONTACT_EMAIL } from '../data/site.js';
import { Link } from '../router.jsx';
import '../components/docs.css';

const TOC = [
  ['before', 'Before you start'],
  ['tally', '1 · Turn on Tally’s gateway'],
  ['account', '2 · Create your account'],
  ['pc', '3 · Add the PC in the app'],
  ['connector', '4 · Install the connector'],
  ['companies', '5 · Choose your companies'],
  ['verify', 'Checking it works'],
  ['bulk', 'Several shops at once'],
  ['updates', 'Updates and removal'],
  ['trouble', 'Troubleshooting'],
  ['files', 'Where things are stored'],
];

/*
 * Every symptom below has been seen for real, and each row says what the thing
 * actually means rather than a step to try. A troubleshooting table that only
 * lists remedies teaches nobody anything, and the second time it happens the
 * customer is back on email.
 */
const TROUBLE = [
  [
    'The app says “Waiting for approval”',
    'Your account exists but has not been activated yet. Nothing is wrong with the install. Write to us and we will turn it on.',
  ],
  [
    'The pairing screen stays on “Waiting for the PC…”',
    'The connector has not reached us yet. On the PC, open Start → TallyFlow → Connector Status. It should report that this computer is paired, and running.',
  ],
  [
    '“Check Tally Connection” cannot reach Tally',
    'TallyPrime is closed, no company is open in it, or the gateway on port 9000 is off. Re-check step 1.',
  ],
  [
    'Everything is connected but a company shows nothing',
    'That company is not open in TallyPrime. Tally answers for a closed company with an empty result rather than an error, so the app can only report what it was given.',
  ],
  [
    'One part of the dashboard stays empty while the rest works',
    'That read is taking longer than the time it is allowed. Run “Check Tally Connection” and look at the timing table — anything marked SLOW cannot reach the dashboard in time.',
  ],
  [
    'Nothing responds, and Tally looks normal',
    'Look for an open dialog box in TallyPrime. Tally answers no requests at all while a message box is waiting for somebody to click OK.',
  ],
  [
    'Windows SmartScreen blocks the installer',
    'Expected until the file is code-signed. More info → Run anyway. Compare the SHA-256 shown on the download page first if you want to be certain.',
  ],
  [
    'The installer rejects the server address',
    'It has to begin with wss:// (or ws:// for a local test server).',
  ],
  [
    'You lost the secret key',
    'It is shown once and stored encrypted, so it cannot be handed back. Delete that connection in the app and create a new one.',
  ],
];

const PATHS = [
  ['Programs', '%LOCALAPPDATA%\\Programs\\TallyFlow Connector\\'],
  ['Pairing details', '…\\TallyFlow Connector\\connector.json'],
  ['Logs', '%LOCALAPPDATA%\\TallyFlow Connector\\logs'],
  ['Startup entry', 'Task Scheduler → TallyFlow Connector'],
];

export default function Docs() {
  const { connector, android } = useManifest();

  return (
    <main>
      <div className="docs-hero">
        <div className="shell">
          <span className="label">Setup guide</span>
          <h1>Getting TallyFlow running.</h1>
          <p>
            Five steps, done once — on the PC where TallyPrime already runs, and on the
            phone you will use. About fifteen minutes, most of which is the first sync
            running by itself.
          </p>
        </div>
      </div>

      <div className="shell docs-layout">
        <nav className="docs-toc" aria-label="On this page">
          <h2>On this page</h2>
          {TOC.map(([id, label]) => (
            <a key={id} href={`#${id}`}>
              {label}
            </a>
          ))}
        </nav>

        <div className="docs-body">
          <section id="before">
            <h2>Before you start</h2>
            <ul>
              <li>
                <b>A Windows PC running TallyPrime.</b> Windows 10 or 11, 64-bit. This
                is the machine the connector goes on — usually the billing PC.
              </li>
              <li>
                <b>An Android phone.</b> Android 8.0 or later. There is no iPhone app
                yet.
              </li>
              <li>
                <b>The company you want to see, open in Tally.</b> TallyFlow can only
                read companies that are open in TallyPrime. That is deliberate: it is
                what keeps you in control of what the app can see.
              </li>
              <li>
                <b>Internet on the PC.</b> Outbound only — no static IP, and no ports
                opened on your machine.
              </li>
            </ul>

            <div className="docs-callout">
              <b>You do not need administrator rights.</b>
              The connector installs for your Windows user only, so there is no UAC
              prompt and a shop owner can run it without calling anybody.
            </div>

            <p style={{ marginTop: 22 }}>
              Both downloads are on the{' '}
              <Link className="link" to="/#download">
                download page
              </Link>{' '}
              — connector v{connector.version} ({mb(connector.size_bytes)}) and app v
              {android.version} ({mb(android.size_bytes)}).
            </p>
          </section>

          <section id="tally">
            <h2>1 · Turn on Tally’s gateway</h2>
            <p>
              TallyPrime can answer requests from other programs on the same machine,
              but the setting is off out of the box. On the PC where Tally runs:
            </p>
            <ol>
              <li>
                Press <b>F1</b>, then <b>Settings</b>
              </li>
              <li>
                Open <b>Connectivity</b>, then <b>Client/Server configuration</b>
              </li>
              <li>
                Set <b>TallyPrime acts as</b> to <b>Both</b>
              </li>
              <li>
                Leave <b>Port</b> at <b>9000</b>
              </li>
              <li>Accept the screen</li>
            </ol>
            <p>
              That is the whole change, and it only allows programs on that same
              computer to talk to Tally. Nothing outside the machine gains access.
            </p>
          </section>

          <section id="account">
            <h2>2 · Create your account</h2>
            <p>
              Install the app on your phone and tap <b>Create account</b>. It asks for
              your business name, your name, an email address and a password.
            </p>
            <div className="docs-callout">
              <b>New accounts start switched off.</b>
              Yours is created with no Tally PCs, no companies and no extra users until
              we activate it, and the app will say it is waiting for approval. That is
              the normal state on day one, not an error. Email{' '}
              <a className="link" href={`mailto:${CONTACT_EMAIL}`}>
                {CONTACT_EMAIL}
              </a>{' '}
              and we will set it up with you.
            </div>
          </section>

          <section id="pc">
            <h2>3 · Add the PC in the app</h2>
            <p>
              Once the account is active, go to <b>Account</b> → <b>Tally PCs</b> →{' '}
              <b>Add a PC</b>.
            </p>
            <ol>
              <li>
                Give the computer a name you will recognise later — “Shop PC”, “Back
                office”, “Warehouse”. This is the name the app uses when it tells you
                which branch has gone offline.
              </li>
              <li>
                Tap <b>Create connection</b>. The app shows a <b>Connector ID</b>, a{' '}
                <b>Pairing code</b> and a <b>Secret key</b>.
              </li>
              <li>
                Leave that screen open — you are about to type two of those into the
                installer.
              </li>
            </ol>
            <div className="docs-callout">
              <b>The secret key is shown once.</b>
              It is stored encrypted, so nobody — us included — can read it back to
              you. If it is lost, delete that connection in the app and create a new
              one. Copying it across now is easier than doing this twice.
            </div>
          </section>

          <section id="connector">
            <h2>4 · Install the connector</h2>
            <p>
              On the Tally PC, download{' '}
              <a className="link num" href={connector.url} download>
                {connector.file}
              </a>{' '}
              and run it. Windows will warn that the publisher is unknown, because the
              file is not code-signed yet: choose <b>More info</b> → <b>Run anyway</b>.
            </p>
            <p>The wizard asks for three things on one page:</p>
            <div className="docs-scroll">
              <table className="docs-table">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>What to enter</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Connector ID</td>
                    <td>Copy it from the app screen in step 3</td>
                  </tr>
                  <tr>
                    <td>Connector secret</td>
                    <td>The secret key from that same screen</td>
                  </tr>
                  <tr>
                    <td>Server address</td>
                    <td>
                      Already filled in — leave it as it is unless we told you otherwise
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p>
              Setup copies the program in, saves the pairing, registers it to start at
              every logon, and starts it straight away. On the last page you can tick{' '}
              <b>Check the connection to TallyPrime now</b> to confirm it can reach
              Tally.
            </p>
            <p>
              Back on your phone, the pairing screen changes from “Waiting for the PC…”
              to <b>Choose companies</b> within a few seconds.
            </p>
          </section>

          <section id="companies">
            <h2>5 · Choose your companies</h2>
            <p>
              Tap <b>Choose companies</b> and pick the ones you want on your phone. Only
              companies currently open in TallyPrime appear in the list.
            </p>
            <p>
              The first sync then reads your history in small slices, pausing between
              them. It is deliberately unhurried: asking a desktop TallyPrime for a
              year of vouchers in one go is what makes Tally stop responding, and that
              PC is usually your billing counter. The dashboard fills in as it goes.
            </p>
            <div className="docs-callout">
              <b>You will see the financial year that is open in Tally.</b>
              TallyPrime only exposes the open company’s current period over its
              gateway, so earlier years genuinely cannot be read. We would rather say so
              here than let you find out from a chart that stops.
            </div>
          </section>

          <section id="verify">
            <h2>Checking it works</h2>
            <p>
              The installer adds shortcuts under <b>Start → TallyFlow</b>:
            </p>
            <ul>
              <li>
                <b>Connector Status</b> — is this PC paired, is it running, is it
                reaching us
              </li>
              <li>
                <b>Check Tally Connection</b> — can it talk to TallyPrime right now, and
                how long each read takes
              </li>
              <li>
                <b>Connector Logs</b> — opens the log folder
              </li>
            </ul>
            <p>
              A healthy status reports that this computer is paired and that the startup
              entry is running. In the app, <b>Account</b> → <b>Tally PCs</b> shows the
              same thing from the other side: the hostname, the connector version, how
              many companies it serves, and when it was last seen.
            </p>
          </section>

          <section id="bulk">
            <h2>Several shops at once</h2>
            <p>
              The installer takes command-line switches, so twenty PCs does not mean
              twenty wizards:
            </p>
            <pre>
              <code>{`${connector.file} /VERYSILENT /ID=<id> /SECRET=<secret>`}</code>
            </pre>
            <p>
              Add <code>/SERVER=wss://…</code> to point at a different backend. When
              both <code>/ID</code> and <code>/SECRET</code> are supplied the pairing
              page is skipped entirely.
            </p>
            <p>
              <b>Each machine still needs its own pairing</b> — create a separate
              connection in the app for every one. One pairing shared across PCs would
              leave you unable to tell which shop went offline, which is most of the
              reason for listing them.
            </p>
          </section>

          <section id="updates">
            <h2>Updates and removal</h2>

            <h3>The connector</h3>
            <p>
              It updates itself. New builds install quietly, and only once your Tally
              has been idle for a while — an unrequested restart in the middle of
              billing is not something a shop forgives. You can also run a newer
              installer over the top by hand; your pairing survives it.
            </p>

            <h3>The app</h3>
            <p>
              The app tells you when a newer version exists, under <b>Account</b>. The
              download starts on its own, then Android shows its own install
              confirmation, which no app is allowed to skip.
            </p>

            <h3>Removing the connector</h3>
            <p>
              Settings → Apps → <b>TallyFlow Connector</b> → Uninstall. That takes away
              the startup entry, the program, the saved pairing and the logs — removing
              the product removes the credential with it.
            </p>
          </section>

          <section id="trouble">
            <h2>Troubleshooting</h2>
            <div className="docs-scroll">
              <table className="docs-table">
                <thead>
                  <tr>
                    <th>What you see</th>
                    <th>What it means</th>
                  </tr>
                </thead>
                <tbody>
                  {TROUBLE.map(([symptom, meaning]) => (
                    <tr key={symptom}>
                      <td>{symptom}</td>
                      <td>{meaning}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              Still stuck? Send us the log folder from the PC — the path is just below —
              and what the app was showing at the time:{' '}
              <a className="link" href={`mailto:${CONTACT_EMAIL}`}>
                {CONTACT_EMAIL}
              </a>
              .
            </p>
          </section>

          <section id="files">
            <h2>Where things are stored</h2>
            <div className="docs-scroll">
              <table className="docs-table">
                <thead>
                  <tr>
                    <th>What</th>
                    <th>Path on the Tally PC</th>
                  </tr>
                </thead>
                <tbody>
                  {PATHS.map(([what, path]) => (
                    <tr key={what}>
                      <td>{what}</td>
                      <td className="num">{path}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
