/// Saved valuations: what you kept, and what has changed under it.
///
/// A save holds assumptions, not figures. Opening one recomputes from
/// today's market data, so the list shows the figure AT THE TIME it was
/// saved and the screen it opens shows today's — which is the comparison
/// the whole feature exists for.
///
/// THE BADGE IS THE POINT
/// ----------------------
/// A save written under an older model carries a visible mark, and
/// opening it says what moved. The model has already changed shape twice
/// in ways that invalidate a stored assumption set: a save made the week
/// before 1.0.41 refers to three fields that no longer exist. Opening it
/// silently would move the figure and let the screen imply the reader
/// chose that; refusing would lose work they did nothing to invalidate.
///
/// Renaming deliberately does NOT stamp the current version onto the
/// save. Renaming is not accepting a new shape, and only the reader
/// saving again is.
library;

import 'package:flutter/material.dart';

import 'formatting.dart';
import 'loading_states.dart';
import 'saved_valuation.dart';
import 'theme.dart';
import 'user_data_store.dart';

class SavesScreen extends StatelessWidget {
  const SavesScreen({
    super.key,
    required this.store,
    this.onOpen,
  });

  final UserDataStore store;

  /// Hands back the save's ticker and its migrated assumptions, plus what
  /// the migration had to do, so the valuation screen can show both the
  /// figure and the labels.
  final void Function(SavedRef ref, LoadedSave loaded)? onOpen;

  @override
  Widget build(BuildContext context) {
    return LoadingStream<List<SavedRef>>(
      stream: store.saves,
      what: 'your saved valuations',
      builder: (context, saves) {
        if (saves.isEmpty) return _empty(context);

        return ListView.separated(
          padding: const EdgeInsets.all(AppSpacing.xl),
          itemCount: saves.length,
          separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
          itemBuilder: (context, i) => _SaveTile(
            key: Key('save-${saves[i].id}'),
            save: saves[i],
            store: store,
            onOpen: onOpen,
          ),
        );
      },
    );
  }

  Widget _empty(BuildContext context) {
    final colors = context.colors;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.save_outlined, size: 40, color: colors.neutral),
            const SizedBox(height: AppSpacing.lg),
            Text('Nothing saved yet',
                key: const Key('saves-empty'),
                style: context.text.titleMedium
                    ?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Change some assumptions on a valuation, then press Save. '
              'Your assumptions are kept — the figure is recomputed each '
              'time you open it.',
              textAlign: TextAlign.center,
              style: context.text.bodySmall
                  ?.copyWith(color: colors.textSecondary, height: 1.45),
            ),
          ],
        ),
      ),
    );
  }
}

class _SaveTile extends StatelessWidget {
  const _SaveTile({
    super.key,
    required this.save,
    required this.store,
    this.onOpen,
  });

