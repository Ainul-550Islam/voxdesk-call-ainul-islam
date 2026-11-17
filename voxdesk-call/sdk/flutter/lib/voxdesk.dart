/// VoxDesk Dart/Flutter client.
///
/// The client is deliberately thin: it maps the REST surface onto typed
/// methods, and it turns a non-2xx response into a [VoxDeskException] carrying
/// the server's own error code. Nothing here retries automatically -- a caller
/// that retries a `POST /api/share-links` blindly creates duplicate links, and
/// that decision belongs to the application.
library voxdesk;

export 'src/client.dart';
