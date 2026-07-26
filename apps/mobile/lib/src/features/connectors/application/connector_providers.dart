import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers.dart';
import '../data/connector_repository.dart';
import '../domain/connector.dart';

final Provider<ConnectorRepository> connectorRepositoryProvider =
    Provider<ConnectorRepository>(
  (Ref ref) => ConnectorRepository(ref.watch(apiClientProvider)),
);

final FutureProvider<List<Connector>> connectorsProvider =
    FutureProvider<List<Connector>>(
  (Ref ref) => ref.watch(connectorRepositoryProvider).list(),
);

/// Live-ish status for a single connector.
///
/// Polled rather than pushed, and only while the pairing screen is open: the
/// wait between "I installed it" and "it says Connected" is the one moment a
/// user genuinely stares at a status dot, and a five-second poll there is far
/// cheaper than a WebSocket the app would otherwise hold open all day.
final AutoDisposeStreamProviderFamily<Connector, String> connectorStatusProvider =
    StreamProvider.autoDispose.family<Connector, String>((Ref ref, String id) async* {
  final ConnectorRepository repository = ref.watch(connectorRepositoryProvider);
  while (true) {
    yield await repository.detail(id);
    await Future<void>.delayed(const Duration(seconds: 5));
  }
});

/// Companies the connector reports as currently open in TallyPrime.
final AutoDisposeFutureProviderFamily<List<String>, String> openCompaniesProvider =
    FutureProvider.autoDispose.family<List<String>, String>((Ref ref, String id) async {
  final Connector connector = await ref.watch(connectorRepositoryProvider).detail(id);
  return connector.companiesOpen;
});
