import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';

import '../app.dart';
import '../services/api_client.dart';
import '../theme/frcaixinha_theme.dart';
import '../widgets/fr_brand_mark.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final email = TextEditingController();
  final password = TextEditingController();

  bool loading = false;
  String? error;

  Future<void> submit() async {
    setState(() => loading = true);

    try {
      final api = ApiClient();

      final r = await api.post('/auth/login', {
        'email': email.text.trim(),
        'password': password.text,
      });

      await context.read<AppState>().login(r['access_token'] as String);

      if (mounted) {
        context.go('/');
      }
    } catch (e) {
      if (mounted) {
        setState(() => error = e.toString());
      }
    }

    if (mounted) {
      setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Center(child: FRBrandMark(size: 88)),
                  const SizedBox(height: 22),
                  Text(
                    'FRcaixinha',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineLarge,
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Seu espaço para acompanhar a caixinha.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: FRColors.muted),
                  ),
                  const SizedBox(height: 40),
                  Text(
                    'Entrar na sua conta',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 20),
                  TextField(
                    controller: email,
                    keyboardType: TextInputType.emailAddress,
                    autofillHints: const [AutofillHints.email],
                    decoration: const InputDecoration(
                      labelText: 'E-mail',
                      prefixIcon: Icon(Icons.mail_outline),
                    ),
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    controller: password,
                    obscureText: true,
                    autofillHints: const [AutofillHints.password],
                    decoration: const InputDecoration(
                      labelText: 'Senha',
                      prefixIcon: Icon(Icons.lock_outline),
                    ),
                  ),
                  if (error != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 14),
                      child: Text(
                        error!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                  const SizedBox(height: 24),
                  FilledButton(
                    onPressed: loading ? null : submit,
                    child: Text(loading ? 'Entrando...' : 'Entrar'),
                  ),
                  const SizedBox(height: 8),
                  TextButton(
                    onPressed: loading ? null : () => context.go('/register'),
                    child: const Text('Criar conta'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  @override
  void dispose() {
    email.dispose();
    password.dispose();
    super.dispose();
  }
}
