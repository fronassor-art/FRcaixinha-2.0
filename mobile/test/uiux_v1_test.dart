import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/screens/home_screen.dart';
import 'package:frcaixinha/screens/login_screen.dart';
import 'package:frcaixinha/theme/frcaixinha_theme.dart';
import 'package:frcaixinha/widgets/fr_brand_mark.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('login mantém campos e ações em tela estreita', (tester) async {
    await tester.binding.setSurfaceSize(const Size(320, 640));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(theme: FRTheme.dark, home: const LoginScreen()),
    );

    expect(find.byType(FRBrandMark), findsOneWidget);
    expect(find.text('E-mail'), findsOneWidget);
    expect(find.text('Senha'), findsOneWidget);
    expect(find.text('Entrar'), findsOneWidget);
    expect(find.text('Criar conta'), findsOneWidget);
    expect(
      Theme.of(tester.element(find.byType(LoginScreen))).brightness,
      Brightness.dark,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('home mantém os acessos no menu sem inventar saldos', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(theme: FRTheme.dark, home: const HomeScreen()),
    );

    expect(find.text('Empréstimos'), findsOneWidget);
    expect(find.textContaining('R\$'), findsNothing);

    await tester.tap(find.text('Menu'));
    await tester.pump();

    expect(find.text('Todos os acessos'), findsOneWidget);
    expect(find.text('Acessos rápidos'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
