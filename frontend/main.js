const jwt = document.getElementById('jwt')
jwt.value = localStorage.getItem('jwt') ?? ''
jwt.oninput = () => localStorage.setItem('jwt', jwt.value)
const log = document.getElementById('log'), q = document.getElementById('q')
const API = import.meta.env.VITE_API_URL ?? '' // '' = same origin (dev proxy)
const messages = [] // stateless API: send full history each time

const add = (role, text, tools = []) => {
  const d = document.createElement('div')
  d.className = `m ${role}`
  d.textContent = text
  if (tools.length) d.insertAdjacentHTML('beforeend', `<small>tools: ${tools.join(', ')}</small>`)
  log.append(d)
  log.scrollTop = log.scrollHeight
}

document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault()
  const text = q.value.trim()
  if (!text) return
  q.value = ''
  messages.push({ role: 'user', content: text })
  add('user', text)
  try {
    const r = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwt.value.trim()}` },
      body: JSON.stringify({ messages }),
    })
    if (!r.ok) throw new Error(r.status === 401 ? 'invalid or missing JWT' : `HTTP ${r.status}`)
    const { reply, tools_used } = await r.json()
    messages.push({ role: 'assistant', content: reply })
    add('assistant', reply, tools_used)
  } catch (err) {
    add('assistant', `Error: ${err.message}`)
  }
}
