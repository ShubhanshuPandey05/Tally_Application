import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme.dart';
import '../../../core/layout/adaptive.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/widgets/primitives.dart';
import '../../companies/domain/company.dart';
import '../../reports/domain/report_catalog.dart';
import '../../top_reports/domain/top_report.dart';
import '../application/team_providers.dart';
import '../domain/access_policy.dart';
import '../domain/team_member.dart';

/// What one colleague may see inside one company, as a tree of switches.
///
/// Three trees, each switched on as a whole and refinable underneath:
/// Reports (the Reports screen and the Top Prime Reports), Ledger Groups (down
/// to single ledgers) and Stock Groups (down to single items). A switch that is
/// on can be tapped to open the level below it; one that is off hides
/// everything under it, so there is nothing below to choose.
///
/// Everything starts on. The admin switches off only what this person should
/// not see, and the server stores only those exceptions.
///
/// Every level edits one shared draft, saved from the top. Saving per level
/// would leave the policy half-applied whenever somebody backed out midway.
class MemberAccessScreen extends ConsumerStatefulWidget {
  const MemberAccessScreen({super.key, required this.member, required this.company});

  final TeamMember member;
  final Company company;

  @override
  ConsumerState<MemberAccessScreen> createState() => _MemberAccessScreenState();
}

