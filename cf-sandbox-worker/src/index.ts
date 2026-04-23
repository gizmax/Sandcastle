/**
 * Sandcastle CF Sandbox Worker
 *
 * Wraps Cloudflare Sandbox to execute agent runner scripts.
 * Receives runner code + env vars from the Sandcastle Python backend
 * and returns stdout/stderr as JSON.
 */

import { getSandbox, type Sandbox } from "@cloudflare/sandbox";

// Re-export for wrangler container binding
export { Sandbox } from "@cloudflare/sandbox";

interface Env {
  Sandbox: Sandbox;
  SANDBOX_SHARED_SECRET?: string;
}

interface RunRequest {
  runner_file: string;
  runner_content: string;
  envs: Record<string, string>;
  tool_files?: Record<string, string>;
}

// Only these runner filenames are allowed - prevents arbitrary path writes
const ALLOWED_RUNNERS = new Set([
  "runner.js",
  "runner.mjs",
  "runner.ts",
  "runner.py",
  "runner.sh",
]);

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/health") {
      return Response.json({ ok: true });
    }

    // Run agent in sandbox
    if (url.pathname === "/run" && request.method === "POST") {
      // Authenticate: require shared secret when configured
      if (env.SANDBOX_SHARED_SECRET) {
        const auth = request.headers.get("Authorization");
        if (auth !== `Bearer ${env.SANDBOX_SHARED_SECRET}`) {
          return Response.json(
            { error: "Unauthorized", stdout: "", stderr: "", exitCode: 1 },
            { status: 401 }
          );
        }
      }

      try {
        const { runner_file, envs, runner_content, tool_files } =
          (await request.json()) as RunRequest;

        // Validate runner_file BEFORE any file write
        if (
          !runner_file ||
          runner_file.includes("..") ||
          runner_file.includes("/") ||
          runner_file.includes("\\") ||
          runner_file.startsWith(".") ||
          !ALLOWED_RUNNERS.has(runner_file)
        ) {
          return Response.json(
            { error: "Invalid runner file path", stdout: "", stderr: "", exitCode: 1 },
            { status: 400 }
          );
        }

        const sandbox = getSandbox(env.Sandbox, crypto.randomUUID());

        // Write runner script into the sandbox (validated above)
        await sandbox.writeFile(`/home/user/${runner_file}`, runner_content);

        // Write tool files into the sandbox
        if (tool_files && typeof tool_files === "object") {
          for (const [fname, content] of Object.entries(tool_files)) {
            // Validate: simple filename only, no traversal
            if (
              !fname ||
              fname.includes("..") ||
              fname.includes("/") ||
              fname.includes("\\") ||
              fname.startsWith(".")
            ) {
              return Response.json(
                { error: `Invalid tool file name: ${fname}`, stdout: "", stderr: "", exitCode: 1 },
                { status: 400 }
              );
            }
            await sandbox.writeFile(`/home/user/${fname}`, content);
          }
        }

        // Build env export string and execute.
        // Validate env var names to prevent shell injection via keys.
        const envEntries = Object.entries(envs).filter(
          ([k]) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(k)
        );
        const envString = envEntries
          .map(([k, v]) => `export ${k}='${v.replace(/'/g, "'\\''")}'`)
          .join(" && ");

        const cmd = envString
          ? `${envString} && node /home/user/${runner_file}`
          : `node /home/user/${runner_file}`;

        const result = await sandbox.exec(cmd);

        return Response.json({
          stdout: result.stdout,
          stderr: result.stderr,
          exitCode: result.exitCode,
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return Response.json(
          { error: message, stdout: "", stderr: "", exitCode: 1 },
          { status: 500 }
        );
      }
    }

    return new Response("Not Found", { status: 404 });
  },
};
