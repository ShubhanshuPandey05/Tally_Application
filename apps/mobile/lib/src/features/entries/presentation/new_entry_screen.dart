import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/layout/adaptive.dart';
import '../../../core/network/api_exception.dart';
import '../../companies/application/company_providers.dart';
import '../application/entry_providers.dart';
import '../domain/entry_draft.dart';

/// Recording something that just happened, from the counter.
///
/// The screen is built around one fact worth stating plainly: **an entry made
/// here does not change the books until somebody approves it in TallyPrime.**
/// That is said before the form rather than after the save, because somebody
/// who takes ₹5,000 over a counter needs to know what this button does before
/// they press it, not afterwards.
class NewEntryScreen extends ConsumerStatefulWidget {
  const NewEntryScreen({super.key, this.initialKind = EntryKind.receipt});

  final EntryKind initialKind;

  @override
  ConsumerState<NewEntryScreen> createState() => _NewEntryScreenState();
}

class _NewEntryScreenState extends ConsumerState<NewEntryScreen> {
  final GlobalKey<FormState> _form = GlobalKey<FormState>();
  final TextEditingController _party = TextEditingController();
  final TextEditingController _amount = TextEditingController();
  final TextEditingController _account = TextEditingController();
  final TextEditingController _narration = TextEditingController();
  final TextEditingController _reference = TextEditingController();

