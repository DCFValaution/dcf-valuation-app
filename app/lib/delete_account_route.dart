/// The deletion page, wired to an account service.
///
/// Kept apart from [DeleteAccountPage] so that the page stays stateless
/// and every one of its states is reachable in a test without arranging an
/// account, a network or a provider.
library;

import 'package:flutter/material.dart';

import 'account_service.dart';
import 'delete_account_page.dart';

class DeleteAccountRoute extends StatefulWidget {
  const DeleteAccountRoute({super.key, this.service, this.onFinished});

  /// Injected by tests. Null means the app builds its own, which until the
  /// Firebase configuration lands is the fake - so the page renders and
  /// routes correctly while the provider work is still outstanding.
  final AccountService? service;

  /// Where "Back to Intrinsic" goes. Null leaves the button inert, which
  /// is correct on the web page: it is its own destination.
  final VoidCallback? onFinished;

  @override
  State<DeleteAccountRoute> createState() => _DeleteAccountRouteState();
}

class _DeleteAccountRouteState extends State<DeleteAccountRoute> {
  late final AccountService _service;
  late final bool _ownsService;

  DeletionStage _stage = DeletionStage.signedOut;
  String? _error;

  @override
  void initState() {
    super.initState();
    _ownsService = widget.service == null;
    _service = widget.service ?? FakeAccountService();
    _syncToAccount();
    _service.changes.listen((_) {
      if (mounted && _stage != DeletionStage.done) _syncToAccount();
    });
  }

  void _syncToAccount() {
    setState(() {
      // An anonymous installation has nothing to delete here and must
      // see the signed-out stage, not a page offering to delete an
      // account it does not have.
      _stage = _service.signedIn
          ? DeletionStage.ready
          : DeletionStage.signedOut;
    });
  }

  @override
  void dispose() {
    if (_ownsService) _service.dispose();
    super.dispose();
  }

  Future<void> _signIn() async {
    try {
      await _service.signIn();
    } catch (e) {
      // Everything, for the same reason as the sign-in sheet: a handler
      // that catches only AccountError lets anything else escape, and on
      // this page the person is left looking at a button that did nothing.
      //
      // A dismissed window is not a failure and must not be reported as
      // one: they simply changed their mind, and the page is already
      // showing what to do next.
      if (e is AccountError && e.message == cancelled) return;
      if (!mounted) return;
      setState(() => _error = e is AccountError
          ? e.message
          : 'Signing in did not finish. Please try again.');
    }
  }

  Future<void> _delete() async {
    setState(() {
      _stage = DeletionStage.working;
      _error = null;
    });
    try {
      await _service.deleteAccount();
      if (mounted) setState(() => _stage = DeletionStage.done);
    } catch (e) {
      // The message is shown as thrown. The service states what is and
      // is not gone, because it is the only thing that knows: documents
      // are deleted before the auth user, so a failure at the second step
      // leaves the data already removed. A person who believes their data
      // is gone when it is not - or still there when it is not - has been
      // misled about the one thing this page exists to do.
      if (mounted) {
        setState(() {
          _stage = DeletionStage.failed;
          _error = e is AccountError ? e.message : '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return DeleteAccountPage(
      stage: _stage,
      email: _service.current?.email,
      error: _error,
      onSignIn: _signIn,
      onDelete: _delete,
      onBackToApp: widget.onFinished,
    );
  }
}
