export class FakeClock {
  private timestamp: number;

  constructor(isoTimestamp: string) {
    this.timestamp = Date.parse(isoTimestamp);
    if (Number.isNaN(this.timestamp)) throw new Error(`Invalid fake-clock timestamp: ${isoTimestamp}`);
  }

  now(): Date {
    return new Date(this.timestamp);
  }

  advanceBy(milliseconds: number): Date {
    if (!Number.isFinite(milliseconds) || milliseconds < 0) {
      throw new Error("FakeClock can only advance by a non-negative finite duration");
    }
    this.timestamp += milliseconds;
    return this.now();
  }
}
