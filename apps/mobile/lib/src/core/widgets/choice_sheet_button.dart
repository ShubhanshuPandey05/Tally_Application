import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// One option in a [ChoiceSheetButton].
class SheetChoice<T> {
  const SheetChoice(this.value, this.label);

  final T value;
  final String label;
}

/// One button naming the filter in force, opening a sheet of every option.
///
/// Used instead of a row of chips once there are more options than fit: a
/// strip that scrolls sideways hides the choice being looked for off its edge,
/// and nothing on screen says it is there.
class ChoiceSheetButton<T> extends StatelessWidget {
  const ChoiceSheetButton({
    super.key,
    required this.choices,
    required this.selected,
    required this.onSelected,
  });

  final List<SheetChoice<T>> choices;
  final T selected;
  final ValueChanged<T> onSelected;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String label = choices
        .firstWhere(
          (SheetChoice<T> choice) => choice.value == selected,
          orElse: () => choices.first,
        )
        .label;

    return ActionChip(
      avatar: Icon(Icons.filter_list, size: 17, color: theme.colorScheme.primary),
      label: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Flexible(child: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis)),
          const SizedBox(width: 2),
          Icon(Icons.arrow_drop_down, size: 18, color: context.mutedColor),
        ],
      ),
      onPressed: () async {
        // The whole choice comes back, not its value: a null value is a real
        // option here ("all"), and must not read as the sheet being dismissed.
        final SheetChoice<T>? choice = await showModalBottomSheet<SheetChoice<T>>(
          context: context,
          showDragHandle: true,
          isScrollControlled: true,
          builder: (BuildContext sheetContext) => ConstrainedBox(
            constraints: BoxConstraints(
              maxHeight: MediaQuery.sizeOf(sheetContext).height * 0.7,
            ),
            child: ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.only(bottom: 16),
              children: <Widget>[
                for (final SheetChoice<T> option in choices)
                  ListTile(
                    dense: true,
                    title: Text(option.label, style: theme.textTheme.bodyMedium),
                    trailing: option.value == selected
                        ? Icon(Icons.check, size: 18, color: theme.colorScheme.primary)
                        : null,
                    onTap: () => Navigator.of(sheetContext).pop(option),
                  ),
              ],
            ),
          ),
        );
        if (choice != null) onSelected(choice.value);
      },
    );
  }
}