  /// Chosen before this screen opened, from the button that launched it.
  late final EntryKind _kind = widget.initialKind;
  DateTime _date = DateTime.now();
  final List<EntryLine> _lines = <EntryLine>[];
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _account.text = _kind.defaultAccount;
  }

  @override
  void dispose() {
    _party.dispose();
    _amount.dispose();
    _account.dispose();
    _narration.dispose();
    _reference.dispose();
    super.dispose();
  }

  /// On a kind with lines the total is the lines, not something typed.
  double get _total {
    if (_kind.takesLines && _lines.isNotEmpty) {
      return _lines.fold<double>(0, (double sum, EntryLine l) => sum + l.amount);
    }
    return double.tryParse(_amount.text.trim()) ?? 0;
  }

  Future<void> _addLine() async {
    final String? companyId = ref.read(activeCompanyIdResolvedProvider);
    final List<String> items =
        ref.read(itemNamesProvider(companyId ?? '')).valueOrNull ??
            const <String>[];

    final EntryLine? line = await showModalBottomSheet<EntryLine>(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext context) => _LineSheet(items: items),
    );
    if (line != null) {
      setState(() => _lines.add(line));
    }
  }

  Future<void> _pickDate() async {
    final DateTime now = DateTime.now();
    final DateTime? picked = await showDatePicker(
      context: context,
      initialDate: _date,
      // No future entries. A voucher dated next week is almost always a typo,
      // and it lands in a period nobody is looking at.
      firstDate: DateTime(now.year - 1),
      lastDate: now,
    );
    if (picked != null) {
      setState(() => _date = picked);
    }
  }

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) {
      return;
    }
    if (_kind.needsLines && _lines.isEmpty) {
      _say('Add at least one item to the order.');
      return;
    }
    final String? companyId = ref.read(activeCompanyIdResolvedProvider);
    if (companyId == null) {
      _say('Choose a company first.');
      return;
    }

    setState(() => _saving = true);
    try {
      final EntryResult result =
          await ref.read(entriesRepositoryProvider).create(
                companyId,
                EntryDraft(
                  kind: _kind,
                  date: _date,
                  party: _party.text.trim(),
                  amount: _total,
                  account: _kind.hasAccount ? _account.text.trim() : null,
                  narration: _narration.text.trim(),
                  reference: _reference.text.trim(),
                  lines: _lines,
                ),
              );
      if (!mounted) {
        return;
      }
      if (result.ok || result.queued) {
        // Queued counts as "leave the form", because the entry is held and
        // re-typing it would create a second one. What it does *not* count as
        // is saved -- the screen that receives this says which happened.
        Navigator.of(context).pop(result);
        return;
      }
      // Something the entry named is not in TallyPrime yet. Offer to add it,
      // then send the entry again -- which is safe, because a refused voucher
      // is one Tally definitely did not write.
      if (result.canCreateMissing) {
        await _offerToCreate(companyId, result);
        return;
      }

      // Whatever the server said, verbatim. "Ledger 'Ram Traders' does not
      // exist" is something the person can act on; a generic failure is not.
      //
      // The retry is offered only when the server could prove nothing was
      // written -- the PC offline, TallyPrime closed, the company not open.
      // After a timeout there is no button here at all, because the entry may
      // already exist and the message tells them to go and look instead.
      _say(
        result.message ?? 'That entry was not saved.',
        retry: result.canRetry ? _save : null,
      );
    } on ApiException catch (error) {
      if (mounted) {
        _say(error.message);
      }
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
  }

  /// Ask before adding anything to somebody's books.
  ///
  /// The confirmation is the feature. Creating a ledger automatically for every
  /// unrecognised name is how a chart of accounts ends up holding "Ram
  /// Traders", "Ram traders" and "Ram Trader", with one customer's outstanding
  /// split across all three and no way afterwards to say which is which.
  Future<void> _offerToCreate(String companyId, EntryResult result) async {
    final String name = result.missingName!;
    final bool isParty = result.missingKind == 'ledger';

    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        title: Text('Add $name?'),
        content: Text(
          isParty
              ? 'There is no customer called "$name" in TallyPrime. Add them '
                  'and save this entry?\n\nCheck the spelling first - a '
                  'second, slightly different name is hard to merge later.'
              : 'There is no item called "$name" in TallyPrime. Add it and '
                  'save this entry?\n\nCheck the spelling first.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(isParty ? 'Add customer' : 'Add item'),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) {
      return;
    }

    setState(() => _saving = true);
    try {
      final String? problem = isParty
          ? await ref.read(entriesRepositoryProvider).createLedger(companyId, name)
          : await ref
              .read(entriesRepositoryProvider)
              .createStockItem(companyId, name);
      if (!mounted) {
        return;
      }
      if (problem != null) {
        _say(problem);
        return;
      }
    } on ApiException catch (error) {
      if (mounted) {
        _say(error.message);
      }
      return;
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }

    // Safe to send again: a voucher Tally refused is one it definitely did not
    // write, so this cannot produce a second copy.
    await _save();
  }

  void _say(String message, {VoidCallback? retry}) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(message),
          // Long enough to read a sentence about somebody's money, and longer
          // still when there is a decision attached to it.
          duration: Duration(seconds: retry == null ? 6 : 10),
          action: retry == null
              ? null
              : SnackBarAction(label: 'Try again', onPressed: retry),
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final String? companyId = ref.watch(activeCompanyIdResolvedProvider);

    return Scaffold(
      appBar: AppBar(title: Text(_kind.label)),
      body: ContentPane(
        child: Form(
          key: _form,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
            children: <Widget>[
              Text(
                _kind.blurb,
                style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
              ),
              const SizedBox(height: 16),
              _NamePicker(
                controller: _party,
                label: _kind.partyLabel,
                // Suggestions come from the party master, but the field is
                // never restricted to them: a customer added in TallyPrime two
                // minutes ago will not be in the snapshot yet, and refusing to
                // let somebody type the name would be worse than a stale list.
                options: ref
                    .watch(partyNamesProvider(companyId ?? ''))
                    .valueOrNull ??
                    const <String>[],
                validator: _required,
              ),
              const SizedBox(height: 12),
              if (!_kind.takesLines || _lines.isEmpty)
                TextFormField(
                  controller: _amount,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  inputFormatters: <TextInputFormatter>[
                    FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                  ],
                  decoration: const InputDecoration(
                    labelText: 'Amount',
                    prefixText: '₹ ',
                  ),
                  onChanged: (_) => setState(() {}),
                  validator: _positiveAmount,
                ),
              if (_kind.takesLines) ...<Widget>[
                const SizedBox(height: 4),
                _Lines(
                  lines: _lines,
                  onAdd: _addLine,
                  onRemove: (int i) => setState(() => _lines.removeAt(i)),
                ),
              ],
              if (_kind.hasAccount) ...<Widget>[
                const SizedBox(height: 12),
                TextFormField(
                  controller: _account,
                  textCapitalization: TextCapitalization.words,
                  decoration: InputDecoration(labelText: _kind.accountLabel),
                  validator: _required,
                ),
              ],
              const SizedBox(height: 12),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Date'),
                subtitle: Text(_pretty(_date)),
                trailing: const Icon(Icons.calendar_today, size: 18),
                onTap: _pickDate,
              ),
              const Divider(),
              TextFormField(
                controller: _reference,
                decoration: const InputDecoration(labelText: 'Reference (optional)'),
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _narration,
                maxLines: 2,
                textCapitalization: TextCapitalization.sentences,
                decoration: const InputDecoration(labelText: 'Narration (optional)'),
              ),
              const SizedBox(height: 20),
              _ApprovalNote(scheme: scheme),
            ],
          ),
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
          child: FilledButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(
                    height: 16,
                    width: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : Text('Send to TallyPrime  ·  ₹${_total.toStringAsFixed(2)}'),
          ),
        ),
      ),
    );
  }
}

