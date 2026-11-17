import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:test/test.dart';
import 'package:voxdesk/voxdesk.dart';

http.Client stub(int status, Object body, {void Function(http.Request)? onRequest}) {
  return MockClient((request) async {
    onRequest?.call(request);
    return http.Response(
      body is String ? body : jsonEncode(body),
      status,
      headers: {'content-type': 'application/json'},
    );
  });
}

void main() {
  test('listAgents parses rows and sends the API key', () async {
    late http.Request seen;
    final client = VoxDeskClient(
      apiKey: 'vd_test',
      baseUrl: 'https://api.test',
      httpClient: stub(
        200,
        {
          'agents': [
            {'id': 'a1', 'name': 'Reception', 'status': 'published'}
          ]
        },
        onRequest: (request) => seen = request,
      ),
    );
    final page = await client.listAgents();
    expect(page.items.single.id, 'a1');
    expect(page.items.single.status, 'published');
    expect(seen.headers['X-API-Key'], 'vd_test');
    expect(seen.url.path, '/api/agents');
  });

  test('a 409 becomes a VoxDeskException carrying the server code', () async {
    final client = VoxDeskClient(
      apiKey: 'vd_test',
      httpClient: stub(409, {
        'detail': {'code': 'review_missing', 'message': 'not signed off'}
      }),
    );
    expect(
      () => client.getCall('c1'),
      throwsA(isA<VoxDeskException>()
          .having((e) => e.code, 'code', 'review_missing')
          .having((e) => e.statusCode, 'status', 409)),
    );
  });

  test('a plain string detail is still surfaced', () async {
    final client = VoxDeskClient(
      apiKey: 'vd_test',
      httpClient: stub(404, {'detail': 'no such call'}),
    );
    expect(
      () => client.getCall('c1'),
      throwsA(isA<VoxDeskException>().having((e) => e.message, 'message', 'no such call')),
    );
  });

  test('redeemShare posts the passcode and parses the session', () async {
    late http.Request seen;
    final client = VoxDeskClient(
      apiKey: '',
      baseUrl: 'https://api.test',
      httpClient: stub(
        200,
        {
          'session_token': 'abc.def',
          'agent_id': 'agent_1',
          'max_seconds': 300,
          'expires_at_unix': 4000000000,
        },
        onRequest: (request) => seen = request,
      ),
    );
    final session = await client.redeemShare('tok', passcode: 'secret1');
    expect(session.agentId, 'agent_1');
    expect(session.isExpired, isFalse);
    expect(jsonDecode(seen.body)['passcode'], 'secret1');
    expect(seen.url.path, '/api/v1/public/share/tok/redeem');
  });

  test('workflowCatalog reports unavailable integrations with a reason', () async {
    final client = VoxDeskClient(
      apiKey: 'vd_test',
      httpClient: stub(200, {
        'integrations': [
          {'key': 'slack', 'name': 'Slack', 'auth': 'oauth2', 'available': true},
          {
            'key': 'outlook',
            'name': 'Outlook Calendar',
            'auth': 'oauth2',
            'available': false,
            'reason': 'no app registration'
          }
        ]
      }),
    );
    final rows = await client.workflowCatalog();
    expect(rows.first.available, isTrue);
    expect(rows.last.available, isFalse);
    expect(rows.last.reason, 'no app registration');
  });

  test('a network failure is a VoxDeskException, not a crash', () async {
    final client = VoxDeskClient(
      apiKey: 'vd_test',
      httpClient: MockClient((_) async => throw const SocketFailure()),
    );
    expect(
      () => client.listAgents(),
      throwsA(isA<VoxDeskException>().having((e) => e.code, 'code', 'network_error')),
    );
  });
}

class SocketFailure implements Exception {
  const SocketFailure();
}