class _MemberAccessScreenState extends ConsumerState<MemberAccessScreen> {
  final ValueNotifier<AccessPolicy> _draft = ValueNotifier<AccessPolicy>(AccessPolicy.all);
  AccessPolicy _saved = AccessPolicy.all;
  AccessCatalog? _catalog;
  Object? _error;
  bool _loading = true;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _draft.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final repository = ref.read(teamRepositoryProvider);
      final List<Object> results = await Future.wait<Object>(<Future<Object>>[
        repository.memberAccess(widget.member.id, widget.company.id),
        repository.accessCatalog(widget.company.id),
      ]);
      if (!mounted) return;
      setState(() {
        _saved = results[0] as AccessPolicy;
        _draft.value = _saved;
        _catalog = results[1] as AccessCatalog;
        _loading = false;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  bool get _dirty => _draft.value.toJson().toString() != _saved.toJson().toString();

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final AccessPolicy stored = await ref
          .read(teamRepositoryProvider)
          .setMemberAccess(widget.member.id, widget.company.id, _draft.value);
      _saved = stored;
      _draft.value = stored;
      // The team list shows who is limited; it must not wait for a restart.
      ref.invalidate(teamMembersProvider);
      if (!mounted) return;
      Navigator.of(context).pop(stored);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _saving = false);
      _toast(context, error.message, isError: true);
    }
  }

  Future<bool> _confirmDiscard() async {
    final bool? discard = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Discard changes?'),
        content: const Text('What you switched here has not been saved.'),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep editing'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Discard'),
          ),
        ],
      ),
    );
    return discard ?? false;
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<AccessPolicy>(
      valueListenable: _draft,
      builder: (BuildContext context, AccessPolicy policy, _) {
        final bool dirty = !_loading && _dirty;
        return PopScope(
          canPop: !dirty,
          onPopInvokedWithResult: (bool didPop, Object? result) async {
            if (didPop) return;
            if (await _confirmDiscard() && context.mounted) {
              _draft.value = _saved;
              Navigator.of(context).pop();
            }
          },
          child: Scaffold(
            appBar: AppBar(
              title: Text(widget.company.name, overflow: TextOverflow.ellipsis),
              actions: <Widget>[
                if (!_loading && _error == null)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: FilledButton(
                      // The theme's buttons fill the width, which an app bar
                      // action cannot give them.
                      style: FilledButton.styleFrom(minimumSize: const Size(72, 40)),
                      onPressed: dirty && !_saving ? _save : null,
                      child: Text(_saving ? 'Saving…' : 'Save'),
                    ),
                  ),
              ],
            ),
            body: ContentPane(child: _body(context, policy)),
          ),
        );
      },
    );
  }

  Widget _body(BuildContext context, AccessPolicy policy) {
    final ThemeData theme = Theme.of(context);
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                _error is ApiException
                    ? (_error! as ApiException).message
                    : 'Could not load what they can see.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              OutlinedButton(onPressed: _load, child: const Text('Try again')),
            ],
          ),
        ),
      );
    }
    final AccessCatalog catalog = _catalog!;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 0, 4, 14),
          child: Text(
            'What ${widget.member.displayName} can see in this company. Everything '
            'is on until you switch it off. Tap a switched-on row to choose inside it.',
            style: theme.textTheme.bodyMedium?.copyWith(color: context.mutedColor),
          ),
        ),
        Card(
          clipBehavior: Clip.antiAlias,
          child: Column(
            children: <Widget>[
              ToggleRow(
                icon: Icons.summarize_outlined,
                tint: AppTheme.tileViolet,
                title: 'Reports',
                subtitle:
                    policy.reports ? _countLabel(_reportsOff(policy), 'report') : 'No reports',
                value: policy.reports,
                onChanged: (bool on) => _draft.value = policy.copyWith(reports: on),
                onOpen: () => _push(context, _ReportsMasterScreen(draft: _draft)),
              ),
              const Divider(indent: 62, height: 1),
              ToggleRow(
                icon: Icons.account_tree_outlined,
                tint: AppTheme.tileBlue,
                title: 'Ledger Groups',
                subtitle: policy.ledgers
                    ? _countLabel(
                        policy.deniedLedgerGroups.length + policy.deniedLedgers.length,
                        'group or ledger',
                        plural: 'groups or ledgers',
                      )
                    : 'No ledgers, and no entries that use one',
                value: policy.ledgers,
                onChanged: (bool on) => _draft.value = policy.copyWith(ledgers: on),
                onOpen: catalog.ledgerGroups.isEmpty && catalog.ledgers.isEmpty
                    ? null
                    : () => _push(
                          context,
                          LedgerTreeScreen(draft: _draft, catalog: catalog, group: null),
                        ),
              ),
              const Divider(indent: 62, height: 1),
              ToggleRow(
                icon: Icons.inventory_2_outlined,
                tint: AppTheme.tileAmber,
                title: 'Stock Groups',
                subtitle: policy.stock
                    ? _countLabel(
                        policy.deniedStockGroups.length + policy.deniedStockItems.length,
                        'group or item',
                        plural: 'groups or items',
                      )
                    : 'No stock items, and no entries that use one',
                value: policy.stock,
                onChanged: (bool on) => _draft.value = policy.copyWith(stock: on),
                onOpen: catalog.stockGroups.isEmpty && catalog.stockItems.isEmpty
                    ? null
                    : () => _push(
                          context,
                          StockTreeScreen(draft: _draft, catalog: catalog, group: null),
                        ),
              ),
            ],
          ),
        ),
        if (!catalog.mastersRead)
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 12, 4, 0),
            child: Text(
              'Ledgers and stock for this company have not been read from Tally yet, '
              'so only whole sections can be switched for now.',
              style: theme.textTheme.bodySmall?.copyWith(color: context.cautionColor),
            ),
          ),
        const SizedBox(height: 14),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4),
          child: Text(
            'An entry that uses anything hidden is hidden whole, and totals count '
            'only what they can see. Admins always see everything.',
            style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
          ),
        ),
        if (!policy.isUnrestricted) ...<Widget>[
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () => _draft.value = AccessPolicy.all,
              icon: const Icon(Icons.lock_open_outlined, size: 18),
              label: const Text('Allow everything again'),
            ),
          ),
        ],
      ],
    );
  }

  static int _reportsOff(AccessPolicy policy) {
    int off = policy.deniedReports.length;
    if (!policy.standardReports) {
      off += reportCatalog.fold<int>(0, (int n, ReportSection s) => n + s.entries.length) -
          policy.deniedReports.where((String k) => !k.startsWith(AccessPolicy.primePrefix)).length;
    }
    if (!policy.primeReports) {
      off += _primeEntries().length -
          policy.deniedReports.where((String k) => k.startsWith(AccessPolicy.primePrefix)).length;
    }
    return off;
  }
}

String _countLabel(int off, String noun, {String? plural}) => switch (off) {
      0 => 'Everything',
      1 => '1 $noun switched off',
      _ => '$off ${plural ?? '${noun}s'} switched off',
    };

Iterable<TopReportEntry> _primeEntries() => topReportCatalog
    .expand((TopReportGroup g) => g.entries)
    .where((TopReportEntry e) => e.unavailable == null);

void _push(BuildContext context, Widget screen) {
  Navigator.of(context).push<void>(MaterialPageRoute<void>(builder: (_) => screen));
}

void _toast(BuildContext context, String message, {bool isError = false}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: isError ? Theme.of(context).colorScheme.error : null,
      ),
    );
}

