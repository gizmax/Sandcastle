/**
 * RAM-only Map-backed fake filesystem used by the Durable Object isolate
 * variant. Keys are absolute POSIX paths (e.g. `/workspace/notes.md`);
 * values are the file contents as UTF-8 strings. Directory existence is
 * implicit (any prefix of a stored file path is considered a directory).
 *
 * This implementation intentionally does NOT shell out, NOT spawn
 * subprocesses, and NOT touch the Cloudflare DO SQLite store: the state
 * lives only in V8 heap for the lifetime of the isolate. The trade-off vs
 * the Containers variant is documented in the README.
 */

const ROOT = "/workspace";

/** Normalize an absolute or relative path to an absolute /workspace path. */
function resolvePath(p: string): string {
  if (!p) throw new Error("fakefs: empty path");
  const abs = p.startsWith("/") ? p : `${ROOT}/${p}`;
  // Collapse `.` and `..` segments without invoking `node:path`.
  const out: string[] = [];
  for (const seg of abs.split("/")) {
    if (!seg || seg === ".") continue;
    if (seg === "..") {
      out.pop();
      continue;
    }
    out.push(seg);
  }
  return `/${out.join("/")}`;
}

export class FakeFS {
  private readonly files: Map<string, string> = new Map();

  /** Whether a regular file exists at `path`. */
  exists(path: string): boolean {
    return this.files.has(resolvePath(path));
  }

  /** Read a file. Throws if missing. */
  read(path: string): string {
    const abs = resolvePath(path);
    const v = this.files.get(abs);
    if (v === undefined) throw new Error(`fakefs: ENOENT ${abs}`);
    return v;
  }

  /** Create or overwrite a file. */
  write(path: string, content: string): void {
    this.files.set(resolvePath(path), content);
  }

  /**
   * Replace `oldStr` with `newStr` exactly once in the file at `path`.
   * Mirrors the contract of the `str_replace_based_edit_tool` tool variant.
   */
  edit(path: string, oldStr: string, newStr: string): void {
    const abs = resolvePath(path);
    const current = this.files.get(abs);
    if (current === undefined) throw new Error(`fakefs: ENOENT ${abs}`);
    const firstIdx = current.indexOf(oldStr);
    if (firstIdx < 0) throw new Error(`fakefs: edit miss in ${abs}`);
    if (current.indexOf(oldStr, firstIdx + 1) >= 0) {
      throw new Error(`fakefs: edit ambiguous in ${abs}`);
    }
    this.files.set(abs, current.replace(oldStr, newStr));
  }

  /**
   * Return all paths matching a simple glob (`*` + `**`). Implemented as a
   * regex translation, not full POSIX glob semantics.
   */
  glob(pattern: string): string[] {
    const absPattern = resolvePath(pattern);
    const rx = new RegExp(
      "^" +
        absPattern
          .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
          .replace(/\*\*/g, "::DOUBLESTAR::")
          .replace(/\*/g, "[^/]*")
          .replace(/::DOUBLESTAR::/g, ".*") +
        "$",
    );
    return [...this.files.keys()].filter((k) => rx.test(k)).sort();
  }

  /**
   * Return `{path, line, text}` records for each line that matches the regex
   * in any file under `root` (default `/workspace`). Mirrors the contract of
   * the `grep` tool variant.
   */
  grep(pattern: string, root: string = ROOT): Array<{ path: string; line: number; text: string }> {
    const absRoot = resolvePath(root);
    const rx = new RegExp(pattern);
    const out: Array<{ path: string; line: number; text: string }> = [];
    for (const [k, v] of this.files) {
      if (!k.startsWith(absRoot)) continue;
      const lines = v.split("\n");
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i] ?? "";
        if (rx.test(line)) out.push({ path: k, line: i + 1, text: line });
      }
    }
    return out;
  }

  /** Number of stored files. */
  size(): number {
    return this.files.size;
  }
}
