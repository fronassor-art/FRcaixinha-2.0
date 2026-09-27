import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frcaixinha/services/session.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  const tokenKey = 'access_token';
  const secureStorage = FlutterSecureStorage();

  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    SharedPreferences.setMockInitialValues({});
  });

  test('saveToken stores the token securely and getToken reads it', () async {
    await Session.saveToken('secure-token');

    expect(await secureStorage.read(key: tokenKey), 'secure-token');
    expect(await Session.getToken(), 'secure-token');
  });

  test(
    'getToken migrates a legacy token and removes it from preferences',
    () async {
      SharedPreferences.setMockInitialValues({tokenKey: 'legacy-token'});

      expect(await Session.getToken(), 'legacy-token');
      expect(await secureStorage.read(key: tokenKey), 'legacy-token');
      expect(
        (await SharedPreferences.getInstance()).getString(tokenKey),
        isNull,
      );
    },
  );

  test('saveToken removes any legacy token', () async {
    SharedPreferences.setMockInitialValues({tokenKey: 'old-token'});

    await Session.saveToken('new-token');

    expect(await secureStorage.read(key: tokenKey), 'new-token');
    expect((await SharedPreferences.getInstance()).getString(tokenKey), isNull);
  });

  test('clear removes the secure token and any legacy token', () async {
    FlutterSecureStorage.setMockInitialValues({tokenKey: 'secure-token'});
    SharedPreferences.setMockInitialValues({tokenKey: 'legacy-token'});

    await Session.clear();

    expect(await secureStorage.read(key: tokenKey), isNull);
    expect((await SharedPreferences.getInstance()).getString(tokenKey), isNull);
  });

  test('secure token takes precedence over a legacy token', () async {
    FlutterSecureStorage.setMockInitialValues({tokenKey: 'secure-token'});
    SharedPreferences.setMockInitialValues({tokenKey: 'legacy-token'});

    expect(await Session.getToken(), 'secure-token');
    expect(await secureStorage.read(key: tokenKey), 'secure-token');
    expect((await SharedPreferences.getInstance()).getString(tokenKey), isNull);
  });
}
