import 'package:voxdesk/voxdesk.dart';

Future<void> main() async {
  final client = VoxDeskClient(
    apiKey: const String.fromEnvironment('VOXDESK_API_KEY'),
    baseUrl: const String.fromEnvironment('VOXDESK_BASE_URL',
        defaultValue: 'https://api.voxdesk.example'),
  );
  final agents = await client.listAgents();
  for (final agent in agents.items) {
    print('${agent.id}  ${agent.name}  ${agent.status}');
  }
  client.close();
}
