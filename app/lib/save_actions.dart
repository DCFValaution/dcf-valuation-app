/// The two things a reader can keep, and the one gate in front of them.
///
/// Both buttons call [requireAccount] first. That is the entire account
/// system from the app's point of view: nothing else in Intrinsic asks who
/// you are, and a test asserts it by driving every other path and finding
/// no sheet.
///
/// Declining costs nothing — the sheet closes and the figure is still on
/// screen — so neither button warns, nags, or explains itself before it is
/// pressed.
library;

import 'package:flutter/material.dart';

import 'account_service.dart';
import 'sign_in_sheet.dart';
import 'theme.dart';
import 'user_data_store.dart';

/// Builds the store for a signed-in account. Injected so tests can hand
/// back a fake without Firebase.
typedef StoreFor = UserDataStore Function(Account account);

class SaveActions extends StatelessWidget {
  const SaveActions({
    super.key,
    required this.ticker,
    required this.companyName,
    required this.assumptions,
    required this.value,
    required this.accounts,
    required this.storeFor,
    this.busy = false,
  });

  final String ticker;
  final String? companyName;

  /// Only what the reader changed, which is what a save is for. The
  /// derived model is recomputed on open, so storing it would be storing
  /// today's answer to a question that will be asked again.
  final Map<String, double> assumptions;

  /// Kept beside the save for the comparison only.
  final double? value;

  final AccountService? accounts;
  final StoreFor storeFor;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: OutlinedButton.icon(
            key: const Key('add-to-watchlist'),
            onPressed: busy ? null : () => _addToWatchlist(context),
            icon: const Icon(Icons.bookmark_add_outlined, size: 18),
            label: const Text('Watch'),
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(48),
              side: BorderSide(color: context.colors.hairline),
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: OutlinedButton.icon(
            key: const Key('save-valuation'),
            onPressed: busy ? null : () => _save(context),
            icon: const Icon(Icons.save_outlined, size: 18),
            label: const Text('Save'),
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(48),
              side: BorderSide(color: context.colors.hairline),
            ),
          ),
        ),
      ],
    );
  }

  Future<void> _addToWatchlist(BuildContext context) async {
    final account = await requireAccount(context, accounts,
        reason: 'Add $ticker to your watchlist');
    if (account == null || !context.mounted) return;
    await storeFor(account).addTicker(ticker);
    if (context.mounted) _say(context, '$ticker is on your watchlist.');
  }

  Future<void> _save(BuildContext context) async {
    final account = await requireAccount(context, accounts,
        reason: 'Save this valuation');
    if (account == null || !context.mounted) return;

    final name = await _askName(context);
    if (name == null || !context.mounted) return;

    await storeFor(account).putSave(
      name: name,
      ticker: ticker,
      assumptions: assumptions,
      savedValue: value,
    );
    if (context.mounted) _say(context, 'Saved as “$name”.');
  }

  /// A name, suggested rather than demanded.
  ///
  /// The suggestion names the company and how many assumptions were
  /// changed, because "NVDA" on its own is useless the moment there are
  /// two of them.
  Future<String?> _askName(BuildContext context) {
    final changed = assumptions.length;
    final suggestion = changed == 0
        ? '$ticker — derived model'
        : '$ticker — $changed '
            '${changed == 1 ? 'assumption' : 'assumptions'} changed';

    return showDialog<String>(
      context: context,
      builder: (_) => _SaveNameDialog(suggestion: suggestion),
    );
  }

  static void _say(BuildContext context, String message) {
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }
}

/// The name dialog, owning its controller.
///
/// A controller disposed right after `await showDialog` is disposed while
/// the dialog is still animating out, and the field rebuilds once more on
/// the way. Owning it here and disposing in [dispose] is the only version
/// that does not throw on the exit frame.
class _SaveNameDialog extends StatefulWidget {
  const _SaveNameDialog({required this.suggestion});

  final String suggestion;

  @override
  State<_SaveNameDialog> createState() => _SaveNameDialogState();
}

class _SaveNameDialogState extends State<_SaveNameDialog> {
  late final TextEditingController _controller =
      TextEditingController(text: widget.suggestion);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Save this valuation'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            key: const Key('save-name-field'),
            controller: _controller,
            autofocus: true,
            maxLength: 120,
            decoration: const InputDecoration(labelText: 'Name'),
          ),
          Text(
            'Your assumptions are saved, not the figures. Reopening '
            'recomputes from today’s market data, so you see both what you '
            'assumed and what has moved.',
            style: context.text.labelSmall
                ?.copyWith(color: context.colors.textSecondary, height: 1.4),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('save-confirm'),
          onPressed: () {
            final text = _controller.text.trim();
            Navigator.of(context)
                .pop(text.isEmpty ? widget.suggestion : text);
          },
          child: const Text('Save'),
        ),
      ],
    );
  }
}
