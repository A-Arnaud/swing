// Optional SDK transport, not installed or invoked by the prototype.
// Run only inside an independently isolated environment after setting budgets.
import { Codex } from "@openai/codex-sdk";
import { readFile } from "node:fs/promises";

const [requestPath] = process.argv.slice(2);
const request = JSON.parse(await readFile(requestPath, "utf8"));
if (request.authorized !== true || !request.workingDirectory || request.timeoutMs > 600000) {
  throw new Error("authorized bounded request required");
}
const codex = new Codex({ codexPathOverride: request.codexExecutable });
const options = {
  workingDirectory: request.workingDirectory,
  sandboxMode: request.readonly ? "read-only" : "workspace-write",
  approvalPolicy: "never",
  networkAccessEnabled: false,
  webSearchMode: "disabled",
};
// Fresh threads by default; saved IDs support deliberate recovery, not blind retries.
const thread = request.threadId ? codex.resumeThread(request.threadId, options) : codex.startThread(options);
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), request.timeoutMs || 60000);
try {
  const { events } = await thread.runStreamed(request.prompt, {
    signal: controller.signal,
    ...(request.outputSchema ? { outputSchema: request.outputSchema } : {}),
  });
  for await (const event of events) {
    // Suppress tool outputs, prompts and provider payloads in this example adapter.
    if (event.type === "thread.started" || event.type === "turn.completed" || event.type === "turn.failed") {
      process.stdout.write(JSON.stringify(event) + "\n");
    }
    if (event.type === "item.completed" && event.item.type === "agent_message") {
      process.stdout.write(JSON.stringify({ type: "result", text: event.item.text }) + "\n");
    }
  }
} finally {
  clearTimeout(timer);
}
