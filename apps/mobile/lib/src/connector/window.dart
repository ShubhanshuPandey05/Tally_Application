import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../app/theme.dart';
import '../core/widgets/primitives.dart';
import 'client.dart';
import 'code_image.dart';
import 'state.dart';

/// How often the window asks the connector what it is doing.
///
/// Two seconds against a loopback socket on the same machine. Slower would make
/// the pairing countdown visibly stutter; faster would put a line in the
/// connector's log four times a second for as long as somebody leaves this
/// open.
const Duration _pollInterval = Duration(seconds: 2);

/// The connector's window.
///
/// It reports and it presses four buttons. It cannot disconnect this computer,
/// unlink a company or remove a person -- those decisions belong to whoever
/// holds the account on their phone, not to whoever is standing at the till --
/// and it shows no figure from anybody's books.
class ConnectorWindow extends StatefulWidget {
  const ConnectorWindow({super.key, required this.port});

  final int port;

  @override
  State<ConnectorWindow> createState() => _ConnectorWindowState();
}

class _ConnectorWindowState extends State<ConnectorWindow> {
  late final ConnectorClient _client = ConnectorClient(port: widget.port);
  Timer? _poll;

  ConnectorState? _state;

  /// Set only once the connector has missed [_missesBeforeDown] polls in a row.
  /// Every other failure is a line of detail on a screen that still shows what
  /// is known.
  String? _unreachable;

  /// Consecutive polls that got no answer.
  ///
  /// One is not evidence of anything: the connector restarts its own session
  /// from a button on this window, and a machine waking from sleep drops a
  /// request or two. Reporting a shop's connector as dead on the strength of a
  /// single missed poll would make this window the least trustworthy thing on
  /// the screen.
  int _misses = 0;

  static const int _missesBeforeDown = 2;

  /// The code currently drawn, and the payload it was drawn from. Kept together
  /// so a poll that carries the same payload cannot cause a repaint.
  List<String> _codeRows = const <String>[];
  String _codePayload = '';

