// Uses a temporary native WebView debug session. Never prints bearer tokens or saved keys.
// --live performs at most one short connection test per provider (Ollama and AIRouter).
const targets = await fetch("http://127.0.0.1:9222/json/list").then(r => r.json());
const target = targets.find(t => t.url === "http://localhost:1420/");
if (!target) throw Error("Open a development Mavo window with temporary WebView debugging first");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
const live = process.argv.includes("--live");
try {
  const result = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Model checks exceeded their time limit")), 285_000);
    socket.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      if (message.id !== 1) return;
      clearTimeout(timer);
      if (message.error || message.result?.exceptionDetails) reject(Error("Model checks failed in the app"));
      else resolve(message.result.result.value);
    };
  });
  socket.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: {
    awaitPromise: true, returnByValue: true,
    expression: `(async () => {
      const {api} = await import('/src/lib/api.ts');
      const routes = await api.routes();
      const report = [];
      for (const provider of ['ollama', 'airouter']) {
        const route = routes.find(r => r.provider === provider);
        if (!route) { report.push({provider, configured: false, tested: false}); continue; }
        if (!${JSON.stringify(live)}) {
          report.push({provider, configured: route.has_credential, model: route.model, tested: false}); continue;
        }
        try {
          const result = await api.testRoute(route.id);
          report.push({provider, model: route.model, tested: true, ok: !!result.response.trim(), latency_ms: result.latency_ms});
        } catch { report.push({provider, model: route.model, tested: true, ok: false}); }
      }
      return report;
    })()`,
  }}));
  console.log(JSON.stringify(await result));
} finally { socket.close(); }
