"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Person } from "@/lib/types";
import { enrollPerson } from "@/lib/api";

interface EnrollmentFormProps {
  onEnrolled?: (p: Person) => void;
}

const MAX_RECORD_SECONDS = 10;

export default function EnrollmentForm({ onEnrolled }: EnrollmentFormProps) {
  const [name, setName] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [voiceFile, setVoiceFile] = useState<File | null>(null);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedSeconds, setRecordedSeconds] = useState(0);
  const [isRecording, setIsRecording] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [micUnavailable, setMicUnavailable] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const formRef = useRef<HTMLFormElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startTimeRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function releaseRecordingResources(stopRecorder = false) {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    if (streamRef.current !== null) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (stopRecorder && recorder && recorder.state !== "inactive") {
      // Unmount path: silence onstop first so it cannot set state on an
      // unmounted component, then actually stop the recorder instead
      // of leaving it recording with only its tracks stopped.
      recorder.ondataavailable = null;
      recorder.onstop = null;
      try {
        recorder.stop();
      } catch {
        // Already stopped.
      }
    }
  }

  useEffect(() => {
    const supported =
      typeof navigator !== "undefined" &&
      !!navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function" &&
      typeof MediaRecorder !== "undefined";
    if (!supported) {
      setMicUnavailable(true);
    }
  }, []);

  useEffect(() => {
    return () => {
      releaseRecordingResources(true);
    };
  }, []);

  async function startRecording() {
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      if (
        typeof navigator === "undefined" ||
        !navigator.mediaDevices ||
        typeof navigator.mediaDevices.getUserMedia !== "function" ||
        typeof MediaRecorder === "undefined"
      ) {
        setMicUnavailable(true);
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        const seconds = Math.min(
          MAX_RECORD_SECONDS,
          Math.max(
            1,
            Math.round((Date.now() - startTimeRef.current) / 1000),
          ),
        );
        setRecordedBlob(blob);
        setRecordedSeconds(seconds);
        setIsRecording(false);
        releaseRecordingResources();
      };
      setRecordedBlob(null);
      setRecordedSeconds(0);
      // Starting a new recording supersedes any uploaded file, so the
      // sample that gets submitted is the one the user acted on last.
      setVoiceFile(null);
      startTimeRef.current = Date.now();
      recorder.start();
      setIsRecording(true);
      setElapsedSeconds(0);
      intervalRef.current = setInterval(() => {
        setElapsedSeconds(
          Math.floor((Date.now() - startTimeRef.current) / 1000),
        );
      }, 250);
      timeoutRef.current = setTimeout(() => {
        stopRecording();
      }, MAX_RECORD_SECONDS * 1000);
    } catch {
      releaseRecordingResources();
      setIsRecording(false);
      setMicUnavailable(true);
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSuccessMessage(null);
    setErrorMessage(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setErrorMessage("Enter a name first.");
      return;
    }
    if (!photo) {
      setErrorMessage("Choose a photo first.");
      return;
    }
    let voice: File | null = null;
    if (recordedBlob) {
      voice = new File([recordedBlob], "voice.webm", {
        type: recordedBlob.type || "audio/webm",
      });
    } else if (voiceFile) {
      voice = voiceFile;
    }
    if (!voice) {
      setErrorMessage(
        "Record a voice sample or upload a voice sample file first.",
      );
      return;
    }
    const fd = new FormData();
    fd.append("name", trimmedName);
    fd.append("photo", photo);
    fd.append("voice", voice);
    setSubmitting(true);
    try {
      const person = await enrollPerson(fd);
      setSuccessMessage(`Enrolled ${person.name}.`);
      setName("");
      setPhoto(null);
      setVoiceFile(null);
      setRecordedBlob(null);
      setRecordedSeconds(0);
      formRef.current?.reset();
      onEnrolled?.(person);
    } catch (err) {
      setErrorMessage(
        `Enrollment failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form ref={formRef} onSubmit={handleSubmit} className="space-y-3">
      <div className="space-y-3">
        <label htmlFor="enroll-name" className="block">
          Name
        </label>
        <input
          id="enroll-name"
          type="text"
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="block border rounded px-2 py-1"
        />
      </div>
      <div className="space-y-3">
        <label htmlFor="enroll-photo" className="block">
          Photo
        </label>
        <input
          id="enroll-photo"
          type="file"
          accept="image/*"
          required
          onChange={(event) =>
            setPhoto(event.target.files?.[0] ?? null)
          }
          className="block border rounded px-2 py-1"
        />
      </div>
      <div className="space-y-3">
        <span className="block">Voice sample</span>
        {!micUnavailable && (
          <div className="space-y-3">
            {isRecording ? (
              <button
                type="button"
                onClick={stopRecording}
                disabled={submitting}
                className="border rounded px-2 py-1"
              >
                Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={startRecording}
                disabled={submitting}
                className="border rounded px-2 py-1"
              >
                Record voice sample
              </button>
            )}
            {isRecording && <p>Recording... {elapsedSeconds}s</p>}
            {!isRecording && recordedBlob && (
              <p>Voice sample recorded ({recordedSeconds}s)</p>
            )}
          </div>
        )}
        {micUnavailable && (
          <p>
            Microphone recording is not available in this browser, so
            upload a voice sample file instead.
          </p>
        )}
        <div className="space-y-3">
          <label htmlFor="enroll-voice-file" className="block">
            Or upload a voice sample file
          </label>
          <input
            id="enroll-voice-file"
            type="file"
            accept="audio/*"
            onChange={(event) => {
              setVoiceFile(event.target.files?.[0] ?? null);
              if (event.target.files?.[0]) {
                // An explicit upload supersedes an earlier recording
                // (handleSubmit prefers a recording when both exist).
                // A cancelled picker fires no file and keeps it.
                setRecordedBlob(null);
                setRecordedSeconds(0);
              }
            }}
            className="block border rounded px-2 py-1"
          />
          {voiceFile && <p>Voice sample file selected: {voiceFile.name}</p>}
        </div>
      </div>
      <button
        type="submit"
        disabled={submitting || isRecording}
        className="border rounded px-2 py-1"
      >
        {submitting ? "Enrolling..." : "Enroll"}
      </button>
      {isRecording && <p>Stop the recording before enrolling.</p>}
      {successMessage && <p>{successMessage}</p>}
      {errorMessage && <p>{errorMessage}</p>}
    </form>
  );
}
