// Dev: API calls are proxied to the local FastAPI (uvicorn app.main:app). Prod: Caddy serves this and proxies the same paths.
const api = 'http://localhost:8000'
export default { server: { proxy: Object.fromEntries(['/chat', '/credentials', '/tasks', '/inbox', '/health'].map((p) => [p, api])) } }
