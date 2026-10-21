import Foundation

// MARK: - Enrollment server contract (Revision 3, per /tmp/enroll-contract.md)
//
// POST {Config.httpBaseURL}/api/enroll
// Also POST {Config.httpBaseURL}/api/people/enroll (alias, identical
// contract). Default base: http://192.168.2.1:8000 (see
// Common/Config.swift). Config.enrollmentURL is httpBaseURL +
// "/api/enroll"; this client posts to /api/enroll only.
//
// Request: application/json, one JSON object:
//   name      string, required, non-blank. The person's display name.
//   photo_b64 string, required. Base64 of a JPEG photo (must start
//             with the JPEG magic bytes FF D8 FF). The server computes
//             an ArcFace face embedding.
//   voice_b64 string, required. Base64 of RAW PCM16 little endian,
//             16 kHz, mono audio. NO WAV header, NO RIFF header, no
//             container of any kind: the server rejects a RIFF header
//             with 422. See rawPCMStrippingWAVHeader(_:) for the
//             WAV-strip guard. The server computes an ECAPA voice
//             embedding.
//   notes     string, optional, free text.
//
// The old Revision 2 multipart/form-data WAV path was deleted entirely.
//
// Success: HTTP 200 with a JSON person object containing at least
// `id` and `name` (older builds also accept `person_id`).
//
// Failure: 422 for client-fixable payload problems (missing fields,
// bad base64, non-JPEG photo, WAV/RIFF voice header, voice under 1 s
// or odd byte count, ML rejection), 413 for oversized photo/voice,
// 503 when the enrollment service is down. The phone treats 422/413
// as "fix the payload and retry" and anything else as server side.
//
// Embeddings live server side (ChromaDB). The phone never stores or
// computes face or voice embeddings.

// MARK: - Result

struct EnrollmentResult: Codable, Equatable {
    let personID: String
    let name: String
    let faceEnrolled: Bool
    let voiceEnrolled: Bool

    enum CodingKeys: String, CodingKey {
        case personID = "person_id"
        case id
        case name
        case faceEnrolled = "face_enrolled"
        case voiceEnrolled = "voice_enrolled"
    }

    init(personID: String, name: String, faceEnrolled: Bool, voiceEnrolled: Bool) {
        self.personID = personID
        self.name = name
        self.faceEnrolled = faceEnrolled
        self.voiceEnrolled = voiceEnrolled
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // The wire contract names the key `id`; accept the legacy
        // `person_id` too.
        if let id = try container.decodeIfPresent(String.self, forKey: .personID) {
            personID = id
        } else {
            personID = try container.decode(String.self, forKey: .id)
        }
        name = try container.decode(String.self, forKey: .name)
        // The enrolled flags are informational; tolerate a server that
        // returns only the id and name.
        faceEnrolled = try container.decodeIfPresent(Bool.self, forKey: .faceEnrolled) ?? false
        voiceEnrolled = try container.decodeIfPresent(Bool.self, forKey: .voiceEnrolled) ?? false
    }
}

// MARK: - Request

/// The JSON body posted to /api/enroll.
private struct EnrollmentRequestBody: Encodable {
    let name: String
    let photo_b64: String
    let voice_b64: String
    let notes: String?

    enum CodingKeys: String, CodingKey {
        case name
        case photo_b64
        case voice_b64
        case notes
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(photo_b64, forKey: .photo_b64)
        try container.encode(voice_b64, forKey: .voice_b64)
        try container.encodeIfPresent(notes, forKey: .notes)
    }
}

// MARK: - Errors

enum EnrollmentError: Error, LocalizedError {
    /// Server answered with a non-2xx status. Associated value is the
    /// `detail`/`message` string from the error body when present,
    /// otherwise a short status description.
    case server(String)
    /// Transport level failure (no route, timeout, TLS, bad response).
    case network(String)
    /// Local validation failure before any request was sent.
    case invalidInput(String)

    var errorDescription: String? {
        switch self {
        case .server(let detail):
            return detail
        case .network(let message):
            return "Network error: \(message)"
        case .invalidInput(let message):
            return message
        }
    }
}

private struct EnrollmentErrorBody: Decodable {
    let detail: String?
    let code: String?
    let message: String?
}

// MARK: - API client

final class EnrollmentAPI {

    let baseURL: URL
    private let session: URLSession

    init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    /// Convenience initializer using the shared app config.
    convenience init(session: URLSession = .shared) {
        self.init(baseURL: Config.httpBaseURL, session: session)
    }