  /// True while one of the four buttons is in flight. The connector answers
  /// every one, but a restart takes a second or two and a second press in that
  /// window would restart the session it is standing up.
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    unawaited(_refresh());
    _poll = Timer.periodic(_pollInterval, (_) => unawaited(_refresh()));
  }

  @override
  void dispose() {
    _poll?.cancel();
    _client.close();
    super.dispose();
  }

  // -- talking to the connector ----------------------------------------

  Future<void> _refresh() async {
    try {
      final ConnectorState next = await _client.fetchState();
      if (!mounted) {
        return;
      }
      setState(() {
        _state = next;
        _misses = 0;
        _unreachable = null;
      });
      await _syncCode(next.pairing.payload);
    } on ConnectorUnreachable catch (error) {
      if (mounted) {
        setState(() {
          _misses++;
          if (_misses >= _missesBeforeDown) {
            _unreachable = error.detail;
          }
        });
      }
    }
  }

  /// Fetches the code's grid, but only when the payload has actually changed.
  ///
  /// Redrawing an unchanged symbol every two seconds is the one way this window
  /// could make a code harder to scan: a phone that is mid-decode when the
  /// canvas is replaced starts again.
  Future<void> _syncCode(String payload) async {
    if (payload == _codePayload) {
      return;
    }
    if (payload.isEmpty) {
      setState(() {
        _codePayload = '';
        _codeRows = const <String>[];
      });
      return;
    }
    try {
      final List<String> rows = await _client.fetchQrRows();
      if (!mounted) {
        return;
      }
      setState(() {
        _codePayload = payload;
        _codeRows = rows;
      });
    } on ConnectorUnreachable {
      // Left alone deliberately. The next poll reports the connector being
      // unreachable; blanking the code here as well would take a working
      // symbol off screen for a hiccup that lasted one request.
    }
  }

  Future<void> _press(String action, {Map<String, Object?>? payload}) async {
    if (_busy) {
      return;
    }
    setState(() => _busy = true);
    final ActionResult result = await _client.invoke(
      action,
      payload: payload ?? const <String, Object?>{},
    );
    if (!mounted) {
      return;
    }
    setState(() => _busy = false);
    _say(result.message, ok: result.ok);
    // Straight away rather than at the next tick: a restart or a new code has
    // already changed what this window should be showing.
    unawaited(_refresh());
  }

  void _say(String message, {required bool ok}) {
    if (message.isEmpty) {
      return;
    }
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(
        SnackBar(
          content: Text(message),
          behavior: SnackBarBehavior.floating,
          width: 420,
          backgroundColor: ok ? AppTheme.inkCard : AppTheme.negative,
        ),
      );
  }

  Future<void> _changePort(int current) async {
    final int? chosen = await showDialog<int>(
      context: context,
      builder: (BuildContext context) => _PortDialog(current: current),
    );
    if (chosen != null && chosen != current) {
      await _press('port', payload: <String, Object?>{'port': chosen});
    }
  }

  // -- drawing ----------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final ConnectorState? state = _state;

    return Scaffold(
      body: Column(
        children: <Widget>[
          _TitleBar(state: state, down: _unreachable != null),
          const Divider(height: 1),
          Expanded(child: _body(state, _unreachable)),
        ],
      ),
    );
  }

  Widget _body(ConnectorState? state, String? down) {
    if (state == null) {
      return down == null
          ? const Center(child: CircularProgressIndicator())
          : _NotRunning(detail: down);
    }

    // A code with no connector behind it is worse than no code. The phone's
    // scan reaches the backend either way, but collecting the secret afterwards
    // is this process's job -- so a code left on screen after the connector
    // stopped would be scanned, appear to work on the phone, and pair nothing.
    if (down != null && state.isPairing) {
      return _NotRunning(detail: down);
    }

    if (state.isPairing) {
      return PairingPanel(
        state: state,
        rows: _codeRows,
        busy: _busy,
        onNewCode: () => _press('new-code'),
      );
    }

    return StatusPanel(
      state: state,
      busy: _busy,
      stale: down != null,
      onRefresh: () => _press('refresh'),
      onRestart: () => _press('restart'),
      onChangePort: () => _changePort(state.tally.port),
    );
  }
}

// ---------------------------------------------------------------------------
// The bar across the top
// ---------------------------------------------------------------------------

class _TitleBar extends StatelessWidget {
  const _TitleBar({required this.state, required this.down});

  final ConnectorState? state;

  /// The connector has stopped answering. The last state is still held, and its
  /// uptime is the one part of it that would keep ticking up on a machine where
  /// nothing is running -- so it is replaced rather than shown.
  final bool down;

