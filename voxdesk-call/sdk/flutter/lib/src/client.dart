import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// Base class for every error the client raises.
class VoxDeskException implements Exception {
  VoxDeskException(this.code, this.message, {this.statusCode = 0});

  /// The server's error code (`review_missing`, `captcha_failed`, ...) or a
  /// client-side code such as `network_error`.
  final String code;
  final String message;
  final int statusCode;

  @override
  String toString() => 'VoxDeskException($code, $statusCode): $message';
}

/// Result of a paged list call.
class VoxDeskPage<T> {
  const VoxDeskPage({required this.items, this.nextCursor});

  final List<T> items;
  final String? nextCursor;

  bool get hasMore => nextCursor != null && nextCursor!.isNotEmpty;
}

/// An agent as returned by `GET /api/agents`.
class Agent {
  const Agent({required this.id, required this.name, required this.status});

  final String id;
  final String name;
  final String status;

  factory Agent.fromJson(Map<String, dynamic> json) => Agent(
        id: (json['id'] ?? json['agent_id'] ?? '').toString(),
        name: (json['name'] ?? '').toString(),
        status: (json['status'] ?? 'unknown').toString(),
      );
}

/// A redeemed share link's session, as returned by the public endpoint.
class ShareSession {
  const ShareSession({
    required this.sessionToken,
    required this.agentId,
    required this.maxSeconds,
    required this.expiresAtUnix,
  });

  final String sessionToken;
  final String agentId;
  final int maxSeconds;
  final int expiresAtUnix;

  bool get isExpired =>
      DateTime.now().millisecondsSinceEpoch >= expiresAtUnix * 1000;

  factory ShareSession.fromJson(Map<String, dynamic> json) => ShareSession(
        sessionToken: (json['session_token'] ?? '').toString(),
        agentId: (json['agent_id'] ?? '').toString(),
        maxSeconds: (json['max_seconds'] ?? 0) as int,
        expiresAtUnix: (json['expires_at_unix'] ?? 0) as int,
      );
}

/// A workflow-library integration.
class Integration {
  const Integration({
    required this.key,
    required this.name,
    required this.auth,
    required this.available,
    this.reason,
  });

  final String key;
  final String name;
  final String auth;
  final bool available;
  final String? reason;

  factory Integration.fromJson(Map<String, dynamic> json) => Integration(
        key: (json['key'] ?? '').toString(),
        name: (json['name'] ?? '').toString(),
        auth: (json['auth'] ?? '').toString(),
        available: json['available'] == true,
        reason: json['reason']?.toString(),
      );
}

/// The API client.
///
/// ```dart
/// final client = VoxDeskClient(apiKey: 'vd_...');
/// final agents = await client.listAgents();
/// ```
class VoxDeskClient {
  VoxDeskClient({
    required this.apiKey,
    this.baseUrl = 'https://api.voxdesk.example',
    http.Client? httpClient,
    this.timeout = const Duration(seconds: 20),
  }) : _http = httpClient ?? http.Client();

  final String apiKey;
  final String baseUrl;
  final Duration timeout;
  final http.Client _http;

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = baseUrl.endsWith('/')
        ? baseUrl.substring(0, baseUrl.length - 1)
        : baseUrl;
    final parsed = Uri.parse('$base$path');
    return query == null ? parsed : parsed.replace(queryParameters: query);
  }

  Map<String, String> get _headers => {
        'X-API-Key': apiKey,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };

  Future<dynamic> _send(
    String method,
    Uri uri, {
    Map<String, dynamic>? body,
    Map<String, String>? headers,
  }) async {
    final request = http.Request(method, uri)
      ..headers.addAll({..._headers, ...?headers});
    if (body != null) {
      request.body = jsonEncode(body);
    }
    http.Response response;
    try {
      final streamed = await _http.send(request).timeout(timeout);
      response = await http.Response.fromStream(streamed);
    } on TimeoutException {
      throw VoxDeskException('timeout', 'the request to $uri timed out');
    } catch (error) {
      throw VoxDeskException('network_error', error.toString());
    }
    final payload = response.body.isEmpty
        ? null
        : (jsonDecode(response.body) as dynamic);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw _error(payload, response.statusCode);
    }
    return payload;
  }

  VoxDeskException _error(dynamic payload, int status) {
    if (payload is Map) {
      final detail = payload['detail'];
      if (detail is Map) {
        return VoxDeskException(
          (detail['code'] ?? 'http_$status').toString(),
          (detail['message'] ?? 'request failed').toString(),
          statusCode: status,
        );
      }
      if (detail is String) {
        return VoxDeskException('http_$status', detail, statusCode: status);
      }
    }
    return VoxDeskException('http_$status', 'request failed', statusCode: status);
  }

  /// `GET /api/agents`
  Future<VoxDeskPage<Agent>> listAgents({String? cursor}) async {
    final payload = await _send(
      'GET',
      _uri('/api/agents', cursor == null ? null : {'cursor': cursor}),
    );
    final rows = payload is Map ? payload['agents'] : payload;
    final items = (rows is List ? rows : const [])
        .whereType<Map>()
        .map((row) => Agent.fromJson(row.cast<String, dynamic>()))
        .toList(growable: false);
    final next = payload is Map ? payload['next_cursor']?.toString() : null;
    return VoxDeskPage(items: items, nextCursor: next);
  }

  /// `GET /api/calls/{call_id}`
  Future<Map<String, dynamic>> getCall(String callId) async {
    final payload = await _send('GET', _uri('/api/calls/$callId'));
    return (payload as Map).cast<String, dynamic>();
  }

  /// `GET /api/workflow-library/catalog`
  Future<List<Integration>> workflowCatalog({bool onlyAvailable = false}) async {
    final payload = await _send(
      'GET',
      _uri('/api/workflow-library/catalog',
          onlyAvailable ? {'only_available': 'true'} : null),
    );
    final rows = payload is Map ? payload['integrations'] : payload;
    return (rows is List ? rows : const [])
        .whereType<Map>()
        .map((row) => Integration.fromJson(row.cast<String, dynamic>()))
        .toList(growable: false);
  }

  /// `POST /api/v1/public/share/{token}/redeem`
  ///
  /// Public: no API key is needed, but the redeem consumes one use of the link,
  /// so call it exactly once per visitor.
  Future<ShareSession> redeemShare(
    String token, {
    String? passcode,
  }) async {
    final payload = await _send(
      'POST',
      _uri('/api/v1/public/share/$token/redeem'),
      body: {if (passcode != null) 'passcode': passcode},
      headers: const {'X-VoxDesk-Public': '1'},
    );
    return ShareSession.fromJson((payload as Map).cast<String, dynamic>());
  }

  /// Closes the underlying HTTP client.
  void close() => _http.close();
}
