import Foundation

// MARK: - Enrollment server contract (assumed, Revision 2)
//
// POST {Config.httpBaseURL}/enroll
// Default base: http://192.168.2.1:8000 (see Common/Config.swift, worker 1).
// Config.enrollmentURL is expected to be httpBaseURL + "/enroll".
//
// Request: multipart/form-data with fields:
//   name        text, the person's display name
//   photo       file, filename "photo.jpg", Content-Type image/jpeg,
//               enrollment face photo (server computes an ArcFace embedding)
//   voice       file, filename "voice.wav", Content-Type audio/wav,
//               10 s PCM16 16 kHz mono WAV (server computes an ECAPA
//               voice embedding)
//   sample_rate text, "16000"
//
// Success (2xx) JSON:
//   { "person_id": "<uuid>", "name": "...",
//     "face_enrolled": true, "voice_enrolled": true }
//
// Error (non-2xx) JSON:
//   { "detail": "..." }
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
        case name
        case faceEnrolled = "face_enrolled"
        case voiceEnrolled = "voice_enrolled"
    }
}

// MARK: - Errors

enum EnrollmentError: Error, LocalizedError {
    /// Server answered with a non-2xx status. Associated value is the
    /// `detail` string from the error body when present, otherwise a
    /// short status description.
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
        return baseURL.appendingPathComponent("enroll")
    }

    /// Uploads one person's enrollment photo and voice sample.
    ///
    /// - Parameters:
    ///   - name: Display name, sent as the `name` text field.
    ///   - photoJPEG: JPEG bytes for the `photo` file field.
    ///   - voiceWAV: WAV bytes for the `voice` file field. Build these
    ///     from raw PCM with `EnrollmentAPI.wavWrapPCM(_:sampleRate:)`.
    func enroll(name: String,
                photoJPEG: Data,
                voiceWAV: Data) async throws -> EnrollmentResult {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else {
            throw EnrollmentError.invalidInput("Name is empty.")
        }
        guard !photoJPEG.isEmpty else {
            throw EnrollmentError.invalidInput("Photo is empty.")
        }
        guard !voiceWAV.isEmpty else {
            throw EnrollmentError.invalidInput("Voice sample is empty.")
        }

        let boundary = "InsightBridge-\(UUID().uuidString)"
        var request = URLRequest(url: enrollmentEndpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")
        request.httpBody = Self.multipartBody(
            boundary: boundary,
            name: trimmedName,
            photoJPEG: photoJPEG,
            voiceWAV: voiceWAV,
            sampleRate: AudioFormat.sampleRate
        )

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

    // MARK: Multipart body

    static func multipartBody(boundary: String,
                              name: String,
                              photoJPEG: Data,
                              voiceWAV: Data,
                              sampleRate: Int) -> Data {
        var body = Data()
        let crlf = "\r\n"

        func appendTextField(_ field: String, _ value: String) {
            body.append("--\(boundary)\(crlf)".data(using: .utf8)!)
            body.append(
                "Content-Disposition: form-data; name=\"\(field)\"\(crlf)"
                    .data(using: .utf8)!
            )
            body.append(crlf.data(using: .utf8)!)
            body.append(value.data(using: .utf8)!)
            body.append(crlf.data(using: .utf8)!)
        }

        func appendFileField(_ field: String,
                             filename: String,
                             contentType: String,
                             data: Data) {
            body.append("--\(boundary)\(crlf)".data(using: .utf8)!)
            body.append(
                ("Content-Disposition: form-data; name=\"\(field)\"; "
                    + "filename=\"\(filename)\"\(crlf)")
                    .data(using: .utf8)!
            )
            body.append(
                "Content-Type: \(contentType)\(crlf)".data(using: .utf8)!
            )
            body.append(crlf.data(using: .utf8)!)
            body.append(data)
            body.append(crlf.data(using: .utf8)!)
        }

        appendTextField("name", name)
        appendFileField("photo",
                        filename: "photo.jpg",
                        contentType: "image/jpeg",
                        data: photoJPEG)
        appendFileField("voice",
                        filename: "voice.wav",
                        contentType: "audio/wav",
                        data: voiceWAV)
        appendTextField("sample_rate", String(sampleRate))

        body.append("--\(boundary)--\(crlf)".data(using: .utf8)!)
        return body
    }

    private static func parseErrorDetail(from data: Data) -> String? {
        guard !data.isEmpty else { return nil }
        if let decoded = try? JSONDecoder().decode(
            EnrollmentErrorBody.self, from: data),
           let detail = decoded.detail,
           !detail.isEmpty {
            return detail
        }
        // FastAPI validation errors can return detail as an array or a
        // nested object. Fall back to the raw body text in that case so
        // the demo operator sees something real on screen.
        if let text = String(data: data, encoding: .utf8),
           !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return text
        }
        return nil
    }

    // MARK: WAV wrapping

    /// Wraps raw PCM16 little endian mono samples in a canonical
    /// 44 byte WAV header.
    ///
    /// Pure and deterministic: same input bytes always produce the
    /// same output bytes. No timestamps, no extra chunks.
    ///
    /// Layout: RIFF header, one `fmt ` chunk (PCM, 1 channel, 16 bit),
    /// then one `data` chunk holding `pcm` unchanged.
    static func wavWrapPCM(_ pcm: Data, sampleRate: Int) -> Data {
        let channels: UInt16 = 1
        let bitsPerSample: UInt16 = 16
        let byteRate: UInt32 = UInt32(sampleRate) * UInt32(channels)
            * UInt32(bitsPerSample / 8)
        let blockAlign: UInt16 = channels * (bitsPerSample / 8)
        let dataSize: UInt32 = UInt32(pcm.count)
        // RIFF chunk size is file size minus the 8 byte RIFF id + size.
        let riffSize: UInt32 = 36 + dataSize

        var out = Data()
        out.reserveCapacity(44 + pcm.count)

        func appendASCII(_ string: String) {
            out.append(string.data(using: .ascii)!)
        }
        func appendUInt32LE(_ value: UInt32) {
            var v = value.littleEndian
            out.append(Data(bytes: &v, count: 4))
        }
        func appendUInt16LE(_ value: UInt16) {
            var v = value.littleEndian
            out.append(Data(bytes: &v, count: 2))
        }

        // RIFF chunk descriptor.
        appendASCII("RIFF")
        appendUInt32LE(riffSize)
        appendASCII("WAVE")

        // fmt subchunk.
        appendASCII("fmt ")
        appendUInt32LE(16)              // fmt chunk size for PCM
        appendUInt16LE(1)               // audio format 1 = PCM
        appendUInt16LE(channels)
        appendUInt32LE(UInt32(sampleRate))
        appendUInt32LE(byteRate)
        appendUInt16LE(blockAlign)
        appendUInt16LE(bitsPerSample)

        // data subchunk.
        appendASCII("data")
        appendUInt32LE(dataSize)
        out.append(pcm)

        return out
    }
}
