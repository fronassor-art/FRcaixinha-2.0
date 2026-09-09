import 'dart:convert';
import 'package:http/http.dart' as http;
import '../config.dart';

class ApiClient {
  String? token;

  Map<String, String> get headers => {
    'Content-Type': 'application/json',
    if (token != null) 'Authorization': 'Bearer $token',
  };

  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body) async {
    final response = await http.post(
      Uri.parse('${AppConfig.apiBaseUrl}$path'),
      headers: headers,
      body: jsonEncode(body),
    );
    return _decode(response);
  }

  Future<Map<String, dynamic>> put(String path, Map<String, dynamic> body) async {
    final response = await http.put(Uri.parse('${AppConfig.apiBaseUrl}$path'), headers: headers, body: jsonEncode(body));
    return _decode(response);
  }

  Future<Map<String, dynamic>> postEmpty(String path) async {
    final response = await http.post(Uri.parse('${AppConfig.apiBaseUrl}$path'), headers: headers);
    return _decode(response);
  }

  Future<Map<String, dynamic>> get(String path) async {
    final response = await http.get(
      Uri.parse('${AppConfig.apiBaseUrl}$path'),
      headers: headers,
    );
    return _decode(response);
  }

  Map<String, dynamic> _decode(http.Response response) {
    dynamic decoded;
    try {
      decoded = jsonDecode(response.body);
    } catch (_) {
      decoded = {'detail': response.body};
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw Exception(decoded is Map ? (decoded['detail'] ?? 'Erro na API') : 'Erro na API');
    }
    return Map<String, dynamic>.from(decoded as Map);
  }
}
