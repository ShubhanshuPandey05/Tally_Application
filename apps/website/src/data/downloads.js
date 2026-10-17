/*
 * What is actually downloadable.
 *
 * The authority is /downloads/manifest.json, which `python run.py publish`
 * generates by measuring the bytes being served -- version, SHA-256 and size
 * are never typed by a human, because a hand-copied checksum is how you ship an
 * update every client refuses. `useManifest()` fetches it at runtime, so the
 * moment a new build is published this page is correct without a rebuild.
 *
 * The constants below are the fallback used until that fetch resolves, and on a
 * dev server where no manifest exists. They describe the LAST PUBLISHED build,
 * which is not necessarily the version in the source tree -- publishing is what
 * moves them.
 */

export const PUBLISHED = {
  connector: {
    version: '0.8.0',
    file: 'PrimeAnalystConnector-Setup-0.8.0.exe',
    url: '/downloads/PrimeAnalystConnector-Setup-0.8.0.exe',
    size_bytes: 48622543,
    sha256: '8d46f2369a1a45916d01d8ea9640606c39d6f1bd68516485d17cb795074f0af9',
  },
  android: {
    version: '0.11.0',
    file: 'PrimeAnalyst-0.11.0.apk',
    url: '/downloads/PrimeAnalyst-0.11.0.apk',
    size_bytes: 36739900,
    sha256: 'c3e365573f0fd7e294297e8acef0b45c6d61f89d648e01ac5ca6ee31fba29e32',
  },
};

/** The three things a customer does, in order. */
export const SETUP_STEPS = [
  {
    n: '1',
    title: 'Turn on Tally’s gateway',
    body:
      'In TallyPrime: F1 → Settings → Connectivity → Client/Server configuration. Set “TallyPrime acts as” to Both, port 9000. Once, on the PC where Tally runs.',
  },
  {
    n: '2',
    title: 'Install the connector',
    body:
      'Run the installer on that same PC. It asks for nothing — no admin rights, no keys to type — and starts again at every logon.',
  },
  {
    n: '3',
    title: 'Scan the code it shows',
    body:
      'The connector opens a window with a code in it. In the app: Tally PCs → Add a PC → point the camera at it. Then link the companies you want to see; the first sync pulls history in chunks so your Tally stays usable while it runs.',
  },
];
