# FRcaixinha 2.0 — v0.26 Android release

## Release
- Application ID: `com.frcaixinha.app`.
- Min SDK 24 / Target SDK 35.
- Version `0.26.0+26`.
- Release build with R8/shrink resources.
- API URL supplied at build time with `--dart-define=API_URL=...`.

## Production build
A machine with Flutter SDK and Android SDK is required. The repository intentionally contains no private signing key.

```bash
flutter pub get
flutter build appbundle --release --dart-define=API_URL=https://api.seudominio.com/api
flutter build apk --release --dart-define=API_URL=https://api.seudominio.com/api
```

The Play Store prefers an Android App Bundle (AAB). Configure the upload key securely before producing the official artifact. citeturn0search1

## Signing
Do not commit `key.properties`, `.jks` or `.keystore`. Configure them only on the secure build machine/CI. The official Flutter release process requires signing for store publication. citeturn0search1

## Important
This development environment does not contain a Flutter SDK or the production keystore, so v0.26 distributes the reproducible Android source/build configuration rather than claiming a signed APK/AAB was generated here.
