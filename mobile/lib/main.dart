import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final state = AppState();
  await state.initialize();
  runApp(ChangeNotifierProvider.value(value: state, child: const FRcaixinhaApp()));
}

class FRcaixinhaApp extends StatelessWidget {
  const FRcaixinhaApp({super.key});
  @override
  Widget build(BuildContext context) {
    final state = context.read<AppState>();
    return MaterialApp.router(
      title: 'FRcaixinha',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(useMaterial3: true, brightness: Brightness.light),
      routerConfig: createRouter(state),
    );
  }
}