  final SavedRef save;
  final UserDataStore store;
  final void Function(SavedRef, LoadedSave)? onOpen;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Material(
      color: colors.accentSurface.withValues(alpha: 0.35),
      borderRadius: BorderRadius.circular(AppRadius.control),
      child: InkWell(
        borderRadius: BorderRadius.circular(AppRadius.control),
        onTap: () => _open(context),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(save.name,
                        style: context.text.titleSmall
                            ?.copyWith(fontWeight: FontWeight.w700)),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  if (save.savedValue != null)
                    Text(
                      '\$${fixed(save.savedValue!, 2)}',
                      style: context.text.titleSmall
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                ],
              ),
              const SizedBox(height: 4),
              Row(
                children: [
                  Text(save.ticker,
                      style: context.text.labelSmall
                          ?.copyWith(color: colors.textSecondary)),
                  if (save.savedValue != null) ...[
                    Text('  ·  ',
                        style: context.text.labelSmall
                            ?.copyWith(color: colors.textSecondary)),
                    Text('the figure when you saved it',
                        style: context.text.labelSmall
                            ?.copyWith(color: colors.textSecondary)),
                  ],
                  const Spacer(),
                  _menu(context),
                ],
              ),
              if (save.fromAnOlderModel || save.fromANewerBuild) ...[
                const SizedBox(height: AppSpacing.sm),
                _badge(context),
              ],
            ],
          ),
        ),
      ),
    );
  }

  /// Visible before it is opened, so a reader is not surprised by the
  /// explanation after the fact.
  Widget _badge(BuildContext context) {
    final colors = context.colors;
    final newer = save.fromANewerBuild;
    return Container(
      key: Key('save-badge-${save.id}'),
      padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md, vertical: 5),
      decoration: BoxDecoration(
        color: newer ? colors.negativeSurface : colors.cautionSurface,
        borderRadius: BorderRadius.circular(AppRadius.chip),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(newer ? Icons.lock_outline_rounded : Icons.history_rounded,
              size: 13, color: newer ? colors.negative : colors.caution),
          const SizedBox(width: 6),
          Text(
            newer
                ? 'Saved in a newer version of the app'
                : 'Saved under an older model (v${save.schemaVersion})',
            style: context.text.labelSmall?.copyWith(
                color: newer ? colors.negative : colors.caution,
                fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }

  Widget _menu(BuildContext context) => PopupMenuButton<String>(
        key: Key('save-menu-${save.id}'),
        padding: EdgeInsets.zero,
        icon: const Icon(Icons.more_horiz_rounded, size: 18),
        tooltip: 'Rename or delete',
        onSelected: (choice) {
          if (choice == 'rename') _rename(context);
          if (choice == 'delete') _delete(context);
        },
        itemBuilder: (_) => const [
          PopupMenuItem(value: 'rename', child: Text('Rename')),
          PopupMenuItem(value: 'delete', child: Text('Delete')),
        ],
      );

  Future<void> _open(BuildContext context) async {
    final loaded = await store.openSave(save.id);
    if (!context.mounted) return;

    if (loaded.notes.isNotEmpty) {
      await showDialog<void>(
        context: context,
        builder: (_) => _WhatChangedDialog(save: save, loaded: loaded),
      );
      if (!context.mounted) return;
    }
    if (loaded.readOnly) return;
    onOpen?.call(save, loaded);
  }

  Future<void> _rename(BuildContext context) async {
    final name = await showDialog<String>(
      context: context,
      builder: (_) => _RenameDialog(current: save.name),
    );
    if (name == null || name.isEmpty) return;
    await store.renameSave(save.id, name);
  }

  Future<void> _delete(BuildContext context) async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Delete “${save.name}”?'),
        content: const Text(
            'The assumptions you saved go with it. The company itself is '
            'unaffected, and you can value it again at any time.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const Key('save-delete-confirm'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (sure == true) await store.deleteSave(save.id);
  }
}

/// What the migration had to do, before the figure is shown.
///
/// Listed rather than summarised: "some assumptions have changed" tells a
/// reader nothing they can check, and the whole point is that they can.
class _WhatChangedDialog extends StatelessWidget {
  const _WhatChangedDialog({required this.save, required this.loaded});

  final SavedRef save;
  final LoadedSave loaded;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return AlertDialog(
      key: const Key('what-changed'),
      title: Text(loaded.readOnly
          ? 'Saved in a newer version'
          : 'Saved under an older model'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!loaded.readOnly)
              Text(
                'You saved this as v${loaded.savedUnder}; the model is now '
                'v$kSchemaVersion. Your assumptions have been carried over '
                'where they still mean the same thing. These did not:',
                style: context.text.bodySmall
                    ?.copyWith(color: colors.textSecondary, height: 1.45),
              ),
            const SizedBox(height: AppSpacing.lg),
            for (final note in loaded.notes) ...[
              Text(note.field,
                  style: context.text.labelMedium
                      ?.copyWith(fontWeight: FontWeight.w700)),
              const SizedBox(height: 2),
              Text(note.detail,
                  style: context.text.bodySmall
                      ?.copyWith(color: colors.textSecondary, height: 1.45)),
              const SizedBox(height: AppSpacing.md),
            ],
            if (!loaded.readOnly)
              Text(
                'Nothing has been changed on the save itself. Save it again '
                'if you want to keep it in the new shape.',
                style: context.text.labelSmall
                    ?.copyWith(color: colors.textSecondary, height: 1.4),
              ),
          ],
        ),
      ),
      actions: [
        FilledButton(
          key: const Key('what-changed-ok'),
          onPressed: () => Navigator.of(context).pop(),
          child: Text(loaded.readOnly ? 'Close' : 'Open it'),
        ),
      ],
    );
  }
}

class _RenameDialog extends StatefulWidget {
  const _RenameDialog({required this.current});

  final String current;

  @override
  State<_RenameDialog> createState() => _RenameDialogState();
}

class _RenameDialogState extends State<_RenameDialog> {
  late final TextEditingController _controller =
      TextEditingController(text: widget.current);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Rename'),
      content: TextField(
        key: const Key('rename-field'),
        controller: _controller,
        autofocus: true,
        maxLength: 120,
        decoration: const InputDecoration(labelText: 'Name'),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('rename-confirm'),
          onPressed: () =>
              Navigator.of(context).pop(_controller.text.trim()),
          child: const Text('Rename'),
        ),
      ],
    );
  }
}
