import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:tallyflow/src/features/connectors/domain/connector.dart';

/// Reading the code a Tally PC shows.
///
/// The camera is pointed at a shop counter, so this parser sees far more than
/// our codes: barcodes on stock, a QR on a payment sticker, a URL somebody
/// printed. Everything that is not exactly our payload has to come back null
/// and be ignored, because the alternative is an error toast for every packet
/// of biscuits behind the monitor -- which buries the one message that matters.
void main() {
  String payload({Object? version = 1, Object? code = 'the-code', String host = 'api.test'}) =>
      jsonEncode(<String, Object?>{'v': version, 'c': code, 'h': host});

  test('a code from a connector is read', () {
    final PairingCode? parsed = PairingCode.tryParse(payload());

    expect(parsed, isNotNull);
    expect(parsed!.code, 'the-code');
    expect(parsed.host, 'api.test');
  });

  test('anything that is not JSON is ignored', () {
    for (final String raw in <String>[
      'https://tallyflow.in',
      '8901234567890',
      '',
      'WIFI:S:ShopWifi;T:WPA;P:hunter2;;',
    ]) {
      expect(PairingCode.tryParse(raw), isNull, reason: raw);
    }
  });

  test('JSON that is not one of ours is ignored', () {
    expect(PairingCode.tryParse('{"order":"1234"}'), isNull);
    expect(PairingCode.tryParse('[1,2,3]'), isNull);
    expect(PairingCode.tryParse('"a string"'), isNull);
  });

  test('a future payload version is refused rather than guessed at', () {
    // A newer connector may show a code this build cannot complete. Sending it
    // anyway produces a server-side refusal nobody standing in the shop can act
    // on; refusing here is what lets the app say something useful instead.
    expect(PairingCode.tryParse(payload(version: 2)), isNull);
    expect(PairingCode.tryParse(payload(version: '1')), isNull);
  });

  test('a payload with no code in it is not a code', () {
    expect(PairingCode.tryParse(payload(code: '')), isNull);
    expect(PairingCode.tryParse(payload(code: null)), isNull);
    expect(PairingCode.tryParse(payload(code: 42)), isNull);
  });

  test('a code with no host still pairs', () {
    // The host is only there so the app can explain a mismatch. Losing it
    // costs a better error message, and must not cost the pairing.
    final PairingCode? parsed =
        PairingCode.tryParse(jsonEncode(<String, Object?>{'v': 1, 'c': 'the-code'}));

    expect(parsed?.code, 'the-code');
    expect(parsed?.host, '');
  });
}
