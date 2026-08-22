/*
 * Session state and routing.
 *
 * Three gates, in order, and the order is the point:
 *
 *   1. no token, or a token the API refuses  -> sign in
 *   2. signed in but `must_change_password`  -> change it, nothing else
 *   3. everything else                       -> the app
 *
 * Gate 2 is not a nag banner. A portal account's first password was either
 * seeded from a deployment manifest or issued by somebody else, so until it is
 * replaced the person at the keyboard is not established. Letting them approve
 * an account "just this once" first is exactly the exception that makes the
 * requirement optional.
 */

import { useCallback, useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { api, setSignOutHandler, token } from './api.js';
import { Loading, ToastHost } from './components/ui.jsx';
import Shell from './layout/Shell.jsx';
import Accounts from './pages/Accounts.jsx';
import Activity from './pages/Activity.jsx';
import BackendLogs from './pages/BackendLogs.jsx';
import ChangePassword from './pages/ChangePassword.jsx';
import ConnectorLogs from './pages/ConnectorLogs.jsx';
import Overview from './pages/Overview.jsx';
import Partners from './pages/Partners.jsx';
import Settings from './pages/Settings.jsx';
import SignIn from './pages/SignIn.jsx';

export default function App() {
  const [me, setMe] = useState(null);
  const [ready, setReady] = useState(false);
  // Lives here rather than in the Accounts page so the sidebar badge survives
  // navigating away from it. It is the one number the portal exists to surface.
  const [pending, setPending] = useState(0);

  const signOut = useCallback(() => {
    token.clear();
    setMe(null);
    setPending(0);
  }, []);

  // Any 401 anywhere -- including one from a background poll -- lands here.
  useEffect(() => setSignOutHandler(signOut), [signOut]);

  // Resume a session from a stored token. `/me` re-reads the platform user, so
  // a partner deactivated since they last loaded the page is refused now rather
  // than when their token happens to expire.
  useEffect(() => {
    if (!token.get()) {
      setReady(true);
      return;
    }
    api('/me')
      .then(setMe)
      .catch(() => token.clear())
      .finally(() => setReady(true));
  }, []);

  const refreshPending = useCallback(() => {
    api('/stats')
      .then((stats) => setPending(stats.pending || 0))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (me && !me.must_change_password) refreshPending();
  }, [me, refreshPending]);

  if (!ready) return <Loading label="Signing in…" />;

  if (!me) {
    return (
      <ToastHost>
        <SignIn onSignedIn={setMe} />
      </ToastHost>
    );
  }

  if (me.must_change_password) {
    return (
      <ToastHost>
        <ChangePassword forced onDone={setMe} />
      </ToastHost>
    );
  }

  return (
    <ToastHost>
      <Shell me={me} pending={pending} onSignOut={signOut}>
        <Routes>
          <Route path="/" element={<Overview me={me} onCounts={setPending} />} />
          <Route path="/accounts" element={<Accounts me={me} onCounts={refreshPending} />} />
          <Route
            path="/partners"
            element={me.role === 'owner' ? <Partners /> : <Navigate to="/" replace />}
          />
          <Route
            path="/logs/backend"
            element={me.role === 'owner' ? <BackendLogs /> : <Navigate to="/" replace />}
          />
          <Route path="/logs/connector" element={<ConnectorLogs />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/settings" element={<Settings me={me} onUpdated={setMe} />} />
          {/* A stale bookmark should land somewhere useful, not on a blank
              page that looks like the portal is broken. */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </ToastHost>
  );
}
