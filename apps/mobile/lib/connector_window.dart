/// The connector's window: a second entrypoint into this project, built for
/// Windows only.
///
/// It shares the phone app's package rather than living in one of its own, for
/// one reason that decided it: the visual language is a single file
/// (`src/app/theme.dart`) that thirty-odd screens already import, and a shop
/// owner sees the window and the app in the same afternoon. Splitting the theme
/// into a shared package to keep them in step would be a sweep across every one
/// of those imports to gain what `--target` gives for nothing.
///
/// Nothing here reaches the phone app's code beyond the theme and a couple of
/// primitives, and nothing in the phone app imports this. `flutter build
/// windows --target=lib/connector_window.dart` produces the window;
/// `lib/main.dart` still produces the app.
library;

import 'package:flutter/material.dart';

import 'src/app/theme.dart';
import 'src/connector/window.dart';

/// The connector's own default. Duplicated from `config.py` rather than read
/// from `connector.json`, because this process must draw something sensible on
/// a machine where that file is unreadable -- and because the shortcut passes
/// the real one anyway.
const int _defaultPort = 9787;

void main(List<String> arguments) {
  runApp(_ConnectorApp(port: _portFrom(arguments)));
}

/// Reads `--port=<n>`, handed over by `tally-connector ui`.
///
/// Passed on the command line rather than read from the connector's settings
/// file: the window is installed beside the connector but has no business
/// parsing its configuration, and the one number it needs is known by the
/// command that launches it.
int _portFrom(List<String> arguments) {
  for (final String argument in arguments) {
    if (argument.startsWith('--port=')) {
      return int.tryParse(argument.substring(7)) ?? _defaultPort;
    }
  }
  return _defaultPort;
}

class _ConnectorApp extends StatelessWidget {
  const _ConnectorApp({required this.port});

  final int port;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'TallyFlow Connector',
      debugShowCheckedModeBanner: false,
      // Light, and not the app's dim default. This window is read at a counter
      // under shop lighting rather than on a phone in the evening, and the
      // pairing code it exists to show has to sit on white in any case.
      theme: AppTheme.light(),
      home: ConnectorWindow(port: port),
    );
  }
}