/// Tally names compare case-insensitively; so do the policy's lists.
String _fold(String name) => name.trim().toLowerCase();

bool _holds(Set<String> names, String name) {
  final String wanted = _fold(name);
  return names.any((String n) => _fold(n) == wanted);
}

Set<String> _toggled(Set<String> names, String name, {required bool deny}) {
  final String wanted = _fold(name);
  final Set<String> next = names.where((String n) => _fold(n) != wanted).toSet();
  if (deny) next.add(name);
  return next;
}

// --------------------------------------------------------------------------
// One row: a switch, and — while it is on — a way into the level below
// --------------------------------------------------------------------------

/// The row in the admin's drawing: a name, a switch, and a chevron that opens
/// the next level while the switch is on.
class ToggleRow extends StatelessWidget {
  const ToggleRow({
    super.key,
    required this.title,
    required this.value,
    required this.onChanged,
    this.subtitle,
    this.icon,
    this.tint,
    this.onOpen,
  });

  final String title;
  final String? subtitle;
  final IconData? icon;
  final Color? tint;
  final bool value;
  final ValueChanged<bool>? onChanged;

  /// Opens the level below. Offered only while [value] is on: everything
  /// under a switched-off row is hidden, so there is nothing there to choose.
  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool canOpen = value && onOpen != null;
    return ListTile(
      onTap: canOpen ? onOpen : (onChanged == null ? null : () => onChanged!(!value)),
      leading: icon == null
          ? null
          : IconTile(icon: icon!, colour: tint ?? AppTheme.tileBlue, size: 34, quiet: !value),
      title: Text(
        title,
        overflow: TextOverflow.ellipsis,
        style: theme.textTheme.bodyLarge?.copyWith(
          fontWeight: FontWeight.w600,
          color: value ? null : context.mutedColor,
        ),
      ),
      subtitle: subtitle == null
          ? null
          : Text(
              subtitle!,
              style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
            ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Switch(value: value, onChanged: onChanged),
          SizedBox(
            width: 24,
            child: canOpen ? Icon(Icons.chevron_right, color: context.mutedColor) : null,
          ),
        ],
      ),
    );
  }
}

class _LevelScaffold extends StatelessWidget {
  const _LevelScaffold({required this.title, required this.children, this.note});

  final String title;
  final String? note;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(title, overflow: TextOverflow.ellipsis)),
      body: ContentPane(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          children: <Widget>[
            if (note != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 0, 4, 12),
                child: Text(
                  note!,
                  style: theme.textTheme.bodySmall?.copyWith(color: context.mutedColor),
                ),
              ),
            ...children,
          ],
        ),
      ),
    );
  }
}

Widget _card(List<Widget> rows) => Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        children: <Widget>[
          for (int i = 0; i < rows.length; i++) ...<Widget>[
            if (i > 0) const Divider(height: 1, indent: 16),
            rows[i],
          ],
        ],
      ),
    );

Widget _heading(BuildContext context, String text) => Padding(
      padding: const EdgeInsets.fromLTRB(4, 14, 0, 8),
      child: Text(
        text,
        style: Theme.of(context).textTheme.titleMedium?.copyWith(color: context.mutedColor),
      ),
    );

// --------------------------------------------------------------------------
// Reports
// --------------------------------------------------------------------------

class _ReportsMasterScreen extends StatelessWidget {
  const _ReportsMasterScreen({required this.draft});

  final ValueNotifier<AccessPolicy> draft;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<AccessPolicy>(
      valueListenable: draft,
      builder: (BuildContext context, AccessPolicy policy, _) => _LevelScaffold(
        title: 'Reports',
        children: <Widget>[
          _card(<Widget>[
            ToggleRow(
              icon: Icons.receipt_long_outlined,
              tint: AppTheme.tileBlue,
              title: 'Reports',
              subtitle: 'Outstanding, transactions and stock',
              value: policy.standardReports,
              onChanged: (bool on) => draft.value = policy.copyWith(standardReports: on),
              onOpen: () => _push(context, _ReportListScreen(draft: draft, prime: false)),
            ),
            ToggleRow(
              icon: Icons.leaderboard_outlined,
              tint: AppTheme.tileViolet,
              title: 'Prime Reports',
              subtitle: 'Top customers, products, profit and outstanding',
              value: policy.primeReports,
              onChanged: (bool on) => draft.value = policy.copyWith(primeReports: on),
              onOpen: () => _push(context, _ReportListScreen(draft: draft, prime: true)),
            ),
          ]),
        ],
      ),
    );
  }
}

