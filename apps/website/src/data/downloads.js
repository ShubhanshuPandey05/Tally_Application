/*
 * Download targets.
 *
 * PLACEHOLDER URLS. The paths match what the build actually produces --
 * `python run.py connector` writes
 * apps/connector/dist/installer/TallyFlowConnector-Setup-<version>.exe and
 * `python run.py release` writes the app bundle -- so publishing is a matter of
 * copying those artefacts to /downloads on the CDN and updating the versions
 * and sizes here.
 */

export const CONNECTOR_VERSION = '0.1.0';
export const APP_VERSION = '0.1.0';

export const CONNECTOR = {
  name: 'TallyFlow Connector',
  platform: 'Windows 10 / 11 · 64-bit',
  file: `TallyFlowConnector-Setup-${CONNECTOR_VERSION}.exe`,
  href: `/downloads/TallyFlowConnector-Setup-${CONNECTOR_VERSION}.exe`,
  size: '18.4 MB',
  checksum: 'sha256:  a7f3…9c21',
  points: [
    'Installs per-user — no admin rights, no UAC prompt',
    'Asks for the Connector ID and Secret from the app’s pairing screen',
    'Starts automatically at logon and reconnects on its own',
    'Silent rollout: /VERYSILENT /ID=<id> /SECRET=<secret>',
  ],
};

export const MOBILE = {
  android: {
    store: 'Google Play',
    href: '#',
    note: 'Android 8.0+',
    badge: 'play',
  },
  ios: {
    store: 'App Store',
    href: '#',
    note: 'iOS 14+',
    badge: 'apple',
  },
  apk: {
    name: `TallyFlow-${APP_VERSION}.apk`,
    href: `/downloads/TallyFlow-${APP_VERSION}.apk`,
    size: '24.1 MB',
  },
};

/** The three steps the pairing wizard walks a customer through. */
export const SETUP_STEPS = [
  {
    n: '01',
    title: 'Turn on Tally’s gateway',
    body: 'In TallyPrime: F1 → Settings → Connectivity → Client/Server. Set “TallyPrime acts as” to Both, port 9000. One time, thirty seconds.',
  },
  {
    n: '02',
    title: 'Install the connector',
    body: 'Run the installer on the PC where Tally lives. Paste the Connector ID and Secret the app shows you. It registers itself to start at logon.',
  },
  {
    n: '03',
    title: 'Open the app',
    body: 'Your companies appear as soon as the connector says hello. Every figure arrives stamped with the moment it was read from Tally.',
  },
];
