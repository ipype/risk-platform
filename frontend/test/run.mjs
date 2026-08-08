/**
 * The frontend test runner, such as it is.
 *
 * The repo has shipped a dozen deliveries verified only by `tsc --noEmit` and `vite
 * build`, which prove that the components compile and prove nothing about what they draw.
 * A component can typecheck perfectly and still divide by zero on a degenerate series,
 * drop a risk from a chart because a nullable field was read as falsy, or render an SVG
 * `<title>` that browsers display as raw markup — all three of which have happened here.
 *
 * The obvious fix is Vitest, and it was not taken. Vitest plus jsdom plus Testing Library
 * is four devDependencies and a config file to run assertions against strings, and this
 * project's whole charting layer is hand-rolled SVG whose output *is* a string. So:
 * `react-dom/server` renders it, esbuild compiles the TSX, and both of those are already
 * installed — esbuild as Vite's own bundler, react-dom as a runtime dependency. Zero new
 * packages, one file, no config.
 *
 * What this deliberately cannot do: fire events, run effects, or assert on layout. It
 * covers first render of pure presentational components. The day something needs a click,
 * that is the day the Vitest argument becomes worth having, and this file should lose
 * rather than grow a synthetic event system.
 *
 * Run with `npm test`. Every `*.test.tsx` in this directory is bundled and executed; a
 * non-zero exit from any of them fails the run.
 */

import { build } from "esbuild";
import { mkdirSync, readdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const here = dirname(new URL(import.meta.url).pathname);
const files = readdirSync(here)
  .filter((f) => f.endsWith(".test.tsx"))
  .sort();

if (files.length === 0) {
  console.error("No *.test.tsx found in frontend/test.");
  process.exit(1);
}

// Inside `node_modules`, not the system temp directory: `react` and `react-dom` are left
// external, and Node resolves an external from where the bundle *sits*. A bundle in /tmp
// cannot see them. `node_modules` is already ignored, so nothing leaks into the tree.
const out = resolve(here, "..", "node_modules", ".cache", "risk-fe-test");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
let failed = 0;

for (const file of files) {
  const bundle = join(out, file.replace(/\.tsx$/, ".mjs"));
  await build({
    entryPoints: [resolve(here, file)],
    bundle: true,
    format: "esm",
    platform: "node",
    jsx: "automatic",
    outfile: bundle,
    // The components read deployment config off `import.meta.env`, which exists only under
    // Vite. An empty object lets every `?? default` in `config.ts` take its default, which
    // is exactly what the tests want to assert against.
    define: { "import.meta.env": "{}" },
    external: ["react", "react-dom", "react-dom/server"],
    logLevel: "silent",
  });

  const mod = await import(pathToFileURL(bundle).href);
  const result = await mod.default();
  for (const line of result.lines) console.log(line);
  failed += result.failed;
}

rmSync(out, { recursive: true, force: true });

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
