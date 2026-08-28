import { spawn, type ChildProcess } from "node:child_process";

const origins = ["http://127.0.0.1:4173", "http://127.0.0.1:4174"] as const;

async function waitForOrigin(origin: string): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${origin}/__w0_harness__`);
      if (response.ok) return;
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for W0 harness at ${origin}`);
}

function start(role: "control" | "preview", port: number, peerOrigin: string): ChildProcess {
  return spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
    cwd: new URL("..", import.meta.url),
    env: {
      ...process.env,
      MISSIONS_W0_HARNESS_ROLE: role,
      MISSIONS_W0_HARNESS_PEER_ORIGIN: peerOrigin,
      MISSIONS_W4_PREVIEW_HARNESS: "1",
    },
    detached: process.platform !== "win32",
    stdio: "ignore",
  });
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const processes = [
    start("control", 4173, origins[1]),
    start("preview", 4174, origins[0]),
  ];

  try {
    await Promise.all(origins.map(waitForOrigin));
  } catch (error) {
    for (const process of processes) process.kill("SIGTERM");
    throw error;
  }

  return async () => {
    for (const child of processes) {
      if (child.exitCode !== null || child.pid === undefined) continue;
      if (process.platform === "win32") child.kill("SIGTERM");
      else {
        try {
          process.kill(-child.pid, "SIGTERM");
        } catch {
          child.kill("SIGTERM");
        }
      }
    }
  };
}