String? _required(String? value) =>
    (value == null || value.trim().isEmpty) ? 'Required' : null;

String? _positiveAmount(String? value) {
  final double? parsed = double.tryParse((value ?? '').trim());
  if (parsed == null) {
    return 'Enter an amount';
  }
  if (parsed <= 0) {
    return 'Must be more than zero';
  }
  return null;
}

String _pretty(DateTime date) =>
    '${date.day.toString().padLeft(2, '0')}/'
    '${date.month.toString().padLeft(2, '0')}/${date.year}';

class _Lines extends StatelessWidget {
  const _Lines({
    required this.lines,
    required this.onAdd,
    required this.onRemove,
  });

  final List<EntryLine> lines;
  final VoidCallback onAdd;
  final ValueChanged<int> onRemove;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < lines.length; i++)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            title: Text(lines[i].item),
            subtitle: Text(
              '${lines[i].quantity} ${lines[i].unit ?? ''} × '
              '₹${lines[i].rate.toStringAsFixed(2)}',
              style: TextStyle(fontSize: 11.5, color: scheme.onSurfaceVariant),
            ),
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text('₹${lines[i].amount.toStringAsFixed(2)}'),
                IconButton(
                  icon: const Icon(Icons.close, size: 16),
                  onPressed: () => onRemove(i),
                ),
              ],
            ),
          ),
        TextButton.icon(
          onPressed: onAdd,
          icon: const Icon(Icons.add, size: 16),
          label: const Text('Add item'),
        ),
      ],
    );
  }
}

/// Said before the save, not after it.
class _ApprovalNote extends StatelessWidget {
  const _ApprovalNote({required this.scheme});

  final ColorScheme scheme;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(
        'This is saved into TallyPrime as an optional entry. It shows in the '
        'Day Book straight away but changes no balance, no stock and no report '
        'until somebody approves it in TallyPrime.',
        style: TextStyle(
          fontSize: 11.5,
          height: 1.4,
          color: scheme.onSurfaceVariant,
        ),
      ),
    );
  }
}

/// One stock line. A sheet rather than inline fields, so the main form stays
/// short on a phone held in one hand at a counter.
class _LineSheet extends StatefulWidget {
  const _LineSheet({required this.items});

  final List<String> items;

  @override
  State<_LineSheet> createState() => _LineSheetState();
}

class _LineSheetState extends State<_LineSheet> {
  final TextEditingController _item = TextEditingController();
  final TextEditingController _quantity = TextEditingController();
  final TextEditingController _rate = TextEditingController();
  final TextEditingController _unit = TextEditingController();

