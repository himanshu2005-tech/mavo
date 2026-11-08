// Read-only check of the real native window. Requires temporary local WebView debugging.
const targets = await fetch('http://127.0.0.1:9222/json/list').then(r => r.json());
const target = targets.find(t => t.url === 'http://localhost:1420/');
if (!target) throw Error('Mavo window unavailable');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
try {
  const result = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('Startup check timed out')), 15_000);
    socket.onmessage = ({data}) => {
      const message = JSON.parse(data);
      if (message.id !== 1) return;
      clearTimeout(timer);
      if (message.error || message.result?.exceptionDetails) reject(Error('Startup inspection failed'));
      else resolve(message.result.result.value);
    };
  });
  socket.send(JSON.stringify({id: 1, method: 'Runtime.evaluate', params: {
    returnByValue: true, expression: `(() => ({
      login: !![...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Sign in'),
      workspace: !!document.querySelector('.graph-canvas'),
      rootEmpty: !document.getElementById('root')?.textContent.trim(),
      startupFallback: !!document.getElementById('mavo-startup')
    }))()`,
  }}));
  const report = await result;
  console.log(JSON.stringify(report));
  if (report.rootEmpty || report.startupFallback || (!report.login && !report.workspace)) {
    throw Error('The native app did not finish loading');
  }
} finally { socket.close(); }
