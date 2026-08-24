import { createHash } from "node:crypto";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";

const release = new URL("../release/", import.meta.url);
const names = (await readdir(release)).filter((name) => /\.(dmg|zip)$/i.test(name)).sort();
if (names.length === 0) throw new Error("No DMG or ZIP artifacts found");
const artifacts = [];
for (const name of names) {
  const path = join(release.pathname, name);
  const bytes = await readFile(path);
  artifacts.push({ name, bytes: (await stat(path)).size, sha256: createHash("sha256").update(bytes).digest("hex") });
}
await writeFile(new URL("../release/RELEASE-MANIFEST.json", import.meta.url), `${JSON.stringify({ schema: 1, artifacts }, null, 2)}\n`);
