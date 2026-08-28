import { expect, test } from "vitest";

import { FakeClock } from "./fakeClock";
import { FakeEventSource } from "./fakeEventSource";

test("fake_clock_and_event_source_are_deterministic", () => {
  const clock = new FakeClock("2026-01-02T09:00:00Z");
  const eventSource = new FakeEventSource(clock);
  const wakeUps: string[] = [];

  eventSource.addEventListener("wake-up", (event) => {
    wakeUps.push(event.data);
  });
  eventSource.recordReconnectFailure();
  eventSource.recordReconnectFailure();
  clock.advanceBy(60_000);
  eventSource.emitWakeUp({ mission_id: "mission_alpha" });

  expect(clock.now().toISOString()).toBe("2026-01-02T09:01:00.000Z");
  expect(wakeUps).toEqual([
    JSON.stringify({
      id: "wake_0001",
      mission_id: "mission_alpha",
      occurred_at: "2026-01-02T09:01:00.000Z",
      type: "wake-up",
    }),
  ]);
  expect(eventSource.reconnectFailureCount).toBe(2);
});
