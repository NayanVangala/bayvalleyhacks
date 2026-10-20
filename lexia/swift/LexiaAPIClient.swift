// Lexia Read Mode for OnRecord. Uncompiled Swift source written Oct 2026. Bridge team: copy into the bridge-ios target and wire the marked integration points.
//
// LexiaAPIClient.swift
// URLSession client for the standalone Lexia API.
// INTEGRATION POINT: set baseURL to the laptop running Lexia, for example
// http://192.168.1.20:8100 on the demo WiFi network. Do not hardcode
// localhost, because on the phone localhost is the phone itself.
// The laptop and the phone or glasses bridge must be on the same network.

import Foundation

public final class LexiaAPIClient {

    // MARK: - Configuration

    public var baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
        self.decoder = JSONDecoder()
        self.encoder = JSONEncoder()
    }

    /// Convenience initializer from a string such as "http://192.168.1.20:8100".
    public convenience init?(baseURLString: String, session: URLSession = .shared) {
        guard let url = URL(string: baseURLString) else { return nil }
        self.init(baseURL: url, session: session)
    }

    // MARK: - Request payload

    private struct ReadRequest: Encodable {
        let imageBase64: String
        let simplify: Bool

        enum CodingKeys: String, CodingKey {
            case imageBase64 = "image_base64"
            case simplify
        }
    }

    // MARK: - Health

    /// GET /health -> {"status", "ocr_engine", "tts_engine"}
    public func health() async throws -> HealthResponse {
        guard let url = URL(string: "health", relativeTo: normalizedBaseURL()) else {
            throw LexiaError.badURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await perform(request, as: HealthResponse.self)
    }

    // MARK: - Read

    /// POST /read with a base64 encoded image.
    /// - Parameters:
    ///   - imageData: JPEG data from a ReadCaptureSource.
    ///   - simplify: Ask Lexia to simplify the recognized text when true.
    public func read(imageData: Data, simplify: Bool) async throws -> ReadResponse {
        guard let url = URL(string: "read", relativeTo: normalizedBaseURL()) else {
            throw LexiaError.badURL
        }
        let payload = ReadRequest(
            imageBase64: imageData.base64EncodedString(),
            simplify: simplify
        )
        let body: Data
        do {
            body = try encoder.encode(payload)
        } catch {
            throw LexiaError.decoding
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = body
        return try await perform(request, as: ReadResponse.self)
    }

    // MARK: - Speak

    /// POST /read/{session_id}/speak -> {"audio_url", "duration_seconds", "engine"}
    public func speak(sessionID: String) async throws -> SpeakResponse {
        let trimmed = sessionID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw LexiaError.noSession
        }
        guard let url = URL(string: "read/\(trimmed)/speak", relativeTo: normalizedBaseURL()) else {
            throw LexiaError.badURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await perform(request, as: SpeakResponse.self)
    }

    // MARK: - Audio URL helper

    /// Resolves the audio_url from a SpeakResponse against the base URL.
    /// The API may return an absolute URL or a path relative to the base URL.
    /// Returns nil if the value cannot be turned into a valid URL.
    public func audioURL(for response: SpeakResponse) -> URL? {
        if let absolute = URL(string: response.audioURL), absolute.scheme != nil {
            return absolute
        }
        return URL(string: response.audioURL, relativeTo: normalizedBaseURL())?.absoluteURL
    }

    // MARK: - Private helpers

    /// Ensures the base URL ends with a slash so relative URL resolution
    /// keeps the full base path.
    private func normalizedBaseURL() -> URL {
        var string = baseURL.absoluteString
        if !string.hasSuffix("/") {
            string += "/"
        }
        return URL(string: string) ?? baseURL
    }

    private func perform<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw LexiaError.network
        }
        guard let http = response as? HTTPURLResponse else {
            throw LexiaError.network
        }
        guard (200..<300).contains(http.statusCode) else {
            throw LexiaError.server(status: http.statusCode)
        }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw LexiaError.decoding
        }
    }
}
