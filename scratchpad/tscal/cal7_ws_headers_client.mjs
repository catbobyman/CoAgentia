// cal7: does node native WebSocket (undici) support custom headers?
const ws = new WebSocket("ws://127.0.0.1:8921/", { headers: { Authorization: "Bearer cal7-key" } });
ws.onmessage = (ev) => { console.log("GOT:", ev.data); process.exit(0); };
ws.onerror = () => { console.log("ERROR: connect failed"); process.exit(1); };
setTimeout(() => { console.log("TIMEOUT"); process.exit(2); }, 5000);
