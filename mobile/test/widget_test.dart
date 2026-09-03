import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/main.dart';

void main() {
  testWidgets('exibe tela de login', (tester) async {
    await tester.pumpWidget(const FRcaixinhaApp());
    expect(find.text('FRcaixinha'), findsOneWidget);
    expect(find.text('Entrar'), findsOneWidget);
  });
}
