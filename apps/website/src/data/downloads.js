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
    version: '0.2.3',
    file: 'TallyFlowConnector-Setup-0.2.3.exe',
    url: '/downloads/TallyFlowConnector-Setup-0.2.3.exe',
    size_bytes: 38117621,
    sha256: '887f263d1473c9fc3d6b280d657db8174786073f793052705582832ae11dce9d',
  },
  android: {
    version: '0.3.0',
    file: 'TallyFlow-0.3.0.apk',
    url: '/downloads/TallyFlow-0.3.0.apk',
    size_bytes: 24823657,
    sha256: 'f60f59e3525a83b5bf98820383fd45102fa004809f563efa62125140028cc2c8',
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
      'Run the installer on that same PC and paste the Connector ID and secret the app gives you. No admin rights needed; it starts again at every logon.',
  },
  {
    n: '3',
    title: 'Open the app',
    body:
      'Link the companies you want to see. The first sync pulls history in chunks so your Tally stays usable while it runs.',
  },
];
