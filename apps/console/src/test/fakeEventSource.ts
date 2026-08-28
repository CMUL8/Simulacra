import type { FakeClock } from "./fakeClock";

type FakeMessageEvent = Readonly<{ data: string }>;
type FakeListener = (event: FakeMessageEvent) => void;

export class FakeEventSource {
  private readonly listeners = new Map<string, Set<FakeListener>>();
  private wakeUpSequence = 0;
  reconnectFailureCount = 0;

  constructor(private readonly clock: FakeClock) {}

  addEventListener(type: string, listener: FakeListener): void {
    const listeners = this.listeners.get(type) ?? new Set<FakeListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: FakeListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  recordReconnectFailure(): void {
    this.reconnectFailureCount += 1;
  }

  emitWakeUp(payload: { mission_id: string }): void {
    this.wakeUpSequence += 1;
    const event = {
      id: `wake_${String(this.wakeUpSequence).padStart(4, "0")}`,
      mission_id: payload.mission_id,
      occurred_at: this.clock.now().toISOString(),
      type: "wake-up",
    };
    const message = Object.freeze({ data: JSON.stringify(event) });
    for (const listener of this.listeners.get("wake-up") ?? []) listener(message);
  }
}
