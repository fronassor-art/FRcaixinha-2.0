import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../services/api_client.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final name = TextEditingController();
  final email = TextEditingController();
  final cpf = TextEditingController();
  final phone = TextEditingController();
  final password = TextEditingController();
  final confirmPassword = TextEditingController();

  bool acceptTerms = false;
  bool loading = false;
  String? error;

  Future<void> submit() async {
    setState(() => error = null);

    if (name.text.trim().length < 2) {
      setState(() => error = 'Informe seu nome completo.');
      return;
    }

    if (cpf.text.replaceAll(RegExp(r'\D'), '').length != 11) {
      setState(() => error = 'Informe um CPF válido.');
      return;
    }

    if (password.text.length < 10) {
      setState(() => error = 'A senha deve ter pelo menos 10 caracteres.');
      return;
    }

    if (password.text != confirmPassword.text) {
      setState(() => error = 'As senhas não conferem.');
      return;
    }

    if (!acceptTerms) {
      setState(() => error = 'É necessário aceitar os termos.');
      return;
    }

    setState(() => loading = true);

    try {
      final api = ApiClient();

      await api.post('/auth/register', {
        'name': name.text.trim(),
        'email': email.text.trim(),
        'cpf': cpf.text.replaceAll(RegExp(r'\D'), ''),
        'phone': phone.text.trim().isEmpty ? null : phone.text.trim(),
        'password': password.text,
        'accept_terms': true,
      });

      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Conta criada com sucesso! Faça login para continuar.'),
        ),
      );

      context.go('/login');
    } catch (e) {
      if (mounted) {
        setState(() => error = e.toString());
      }
    } finally {
      if (mounted) {
        setState(() => loading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Criar conta')),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Cadastre-se na FRcaixinha',
                  style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 8),
                const Text('Preencha seus dados para criar sua conta.'),
                const SizedBox(height: 24),
                TextField(
                  controller: name,
                  textCapitalization: TextCapitalization.words,
                  decoration: const InputDecoration(
                    labelText: 'Nome completo',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: email,
                  keyboardType: TextInputType.emailAddress,
                  decoration: const InputDecoration(
                    labelText: 'E-mail',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: cpf,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'CPF',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: phone,
                  keyboardType: TextInputType.phone,
                  decoration: const InputDecoration(
                    labelText: 'Telefone (opcional)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: password,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'Senha',
                    helperText: 'Mínimo de 10 caracteres',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: confirmPassword,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'Confirme sua senha',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                CheckboxListTile(
                  value: acceptTerms,
                  onChanged:
                      loading
                          ? null
                          : (value) {
                            setState(() => acceptTerms = value ?? false);
                          },
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'Aceito os termos e condições da FRcaixinha.',
                  ),
                  controlAffinity: ListTileControlAffinity.leading,
                ),
                if (error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      error!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: loading ? null : submit,
                  child: Text(loading ? 'Criando conta...' : 'Criar conta'),
                ),
                const SizedBox(height: 8),
                TextButton(
                  onPressed: loading ? null : () => context.go('/login'),
                  child: const Text('Já tenho uma conta'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  void dispose() {
    name.dispose();
    email.dispose();
    cpf.dispose();
    phone.dispose();
    password.dispose();
    confirmPassword.dispose();
    super.dispose();
  }
}
