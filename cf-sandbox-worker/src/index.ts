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
}

interface RunRequest {
  runner_file: string;
  runner_content: string;
  envs: Record<string, string>;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/health") {
      return Response.json({ ok: true });
    }

    // Run agent in sandbox
    if (url.pathname === "/run" && request.method === "POST") {
      try {
        const { runner_file, envs, runner_content } =
          (await request.json()) as RunRequest;

        const sandbox = getSandbox(env.Sandbox, crypto.randomUUID());

        // Write runner script into the sandbox
        await sandbox.writeFile(`/home/user/${runner_file}`, runner_content);

        // Build env export string and execute
        const envString = Object.entries(envs)
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