  @override
  Widget build(BuildContext context) {
    final ConnectorState? it = state;
    final String name = (it?.connectorName ?? '').isEmpty
        ? 'This computer'
        : it!.connectorName;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Row(
        children: <Widget>[
          const BrandMark(size: 34),
          const SizedBox(width: 10),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Text(
                'TallyFlow Connector',
                style: TextStyle(
                  fontSize: 14.5,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -0.2,
                ),
              ),
              Text(
                switch ((it, down)) {
                  (_, true) => '$name · not running',
                  (null, _) => 'Starting up',
                  (final ConnectorState state, _) =>
                    '$name · running for ${_spell(state.uptime)}',
                },
                style: TextStyle(
                  fontSize: 11.5,
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
          const Spacer(),
          if (it != null && it.version.isNotEmpty)
            Text(
              'v${it.version}',
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w600,
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Nothing is listening
// ---------------------------------------------------------------------------

class _NotRunning extends StatelessWidget {
  const _NotRunning({required this.detail});

  final String detail;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(Icons.power_off_outlined, size: 34, color: scheme.onSurfaceVariant),
            const SizedBox(height: 12),
            const Text(
              'The connector is not running on this computer',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 6),
            Text(
              // Deliberately says which of the two things is wrong. "TallyFlow "
              // "is down" and "the program on this PC is not started" have
              // completely different fixes, and only one of them is the shop's
              // to carry out.
              'TallyFlow itself is unaffected. This window shows what the '
              'program on this PC is doing, and that program is not started. '
              'It starts on its own when you sign in to Windows; if it has not, '
              'restarting the computer is the fastest fix.',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 12.5,
                height: 1.45,
                color: scheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 14),
            Text(
              detail,
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Waiting to be scanned
// ---------------------------------------------------------------------------

@visibleForTesting
class PairingPanel extends StatelessWidget {
  const PairingPanel({
    super.key,
    required this.state,
    required this.rows,
    required this.busy,
    required this.onNewCode,
  });

  final ConnectorState state;
  final List<String> rows;
  final bool busy;
  final VoidCallback onNewCode;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final int left = state.pairing.secondsLeft;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 720),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Text(
                'Connect this computer to your TallyFlow account',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, letterSpacing: -0.3),
              ),
              const SizedBox(height: 4),
              Text(
                'Nothing is sent anywhere until somebody on the account scans this code.',
                style: TextStyle(fontSize: 12.5, color: scheme.onSurfaceVariant),
              ),
              const SizedBox(height: 18),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  // The white ground is the widget's own, not the card's. See
                  // PairingCodeImage: a symbol that inherited a surface colour is
                  // one that stops scanning on a dark desktop.
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(AppTheme.radiusCard),
                      border: Border.all(color: scheme.outlineVariant),
                    ),
                    child: PairingCodeImage(rows: rows),
                  ),
                  const SizedBox(width: 24),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        const _Step(1, 'Open TallyFlow on your phone and sign in.'),
                        const _Step(2, 'Go to Computers, then Add a computer.'),
                        const _Step(3, 'Point the camera at this code.'),
                        const SizedBox(height: 18),
                        if (left > 0)
                          Text(
                            'This code stops working in ${_clock(left)}. '
                            'A new one appears on its own.',
                            style: TextStyle(
                              fontSize: 12,
                              height: 1.4,
                              color: scheme.onSurfaceVariant,
                            ),
                          ),
                        if (state.pairing.detail.isNotEmpty) ...<Widget>[
                          const SizedBox(height: 8),
                          Text(
                            state.pairing.detail,
                            style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
                          ),
                        ],
                        const SizedBox(height: 14),
                        OutlinedButton.icon(
                          onPressed: busy ? null : onNewCode,
                          icon: const Icon(Icons.autorenew, size: 16),
                          label: const Text('Show a new code'),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 22),
              _MachineFacts(state: state),
            ],
          ),
        ),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step(this.number, this.text);

  final int number;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Container(
            width: 20,
            height: 20,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              color: AppTheme.accentSoft,
              shape: BoxShape.circle,
            ),
            child: Text(
              '$number',
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w700,
                color: AppTheme.accentInk,
              ),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(text, style: const TextStyle(fontSize: 13, height: 1.35)),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Paired: what this machine is doing
// ---------------------------------------------------------------------------

@visibleForTesting
class StatusPanel extends StatelessWidget {
  const StatusPanel({
    super.key,
    required this.state,
    required this.busy,
    required this.stale,
    required this.onRefresh,
    required this.onRestart,
    required this.onChangePort,
  });

  final ConnectorState state;
  final bool busy;

  /// The last poll did not get an answer. What is on screen is still the last
  /// thing the connector said, and saying so is better than replacing a full
  /// screen with an error for one dropped request.
  final bool stale;

  final VoidCallback onRefresh;
  final VoidCallback onRestart;
  final VoidCallback onChangePort;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final AccountState account = state.account;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              if (stale)
                const Padding(
                  padding: EdgeInsets.only(bottom: 12),
                  child: _Notice(
                    'This window lost touch with the connector a moment ago. '
                    'What is below is the last thing it said.',
                  ),
                ),
              // IntrinsicHeight, and not `stretch` on its own. The two cards
              // should end up the same height whichever has more to say, and
              // inside a scroll view the incoming height is unbounded -- so
              // stretching to it asks both cards to be infinitely tall, which
              // in a release build is not an error, just a ruined screen.
              IntrinsicHeight(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Expanded(child: _BackendCard(state.backend)),
                    const SizedBox(width: 12),
                    Expanded(child: _TallyCard(state.tally)),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              _Section(
                title: 'Companies this computer feeds',
                trailing: TextButton.icon(
                  onPressed: busy ? null : onRefresh,
                  icon: const Icon(Icons.refresh, size: 15),
                  label: const Text('Refresh'),
                ),
                child: account.companies.isEmpty
                    ? _Empty(
                        account.isEmpty
                            ? 'Waiting for TallyFlow to say which companies this '
                                'computer is for.'
                            : 'No company on the account is set to read from this '
                                'computer yet. Add one in the app.',
                      )
                    : Column(
                        children: <Widget>[
                          for (final FedCompany company in account.companies)
                            _CompanyRow(company),
                        ],
                      ),
              ),
              const SizedBox(height: 18),
              _Section(
                title: 'Who can see them',
                trailing: account.asOf == null
                    ? null
                    : Text(
                        'as of ${_ago(account.asOf!)}',
                        style: TextStyle(
                          fontSize: 11.5,
                          color: scheme.onSurfaceVariant,
                        ),
                      ),
                child: account.users.isEmpty
                    ? const _Empty('Waiting for TallyFlow to send the list.')
                    : Column(
                        children: <Widget>[
                          for (final RosterUser user in account.users) _UserRow(user),
                        ],
                      ),
              ),
              const SizedBox(height: 18),
              _MachineFacts(
                state: state,
                onChangePort: busy ? null : onChangePort,
                onRestart: busy ? null : onRestart,
              ),
              const SizedBox(height: 8),
              Text(
                // The one sentence that answers the question this window most
                // often gets opened for.
                'Only people on your TallyFlow account can see these companies, '
                'and only through the app. This computer answers nothing else.',
                style: TextStyle(fontSize: 11.5, height: 1.4, color: scheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _BackendCard extends StatelessWidget {
  const _BackendCard(this.backend);

  final BackendState backend;

  @override
  Widget build(BuildContext context) {
    return _StatusCard(
      icon: Icons.cloud_outlined,
      label: 'TallyFlow',
      good: backend.connected,
      headline: backend.connected ? 'Connected' : 'Not connected',
      detail: backend.connected
          ? 'for ${_spell(backend.connectedFor ?? Duration.zero)}'
          : (backend.detail.isEmpty ? 'Trying again shortly' : backend.detail),
      footnote: backend.url,
    );
  }
}

class _TallyCard extends StatelessWidget {
  const _TallyCard(this.tally);

  final TallyState tally;

  @override
  Widget build(BuildContext context) {
    // Three states, not two. Null is "we have not asked yet", and telling
    // somebody TallyPrime is not answering when nothing has looked sends them
    // to restart software that is running perfectly.
    final (bool? good, String headline, String detail) = switch (tally.online) {
      null => (null, 'Checking', 'Asking TallyPrime whether it is there'),
      true => (true, 'Answering', 'on port ${tally.port}'),
      false => (
          false,
          'Not answering',
          'TallyPrime may be closed, or a dialog box may be open in it',
        ),
    };

    return _StatusCard(
      icon: Icons.desktop_windows_outlined,
      label: 'TallyPrime',
      good: good,
      headline: headline,
      detail: detail,
      footnote: tally.companiesOpen.isEmpty
          ? 'No company open'
          : 'Open: ${tally.companiesOpen.join(', ')}',
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({
    required this.icon,
    required this.label,
    required this.good,
    required this.headline,
    required this.detail,
    required this.footnote,
  });

  final IconData icon;
  final String label;

  /// Null renders neither green nor red. See [_TallyCard].
  final bool? good;

  final String headline;
  final String detail;
  final String footnote;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final Color dot = switch (good) {
      true => AppTheme.positive,
      false => AppTheme.negative,
      null => scheme.onSurfaceVariant,
    };

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(icon, size: 15, color: scheme.onSurfaceVariant),
                const SizedBox(width: 6),
                Text(
                  label.toUpperCase(),
                  style: TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                    color: scheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: <Widget>[
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
                ),
                const SizedBox(width: 8),
                Text(
                  headline,
                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: 3),
            Text(
              detail,
              style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
            ),
            const SizedBox(height: 8),
            Text(
              footnote,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}

class _CompanyRow extends StatelessWidget {
  const _CompanyRow(this.company);

  final FedCompany company;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final DateTime? synced = company.lastSyncedAt;

    // The name in Tally, but only when it differs. Shown because the two drift
    // apart the moment somebody renames one, and that difference is what
    // explains a dashboard that stopped filling in.
    final bool renamed = company.tallyName.isNotEmpty &&
        company.tallyName.toLowerCase() != company.name.toLowerCase();

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  company.name,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: company.isActive ? null : scheme.onSurfaceVariant,
                  ),
                ),
                if (renamed)
                  Text(
                    'in TallyPrime: ${company.tallyName}',
                    style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
                  ),
              ],
            ),
          ),
          if (!company.isActive)
            const Padding(
              padding: EdgeInsets.only(right: 10),
              child: _Tag('paused'),
            ),
          Text(
            // Never "synced never", and never a zero: no sync yet and a sync
            // that returned nothing are different facts.
            synced == null ? 'not synced yet' : 'synced ${_ago(synced)}',
            style: TextStyle(fontSize: 11.5, color: scheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

class _UserRow extends StatelessWidget {
  const _UserRow(this.user);

  final RosterUser user;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  user.name.isEmpty ? user.email : user.name,
                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                ),
                if (user.name.isNotEmpty && user.email.isNotEmpty)
                  Text(
                    user.email,
                    style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
                  ),
              ],
            ),
          ),
          if (user.role.isNotEmpty) _Tag(user.role),
          const SizedBox(width: 10),
          SizedBox(
            width: 120,
            child: Text(
              user.hasAccess ? 'can see them' : 'no access',
              textAlign: TextAlign.right,
              style: TextStyle(
                fontSize: 11.5,
                color: user.hasAccess ? AppTheme.positive : scheme.onSurfaceVariant,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// The settings and addresses somebody on a support call gets asked for.
class _MachineFacts extends StatelessWidget {
  const _MachineFacts({
    required this.state,
    this.onChangePort,
    this.onRestart,
  });

  final ConnectorState state;

  /// Null while a button is already in flight, and on the pairing screen, where
  /// the only sensible action is to scan the code.
  final VoidCallback? onChangePort;
  final VoidCallback? onRestart;

  @override
  Widget build(BuildContext context) {
    return _Section(
      title: 'This computer',
      child: Column(
        children: <Widget>[
          _FactRow(
            label: 'TallyPrime port',
            value: '${state.tally.port}',
            action: onChangePort == null
                ? null
                : TextButton(onPressed: onChangePort, child: const Text('Change')),
          ),
          _FactRow(label: 'TallyFlow address', value: state.backend.url),
          if (state.connectorId.isNotEmpty)
            _FactRow(label: 'Computer ID', value: state.connectorId),
          _FactRow(
            label: 'Log folder',
            value: state.logDir.isEmpty ? 'not writing logs to disk' : state.logDir,
          ),
          if (onRestart != null) ...<Widget>[
            const SizedBox(height: 10),
            Align(
              alignment: Alignment.centerLeft,
              child: OutlinedButton.icon(
                onPressed: onRestart,
                icon: const Icon(Icons.restart_alt, size: 16),
                // "Reconnect", not "Restart the connector": what this does is
                // stand up a new session against freshly loaded settings, and
                // nothing on this machine stops or starts.
                label: const Text('Reconnect'),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _FactRow extends StatelessWidget {
  const _FactRow({required this.label, required this.value, this.action});

  final String label;
  final String value;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: <Widget>[
          SizedBox(
            width: 140,
            child: Text(
              label,
              style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
            ),
          ),
          Expanded(
            // Selectable, because half of what is on this row exists to be read
            // down a telephone or pasted into a support message.
            child: SelectableText(
              value,
              maxLines: 1,
              style: const TextStyle(fontSize: 12.5),
            ),
          ),
          if (action != null) action!,
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.child, this.trailing});

  final String title;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Row(
          children: <Widget>[
            Text(
              title.toUpperCase(),
              style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.6,
                color: scheme.onSurfaceVariant,
              ),
            ),
            const Spacer(),
            if (trailing != null) trailing!,
          ],
        ),
        const SizedBox(height: 4),
        Card(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
            child: child,
          ),
        ),
      ],
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Text(
        message,
        style: TextStyle(
          fontSize: 12.5,
          height: 1.4,
          color: Theme.of(context).colorScheme.onSurfaceVariant,
        ),
      ),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(AppTheme.radiusTile),
      ),
      child: Row(
        children: <Widget>[
          Icon(Icons.info_outline, size: 15, color: scheme.onSurfaceVariant),
          const SizedBox(width: 8),
          Expanded(
            child: Text(message, style: const TextStyle(fontSize: 12, height: 1.35)),
          ),
        ],
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 10.5,
          fontWeight: FontWeight.w600,
          color: scheme.onSurfaceVariant,
        ),
      ),
    );
  }
}

/// Asks for a port, and refuses anything that is not one.
///
/// Validated here as well as in the connector. The connector's check is the one
/// that matters -- this window is not the only thing that can call it -- but a
/// dialog that accepts "9000 " and then reports a failure two seconds later
/// reads as the product being broken.
class _PortDialog extends StatefulWidget {
  const _PortDialog({required this.current});

  final int current;

  @override
  State<_PortDialog> createState() => _PortDialogState();
}

class _PortDialogState extends State<_PortDialog> {
  late final TextEditingController _controller =
      TextEditingController(text: '${widget.current}');
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final int? port = int.tryParse(_controller.text.trim());
    if (port == null || port < 1 || port > 65535) {
      setState(() => _error = 'A port is a number between 1 and 65535.');
      return;
    }
    Navigator.of(context).pop(port);
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Which port is TallyPrime on?'),
      content: SizedBox(
        width: 360,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'In TallyPrime this is under F1 > Settings > Connectivity > '
              'Client/Server configuration. It is 9000 unless somebody changed it.',
              style: TextStyle(fontSize: 12.5, height: 1.4),
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _controller,
              autofocus: true,
              keyboardType: TextInputType.number,
              inputFormatters: <TextInputFormatter>[
                FilteringTextInputFormatter.digitsOnly,
              ],
              decoration: InputDecoration(labelText: 'Port', errorText: _error),
              onSubmitted: (_) => _submit(),
            ),
            const SizedBox(height: 10),
            Text(
              'Saving this reconnects to TallyPrime straight away.',
              style: TextStyle(
                fontSize: 11.5,
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(onPressed: _submit, child: const Text('Save')),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/// A duration in the shortest form that is still true.
String _spell(Duration value) {
  if (value.inMinutes < 1) {
    return '${value.inSeconds}s';
  }
  if (value.inHours < 1) {
    return '${value.inMinutes}m';
  }
  if (value.inDays < 1) {
    return '${value.inHours}h ${value.inMinutes % 60}m';
  }
  return '${value.inDays}d ${value.inHours % 24}h';
}

/// A countdown, as a clock. Minutes and seconds, because the number people
/// watch here is under fifteen minutes and never has an hour in it.
String _clock(int seconds) {
  final String rest = (seconds % 60).toString().padLeft(2, '0');
  return '${seconds ~/ 60}:$rest';
}

/// How long ago, in words. Falls back to a date once "ago" stops being useful.
String _ago(DateTime when) {
  final Duration since = DateTime.now().difference(when);
  if (since.isNegative || since.inSeconds < 45) {
    return 'just now';
  }
  if (since.inMinutes < 60) {
    return '${since.inMinutes} min ago';
  }
  if (since.inHours < 24) {
    return '${since.inHours} hr ago';
  }
  if (since.inDays < 7) {
    return '${since.inDays} d ago';
  }
  return DateFormat('d MMM, HH:mm').format(when);
}
