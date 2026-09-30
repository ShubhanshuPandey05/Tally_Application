import 'package:flutter/foundation.dart';

/// Whether this phone could reach the TallyFlow server on its last attempt.
///
/// A different question from `Freshness.connectorOnline`, which is about the
/// shop's PC as seen *by the server*. When the phone itself has no signal the
/// server cannot say anything, and a figure shown from the phone's own copy
/// must be captioned as that -- not as "your Tally PC is offline", which would
/// send somebody off to check a machine that is fine.
///
/// Observed from traffic that already happens, never polled: every response
/// sets it, every connection failure clears it.
class Reachability extends ChangeNotifier {
  bool _online = true;

  bool get online => _online;

  void reached() => _set(true);

  void unreachable() => _set(false);

  void _set(bool value) {
    if (_online == value) return;
    _online = value;
    notifyListeners();
  }
}
