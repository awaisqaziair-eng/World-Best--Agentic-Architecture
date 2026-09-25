import { JSDOM } from "jsdom";
import fs from "fs";
const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.window = dom.window; globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;
const { default: mermaid } = await import("mermaid");
mermaid.initialize({ startOnLoad: false });
const diagrams = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
let bad = 0;
for (const d of diagrams) {
  try { await mermaid.parse(d.code); console.log(`ok   ${d.file} #${d.n}`); }
  catch (e) { bad++; console.log(`FAIL ${d.file} #${d.n}: ${String(e.message || e).split("\n").slice(0,3).join(" | ")}`); }
}
console.log(`${diagrams.length - bad}/${diagrams.length} diagrams parse`);
