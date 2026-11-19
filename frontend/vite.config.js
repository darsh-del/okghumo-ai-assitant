// Dev: /chat is proxied to the local FastAPI (uvicorn app.main:app). Prod: set VITE_API_URL at build time.
export default { server: { proxy: { '/chat': 'http://localhost:8000' } } }
