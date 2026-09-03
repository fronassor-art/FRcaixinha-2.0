class AppConfig {
  // Em produção, substituir pela URL HTTPS do backend.
  static const apiBaseUrl = String.fromEnvironment(
    'API_URL',
    defaultValue: 'http://10.0.2.2:8000/api',
  );
}
