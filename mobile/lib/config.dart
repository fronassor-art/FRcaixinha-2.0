class AppConfig {
  // Em produção, substituir pela URL HTTPS do backend.
  static const apiBaseUrl = String.fromEnvironment(
    'API_URL',
    defaultValue: 'http://127.0.0.1:8000/api',
  );
}
