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
    version: '0.10.0',
    file: 'PrimeAnalystConnector-Setup-0.10.0.exe',
    url: '/downloads/PrimeAnalystConnector-Setup-0.10.0.exe',
    size_bytes: 48622141,
    sha256: '7059539d7c1ec0fbaf8016a6b11c579e024b23b3b920fad8c843e54f8defad65',
  },
  android: {
    version: '0.14.0',
    file: 'PrimeAnalyst-0.14.0.apk',
    url: '/downloads/PrimeAnalyst-0.14.0.apk',
    size_bytes: 38437504,
    sha256: 'f9c8c252739ff79c4faba71a68653d8bbd57d1dbf430f6020f495048a56ee761',
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