class _ReportListScreen extends StatelessWidget {
  const _ReportListScreen({required this.draft, required this.prime});

  final ValueNotifier<AccessPolicy> draft;
  final bool prime;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<AccessPolicy>(
      valueListenable: draft,
      builder: (BuildContext context, AccessPolicy policy, _) {
        Widget row(String key, String title, IconData icon, Color tint) => ToggleRow(
              icon: icon,
              tint: tint,
              title: title,
              value: !policy.deniedReports.contains(key),
              onChanged: (bool on) {
                final Set<String> next = <String>{...policy.deniedReports};
                on ? next.remove(key) : next.add(key);
                draft.value = policy.copyWith(deniedReports: next);
              },
            );

        final List<Widget> children = <Widget>[];
        if (prime) {
          for (final TopReportGroup group in topReportCatalog) {
            final List<TopReportEntry> live =
                group.entries.where((TopReportEntry e) => e.unavailable == null).toList();
            if (live.isEmpty) continue;
            children
              ..add(_heading(context, group.heading))
              ..add(_card(<Widget>[
                for (final TopReportEntry e in live)
                  row('${AccessPolicy.primePrefix}${e.key}', e.title, e.icon, AppTheme.tileViolet),
              ]));
          }
        } else {
          for (final ReportSection section in reportCatalog) {
            children
              ..add(_heading(context, section.heading))
              ..add(_card(<Widget>[
                for (final ReportEntry e in section.entries)
                  row(e.key, e.title, e.icon, section.tint),
              ]));
          }
        }
        return _LevelScaffold(
          title: prime ? 'Prime Reports' : 'Reports',
          note: 'Switched-off reports are left out of their menus and refused if opened.',
          children: children,
        );
      },
    );
  }
}

// --------------------------------------------------------------------------
// Ledger groups and stock groups
// --------------------------------------------------------------------------

/// A level of a tree with a filter box once it is too long to scan.
class _TreeLevel extends StatefulWidget {
  const _TreeLevel({required this.title, required this.note, required this.rows});

  final String title;
  final String note;

  /// Name + the row for it, in display order.
  final List<(String, Widget)> rows;

  @override
  State<_TreeLevel> createState() => _TreeLevelState();
}

class _TreeLevelState extends State<_TreeLevel> {
  String _query = '';

  /// Below this a list is quicker to scan than to search.
  static const int _filterFrom = 12;

  @override
  Widget build(BuildContext context) {
    final String q = _fold(_query);
    final List<Widget> shown = <Widget>[
      for (final (String name, Widget row) in widget.rows)
        if (q.isEmpty || _fold(name).contains(q)) row,
    ];
    return _LevelScaffold(
      title: widget.title,
      note: widget.note,
      children: <Widget>[
        if (widget.rows.length >= _filterFrom)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TextField(
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: 'Find by name',
                isDense: true,
              ),
              onChanged: (String value) => setState(() => _query = value),
            ),
          ),
        if (shown.isEmpty)
          Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              widget.rows.isEmpty ? 'Nothing in here.' : 'Nothing matches.',
              textAlign: TextAlign.center,
              style: TextStyle(color: context.mutedColor),
            ),
          )
        else
          _card(shown),
      ],
    );
  }
}

/// One level of Tally's group tree: the sub-groups and ledgers under [group]
/// (or the top of the tree when [group] is null).
class LedgerTreeScreen extends StatelessWidget {
  const LedgerTreeScreen({
    super.key,
    required this.draft,
    required this.catalog,
    required this.group,
  });

  final ValueNotifier<AccessPolicy> draft;
  final AccessCatalog catalog;
  final String? group;

