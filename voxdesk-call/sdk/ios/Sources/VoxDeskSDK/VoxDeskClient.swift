import Foundation

/// How the host app supplies credentials. The SDK never stores an access token.
public typealias TokenProvider = @Sendable () async -> String?

/// The server answered and refused. `code` is the machine-readable reason.
public struct VoxDeskAPIError: Error, Equatable, Sendable {
    /// HTTP status.
    public let status: Int
    /// e.g. `quiet_hours_too_long`, `device_not_found`, `permission_denied`.
    public let code: String
    public let message: String

    public init(status: Int, code: String, message: String) {
        self.status = status
        self.code = code
        self.message = message
    }

    /// A 4xx that a retry will not fix — 429 excluded, because it invites one.
    public var isPermanent: Bool { (400..<500).contains(status) && status != 429 }

    /// The device is already gone, which is the end state `revoke` wants.
    public var isAlreadyGone: Bool { status == 404 }
}

/// No answer arrived: offline, DNS, timeout. Retryable, and not a refusal.
public struct VoxDeskNetworkError: Error, Sendable {
    public let message: String
    public let underlying: String
}

/// The HTTP client.
///
/// `URLSession` with `async/await`. Three behaviours are deliberate:
///
/// * **Every request has a timeout.** A promise that never settles is
///   indistinguishable from a frozen app, and gets reported as one.
/// * **A refusal is typed.** Callers branch on `VoxDeskAPIError.code`.
/// * **A 401 triggers `onUnauthorized` once**, so the host app can refresh or sign
///   out in one place, then the error is still thrown.
public final class VoxDeskClient: @unchecked Sendable {
    public let baseURL: URL
    private let tokenProvider: TokenProvider?
    private let onUnauthorized: (@Sendable () -> Void)?
    private let extraHeaders: [String: String]
    private let timeout: TimeInterval
    private let session: URLSession

    public init(
        baseURL: String,
        tokenProvider: TokenProvider? = nil,
        timeout: TimeInterval = 15,
        extraHeaders: [String: String] = [:],
        onUnauthorized: (@Sendable () -> Void)? = nil,
        session: URLSession = .shared
    ) {
        let trimmed = baseURL.hasSuffix("/") ? String(baseURL.dropLast()) : baseURL
        guard let url = URL(string: trimmed) else {
            preconditionFailure("VoxDeskClient requires an absolute base URL, got \(baseURL)")
        }
        self.baseURL = url
        self.tokenProvider = tokenProvider
        self.timeout = timeout
        self.extraHeaders = extraHeaders
        self.onUnauthorized = onUnauthorized
        self.session = session
    }

    /// Build the absolute URL for a path and query. Blanks are dropped, and values
    /// are percent-encoded — a `+` in a query string is a space, which is not what
    /// an ISO-8601 timestamp containing `+02:00` means.
    public func url(for path: String, query: [String: String] = [:]) -> URL {
        let normalized = path.hasPrefix("/") ? path : "/" + path
        var components = URLComponents(
            url: baseURL.appendingPathComponent(String(normalized.dropFirst())),
            resolvingAgainstBaseURL: false
        )
        let pairs = query
            .filter { !$0.value.isEmpty }
            .sorted { $0.key < $1.key }
            .map { URLQueryItem(name: $0.key, value: $0.value) }
        if !pairs.isEmpty {
            components?.queryItems = pairs
        }
        return components?.url ?? baseURL.appendingPathComponent(normalized)
    }

    public func get<T: Decodable>(
        _ path: String,
        query: [String: String] = [:],
        as type: T.Type
    ) async throws -> T {
        let data = try await send(method: "GET", path: path, query: query, body: nil, idempotencyKey: nil)
        return try decode(data, as: type)
    }

    public func post<Body: Encodable, T: Decodable>(
        _ path: String,
        body: Body,
        as type: T.Type,
        idempotencyKey: String? = nil
    ) async throws -> T {
        let data = try await send(
            method: "POST",
            path: path,
            query: [:],
            body: try JSONEncoder().encode(body),
            idempotencyKey: idempotencyKey
        )
        return try decode(data, as: type)
    }

    /// `POST` where the response body carries nothing the caller needs.
    @discardableResult
    public func postForget<Body: Encodable>(
        _ path: String,
        body: Body,
        idempotencyKey: String? = nil
    ) async throws -> Data {
        try await send(
            method: "POST",
            path: path,
            query: [:],
            body: try JSONEncoder().encode(body),
            idempotencyKey: idempotencyKey
        )
    }

    /// `POST` with no body, e.g. `/accept` or `/cancel`.
    @discardableResult
    public func postEmpty(_ path: String) async throws -> Data {
        try await send(method: "POST", path: path, query: [:], body: nil, idempotencyKey: nil)
    }

    public func delete(_ path: String) async throws {
        _ = try await send(method: "DELETE", path: path, query: [:], body: nil, idempotencyKey: nil)
    }

    /// A fresh idempotency key. Keep it with the intent if a retry is possible.
    public static func newIdempotencyKey() -> String {
        UUID().uuidString
    }

    private func decode<T: Decodable>(_ data: Data, as type: T.Type) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw VoxDeskNetworkError(
                message: "the server returned a body this app cannot read",
                underlying: String(describing: error)
            )
        }
    }

    private func send(
        method: String,
        path: String,
        query: [String: String],
        body: Data?,
        idempotencyKey: String?
    ) async throws -> Data {
        var request = URLRequest(url: url(for: path, query: query))
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        for (key, value) in extraHeaders {
            request.setValue(value, forHTTPHeaderField: key)
        }
        if let tokenProvider, let token = await tokenProvider() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let idempotencyKey {
            request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
        }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw VoxDeskNetworkError(message: "request to \(path) failed", underlying: String(describing: error))
        }
        guard let http = response as? HTTPURLResponse else {
            throw VoxDeskNetworkError(message: "no HTTP response for \(path)", underlying: "not an HTTPURLResponse")
        }
        if http.statusCode == 401 {
            onUnauthorized?()
        }
        guard (200..<300).contains(http.statusCode) else {
            let envelope = try? JSONDecoder().decode(ApiErrorEnvelope.self, from: data)
            let message = envelope?.detail?.message
                ?? String(data: data.prefix(300), encoding: .utf8)
                ?? "request failed with status \(http.statusCode)"
            throw VoxDeskAPIError(
                status: http.statusCode,
                code: envelope?.detail?.code ?? "error",
                message: message.isEmpty ? "request failed with status \(http.statusCode)" : message
            )
        }
        return data
    }
}
