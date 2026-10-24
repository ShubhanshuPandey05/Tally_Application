/// A first look at a typed mobile number, before the server decides.
///
/// Mirrors `services.trial.normalise_phone` closely enough to catch a typo
/// while the form is still open. The server's answer is the one that counts --
/// it normalises the number, checks nobody else has it, and says so in a
/// sentence -- so this errs towards letting a number through.
library;

final RegExp _indianMobile = RegExp(r'^[6-9]\d{9}$');
final RegExp _international = RegExp(r'^\+[1-9]\d{7,14}$');

/// Null when [raw] reads as a mobile number, otherwise what to put under the
/// box.
String? validatePhone(String? raw) {
  String compact = (raw ?? '').replaceAll(RegExp(r'[\s\-().]'), '');
  if (compact.isEmpty) return 'Enter your mobile number';
  if (compact.startsWith('00')) compact = '+${compact.substring(2)}';

  final bool ok;
  if (compact.startsWith('+91')) {
    ok = _indianMobile.hasMatch(compact.substring(3));
  } else if (compact.startsWith('+')) {
    ok = _international.hasMatch(compact);
  } else if (compact.startsWith('0')) {
    ok = _indianMobile.hasMatch(compact.substring(1));
  } else if (compact.length == 12 && compact.startsWith('91')) {
    ok = _indianMobile.hasMatch(compact.substring(2));
  } else {
    ok = _indianMobile.hasMatch(compact);
  }
  return ok ? null : 'Enter a valid mobile number, like 98765 43210';
}