  @override
  Widget build(BuildContext context) {
    final Set<String> groupNames =
        catalog.ledgerGroups.map((AccessNode g) => _fold(g.name)).toSet();

    // The top of the tree is every group whose parent is not itself a group
    // in the read -- Tally reports its primary groups under "Primary".
    bool under(String? parent) => group == null
        ? parent == null || !groupNames.contains(_fold(parent))
        : parent != null && _fold(parent) == _fold(group!);

    final List<AccessNode> groups = catalog.ledgerGroups
        .where((AccessNode g) => under(g.parent))
        .toList()
      ..sort((AccessNode a, AccessNode b) => _fold(a.name).compareTo(_fold(b.name)));
    final List<AccessNode> ledgers = catalog.ledgers
        .where((AccessNode l) => under(l.parent))
        .toList()
      ..sort((AccessNode a, AccessNode b) => _fold(a.name).compareTo(_fold(b.name)));

    bool hasChildren(String name) =>
        catalog.ledgerGroups
            .any((AccessNode g) => g.parent != null && _fold(g.parent!) == _fold(name)) ||
        catalog.ledgers.any((AccessNode l) => l.parent != null && _fold(l.parent!) == _fold(name));

    return ValueListenableBuilder<AccessPolicy>(
      valueListenable: draft,
      builder: (BuildContext context, AccessPolicy policy, _) => _TreeLevel(
        title: group ?? 'Ledger Groups',
        note: 'Switching a group off hides every ledger inside it, at any depth, '
            'and every entry that uses one.',
        rows: <(String, Widget)>[
          for (final AccessNode g in groups)
            (
              g.name,
              ToggleRow(
                icon: Icons.folder_outlined,
                tint: AppTheme.tileBlue,
                title: g.name,
                subtitle: 'Group',
                value: !_holds(policy.deniedLedgerGroups, g.name),
                onChanged: (bool on) => draft.value = policy.copyWith(
                  deniedLedgerGroups: _toggled(policy.deniedLedgerGroups, g.name, deny: !on),
                ),
                onOpen: hasChildren(g.name)
                    ? () => _push(
                          context,
                          LedgerTreeScreen(draft: draft, catalog: catalog, group: g.name),
                        )
                    : null,
              ),
            ),
          for (final AccessNode l in ledgers)
            (
              l.name,
              ToggleRow(
                title: l.name,
                value: !_holds(policy.deniedLedgers, l.name),
                onChanged: (bool on) => draft.value = policy.copyWith(
                  deniedLedgers: _toggled(policy.deniedLedgers, l.name, deny: !on),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Stock groups, then the items in one. Tally nests stock groups too, but the
/// connector reads only each item's own group, so this is two levels deep.
class StockTreeScreen extends StatelessWidget {
  const StockTreeScreen({
    super.key,
    required this.draft,
    required this.catalog,
    required this.group,
  });

  final ValueNotifier<AccessPolicy> draft;
  final AccessCatalog catalog;
  final String? group;

  @override
  Widget build(BuildContext context) {
    final Set<String> groupNames = catalog.stockGroups.map((AccessNode g) => _fold(g.name)).toSet();
    final List<AccessNode> groups = group == null
        ? (catalog.stockGroups.toList()
          ..sort((AccessNode a, AccessNode b) => _fold(a.name).compareTo(_fold(b.name))))
        : const <AccessNode>[];
    final List<AccessNode> items = catalog.stockItems
        .where(
          (AccessNode i) => group == null
              ? i.parent == null || !groupNames.contains(_fold(i.parent!))
              : i.parent != null && _fold(i.parent!) == _fold(group!),
        )
        .toList()
      ..sort((AccessNode a, AccessNode b) => _fold(a.name).compareTo(_fold(b.name)));

    return ValueListenableBuilder<AccessPolicy>(
      valueListenable: draft,
      builder: (BuildContext context, AccessPolicy policy, _) => _TreeLevel(
        title: group ?? 'Stock Groups',
        note: 'Switching a group off hides every item in it, and every entry that uses one.',
        rows: <(String, Widget)>[
          for (final AccessNode g in groups)
            (
              g.name,
              ToggleRow(
                icon: Icons.folder_outlined,
                tint: AppTheme.tileAmber,
                title: g.name,
                subtitle: 'Stock group',
                value: !_holds(policy.deniedStockGroups, g.name),
                onChanged: (bool on) => draft.value = policy.copyWith(
                  deniedStockGroups: _toggled(policy.deniedStockGroups, g.name, deny: !on),
                ),
                onOpen: () => _push(
                  context,
                  StockTreeScreen(draft: draft, catalog: catalog, group: g.name),
                ),
              ),
            ),
          for (final AccessNode i in items)
            (
              i.name,
              ToggleRow(
                title: i.name,
                value: !_holds(policy.deniedStockItems, i.name),
                onChanged: (bool on) => draft.value = policy.copyWith(
                  deniedStockItems: _toggled(policy.deniedStockItems, i.name, deny: !on),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
