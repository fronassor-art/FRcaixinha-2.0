import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/main.dart';
import 'package:frcaixinha/app.dart';
import 'package:provider/provider.dart';

void main() {
  testWidgets('exibe tela de login', (tester) async {
    final state = AppState()..initialized = true;
    await tester.pumpWidget(
      ChangeNotifierProvider.value(
        value: state,
        child: const FRcaixinhaApp(),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('FRcaixinha'), findsOneWidget);
    expect(find.text('Entrar'), findsOneWidget);
  });
}
