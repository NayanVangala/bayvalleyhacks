import type { LiveEvent } from "./types";

export type ConnectionState = "connecting" | "open" | "closed";

const DEFAULT_URL = "ws://localhost:8000/ws/live";
const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 16000;

type EventListener = (e: LiveEvent) => void;
type StateListener = (s: ConnectionState) => void;

function isLiveEventLike(value: unknown): value is LiveEvent {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const type: unknown = (value as { type?: unknown }).type;
  return typeof type === "string";
}

export class LiveClient {
  private readonly url: string;
  private readonly eventListeners = new Set<EventListener>();
  private readonly stateListeners = new Set<StateListener>();

  private socket: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelayMs: number = INITIAL_RECONNECT_DELAY_MS;
  private intentionallyClosed = false;
  private currentState: ConnectionState = "closed";

  constructor(url?: string) {
    // Safe during SSR: only reads process.env, never touches
    // window or WebSocket here. Those are used only in connect().
    this.url = url ?? process.env.NEXT_PUBLIC_WS_URL ?? DEFAULT_URL;
  }

  get state(): ConnectionState {
    return this.currentState;
  }

  onEvent(cb: (e: LiveEvent) => void): () => void {
    this.eventListeners.add(cb);
    return () => {
      this.eventListeners.delete(cb);
    };
  }

  onState(cb: (s: ConnectionState) => void): () => void {
    this.stateListeners.add(cb);
    return () => {
      this.stateListeners.delete(cb);
    };
  }

  connect(): void {
    this.intentionallyClosed = false;

    if (
      this.socket !== null &&
      (this.socket.readyState === WebSocket.CONNECTING ||
        this.socket.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    if (typeof WebSocket === "undefined") {
      return;
    }

    this.clearReconnectTimer();
    this.setState("connecting");

    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url);
    } catch {
      this.socket = null;
      this.setState("closed");
      this.scheduleReconnect();
      return;
    }

    this.socket = socket;

    socket.onopen = () => {
      if (this.socket !== socket) {
        return;
      }
      this.reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
      this.setState("open");
    };

    socket.onmessage = (event: MessageEvent<unknown>) => {
      if (this.socket !== socket) {
        return;
      }
      this.handleMessage(event.data);
    };

    socket.onerror = () => {
      // Reconnect is driven by onclose, which always follows an
      // error on a WebSocket. Nothing else to do here.
    };

    socket.onclose = () => {
      if (this.socket !== socket) {
        return;
      }
      this.socket = null;
      this.setState("closed");
      if (!this.intentionallyClosed) {
        this.scheduleReconnect();
      }
    };
  }

  close(): void {
    this.intentionallyClosed = true;
    this.clearReconnectTimer();
    this.reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;

    const socket = this.socket;
    this.socket = null;

    if (socket !== null) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        socket.close();
      } catch {
        // Ignore errors from closing an already broken socket.
      }
    }

    this.setState("closed");
  }

  private handleMessage(data: unknown): void {
    // The dashboard feed is JSON control plus segment events only.
    // Binary frames (Blob, ArrayBuffer, typed arrays) are ignored.
    if (typeof data !== "string") {
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(data) as unknown;
    } catch {
      return;
    }

    if (!isLiveEventLike(parsed)) {
      return;
    }

    for (const listener of [...this.eventListeners]) {
      try {
        listener(parsed);
      } catch {
        // One bad listener must not starve the others or break the
        // socket handler.
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.intentionallyClosed || this.reconnectTimer !== null) {
      return;
    }

    const delay = this.reconnectDelayMs;
    this.reconnectDelayMs = Math.min(
      this.reconnectDelayMs * 2,
      MAX_RECONNECT_DELAY_MS,
    );

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.intentionallyClosed) {
        return;
      }
      this.connect();
    }, delay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setState(next: ConnectionState): void {
    if (this.currentState === next) {
      return;
    }
    this.currentState = next;
    for (const listener of [...this.stateListeners]) {
      try {
        listener(next);
      } catch {
        // A throwing state listener must not abort connect() before
        // the socket is created, or break close().
      }
    }
  }
}