    var enrollmentEndpoint: URL {
        // Prefer the config provided endpoint when the base URL matches
        // the configured one, so there is a single source of truth.
        if baseURL == Config.httpBaseURL {
            return Config.enrollmentURL
        }
        // XCODE-VERIFY: confirm the server serves enrollment at
        // POST /api/enroll (alias /api/people/enroll accepts the same
        // JSON body) and NOT at the old /enroll multipart path.
        return baseURL.appendingPathComponent("api/enroll")
    }

    /// Uploads one person's enrollment photo and voice sample.
    ///
    /// - Parameters:
    ///   - name: Display name, sent as the `name` JSON field.
    ///   - photoJPEG: JPEG bytes for the `photo_b64` field (base64 of
    ///     the JPEG).
    ///   - voicePCM: Raw PCM16 little endian 16 kHz mono bytes for the
    ///     `voice_b64` field (base64 of the RAW PCM, no WAV header).
    ///     If a caller accidentally passes a WAV file instead, the
    ///     canonical 44 byte header is stripped first; see
    ///     `rawPCMStrippingWAVHeader(_:)`.
    ///   - notes: Optional free text sent as the `notes` field.
    func enroll(name: String,
                photoJPEG: Data,
                voicePCM: Data,
                notes: String? = nil) async throws -> EnrollmentResult {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else {
            throw EnrollmentError.invalidInput("Name is empty.")
        }
        guard !photoJPEG.isEmpty else {
            throw EnrollmentError.invalidInput("Photo is empty.")
        }
        guard !voicePCM.isEmpty else {
            throw EnrollmentError.invalidInput("Voice sample is empty.")
        }

        let body = EnrollmentRequestBody(
            name: trimmedName,
            photo_b64: photoJPEG.base64EncodedString(),
            voice_b64: Self.rawPCMStrippingWAVHeader(voicePCM).base64EncodedString(),
            notes: notes
        )
        let bodyData: Data
        do {
            bodyData = try JSONEncoder().encode(body)
        } catch {
            throw EnrollmentError.invalidInput("Could not encode the enrollment request.")
        }

        var request = URLRequest(url: enrollmentEndpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = bodyData

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw EnrollmentError.network(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw EnrollmentError.network("No HTTP response from server.")
        }

        guard (200..<300).contains(http.statusCode) else {
            let detail = Self.parseErrorDetail(from: data)
                ?? "Server returned HTTP \(http.statusCode)."
            throw EnrollmentError.server(detail)
        }

        do {
            return try JSONDecoder().decode(EnrollmentResult.self, from: data)
        } catch {
            throw EnrollmentError.server(
                "Server returned an unreadable success response."
            )
        }
    }

    private static func parseErrorDetail(from data: Data) -> String? {
        guard !data.isEmpty else { return nil }
        if let decoded = try? JSONDecoder().decode(
            EnrollmentErrorBody.self, from: data) {
            if let detail = decoded.detail, !detail.isEmpty {
                return detail
            }
            // Server wire-contract error shape: { code, message }.
            if let message = decoded.message, !message.isEmpty {
                if let code = decoded.code, !code.isEmpty {
                    return "\(code): \(message)"
                }
                return message
            }
        }
        // Fall back to the raw body text so the demo operator sees
        // something real on screen.
        if let text = String(data: data, encoding: .utf8),
           !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return text
        }
        return nil
    }

    // MARK: WAV header strip

    /// Returns the raw PCM inside `data`, stripping the canonical
    /// 44 byte WAV (RIFF/WAVE, PCM) header when one is present.
    ///
    /// The voice contract sends RAW PCM, so callers pass the recorder's
    /// output unchanged. This is only a guard for WAV bytes handed in
    /// by mistake: when the data starts with the RIFF magic, the first
    /// 44 bytes (the canonical header of a RIFF/WAVE PCM file) are
    /// dropped and the PCM payload after them is returned. Otherwise
    /// the data is returned as is. The server rejects a RIFF header
    /// with 422, so this guard keeps a mistaken WAV from silently
    /// failing enrollment.
    static func rawPCMStrippingWAVHeader(_ data: Data) -> Data {
        guard data.count > 44 else { return data }
        // XCODE-VERIFY: the recorder (VoiceSampleRecorder) produces raw
        // PCM16 little endian, 16 kHz, mono (2 bytes per sample,
        // exactly 320000 bytes for the 10 s enrollment sample), never
        // WAV bytes. This strip only fires for data starting with the
        // "RIFF" magic of a canonical 44-byte WAV header; anything else
        // (including non-canonical WAV with extra chunks) passes
        // through untouched. Confirm on the Mac that the enrollment
        // voice upload on the wire is raw PCM with no 44-byte header:
        // a RIFF/WAVE header is a hard 422 from the server.
        if data[0] == 0x52, data[1] == 0x49,
           data[2] == 0x46, data[3] == 0x46 { // "RIFF"
            return data.dropFirst(44)
        }
        return data
    }
}