  @override
  void dispose() {
    _item.dispose();
    _quantity.dispose();
    _rate.dispose();
    _unit.dispose();
    super.dispose();
  }

  void _done() {
    final double? quantity = double.tryParse(_quantity.text.trim());
    final double? rate = double.tryParse(_rate.text.trim());
    if (_item.text.trim().isEmpty || quantity == null || rate == null) {
      return;
    }
    if (quantity <= 0 || rate < 0) {
      return;
    }
    Navigator.of(context).pop(
      EntryLine(
        item: _item.text.trim(),
        quantity: quantity,
        rate: rate,
        unit: _unit.text.trim().isEmpty ? null : _unit.text.trim(),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        16,
        16,
        16,
        MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          _NamePicker(
            controller: _item,
            label: 'Item',
            options: widget.items,
            autofocus: true,
          ),
          const SizedBox(height: 12),
          Row(
            children: <Widget>[
              Expanded(
                child: TextField(
                  controller: _quantity,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(labelText: 'Quantity'),
                ),
              ),
              const SizedBox(width: 10),
              SizedBox(
                width: 90,
                child: TextField(
                  controller: _unit,
                  decoration: const InputDecoration(labelText: 'Unit'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: TextField(
                  controller: _rate,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(
                    labelText: 'Rate',
                    prefixText: '₹ ',
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          FilledButton(onPressed: _done, child: const Text('Add')),
        ],
      ),
    );
  }
}


/// A name field that suggests what is already in TallyPrime.
///
/// Suggestions, not a restriction. The list comes from a snapshot that may be
/// an hour old, so a customer added in TallyPrime five minutes ago would be
/// missing from it -- and a picker that refused to let somebody type the name
/// would turn a stale cache into a shop that cannot record a sale.
class _NamePicker extends StatelessWidget {
  const _NamePicker({
    required this.controller,
    required this.label,
    required this.options,
    this.validator,
    this.autofocus = false,
  });

  final TextEditingController controller;
  final String label;
  final List<String> options;
  final String? Function(String?)? validator;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    return RawAutocomplete<String>(
      textEditingController: controller,
      focusNode: FocusNode(),
      optionsBuilder: (TextEditingValue value) {
        final String typed = value.text.trim().toLowerCase();
        if (typed.isEmpty) {
          // Everything, capped. An empty field that offers nothing looks
          // broken; an empty field that offers two thousand rows is worse.
          return options.take(8);
        }
        return options
            .where((String o) => o.toLowerCase().contains(typed))
            .take(8);
      },
      fieldViewBuilder: (
        BuildContext context,
        TextEditingController fieldController,
        FocusNode node,
        VoidCallback onSubmit,
      ) {
        return TextFormField(
          controller: fieldController,
          focusNode: node,
          autofocus: autofocus,
          textCapitalization: TextCapitalization.words,
          decoration: InputDecoration(
            labelText: label,
            helperText: options.isEmpty
                ? 'Type the name exactly as it is in TallyPrime'
                : 'Pick one, or type a new name',
          ),
          validator: validator,
        );
      },
      optionsViewBuilder: (
        BuildContext context,
        void Function(String) onSelected,
        Iterable<String> found,
      ) {
        return Align(
          alignment: Alignment.topLeft,
          child: Material(
            elevation: 3,
            borderRadius: BorderRadius.circular(10),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 260, maxWidth: 420),
              child: ListView.builder(
                shrinkWrap: true,
                padding: EdgeInsets.zero,
                itemCount: found.length,
                itemBuilder: (BuildContext context, int i) {
                  final String option = found.elementAt(i);
                  return ListTile(
                    dense: true,
                    title: Text(option, style: const TextStyle(fontSize: 13)),
                    onTap: () => onSelected(option),
                  );
                },
              ),
            ),
          ),
        );
      },
    );
  }
}
