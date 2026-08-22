import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../../app/theme.dart';
import '../../../core/providers.dart';

/// The chosen skin, remembered across launches.
///
/// Stored locally rather than on the account, and deliberately so: a theme is a
/// property of the device it is being read on, not of the business. The same
/// owner may want the light skin on the tablet at the counter and the dark one
/// on the phone they check in bed, and syncing the choice would make one of
/// those two devices wrong every time.
///
/// Written synchronously into the notifier and persisted in the background, so
/// tapping a segment repaints immediately instead of after a disk write.
class ThemeController extends Notifier<AppThemeMode> {
  static const String _key = 'theme_mode';

  @override
  AppThemeMode build() {
    final SharedPreferences prefs = ref.watch(sharedPreferencesProvider);
    // An unreadable or absent value falls back to the default rather than
    // throwing: a corrupt preference must not stop the app from starting.
    return AppThemeMode.fromName(prefs.getString(_key));
  }

  Future<void> select(AppThemeMode mode) async {
    if (mode == state) return;
    state = mode;
    await ref.read(sharedPreferencesProvider).setString(_key, mode.name);
  }
}

final NotifierProvider<ThemeController, AppThemeMode> themeModeProvider =
    NotifierProvider<ThemeController, AppThemeMode>(ThemeController.new);
