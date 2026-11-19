const $ = (id) => document.getElementById(id)
const API = import.meta.env.VITE_API_URL ?? '' // '' = same origin (Caddy in prod, vite proxy in dev)
const log = $('log'), q = $('q'), jwt = $('jwt'), settings = $('settings'), msg = $('msg')
const store = { // localStorage can throw (private mode); never let that break the page
  get: (k) => { try { return localStorage.getItem(k) ?? '' } catch { return '' } },
  set: (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k) } catch {} },
}
let messages = [] // stateless API: send full history each time
let busy = false

const SUGGESTIONS = ['How were my sales this month?', 'Who owes me money?', 'Show bookings by source (online, agent, offline)']
const ERRORS = {
  401: 'Your access token is missing, invalid or expired. Paste a fresh one in Settings.',
  403: "This account's role is not allowed to use the assistant.",
  409: 'No OkGhumo account is connected yet. Open Settings and click Connect.',
  502: 'OkGhumo sign-in failed (the stored token may have expired). Open Settings and Connect again.',
}

const refreshStatus = () => {
  const on = !!jwt.value.trim()
  $('status').textContent = on ? 'Token saved' : 'Not signed in'
  $('status').className = 'pill' + (on ? ' ok' : '')
}
const flash = (text, bad) => { msg.textContent = text; msg.style.color = bad ? 'var(--err)' : 'var(--ok)' }

const empty = () => {
  log.innerHTML = ''
  const d = document.createElement('div')
  d.className = 'empty'
  d.innerHTML = '<h2>How can I help?</h2><div>Ask about your sales, bookings and collections.</div><div class="chips"></div>'
  for (const s of SUGGESTIONS) {
    const b = document.createElement('button')
    b.type = 'button'; b.textContent = s; b.onclick = () => send(s)
    d.querySelector('.chips').append(b)
  }
  log.append(d)
}

const add = (role, text, tools = [], error = false) => {
  log.querySelector('.empty')?.remove()
  const d = document.createElement('div')
  d.className = `m ${role}${error ? ' error' : ''}`
  d.textContent = text // textContent: model/tool output is untrusted, never innerHTML
  if (tools.length) {
    const t = document.createElement('div')
    t.className = 'tools'
    for (const n of new Set(tools)) t.append(Object.assign(document.createElement('span'), { textContent: n }))
    d.append(t)
  }
  log.append(d)
  log.scrollTop = log.scrollHeight
  return d
}

const typing = () => {
  const d = document.createElement('div')
  d.className = 'm assistant typing'
  d.innerHTML = '<i></i><i></i><i></i>'
  log.append(d)
  log.scrollTop = log.scrollHeight
  return d
}

const setBusy = (b) => { busy = b; $('send').disabled = b }

async function send(text) {
  text = text.trim()
  if (!text || busy) return
  if (!jwt.value.trim()) { settings.classList.add('open'); flash('Paste your access token first.', true); return }
  q.value = ''; q.style.height = 'auto'
  messages.push({ role: 'user', content: text })
  add('user', text)
  setBusy(true)
  const t = typing()
  try {
    const r = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwt.value.trim()}` },
      body: JSON.stringify({ messages }),
    })
    if (!r.ok) throw new Error(ERRORS[r.status] ?? `Request failed (HTTP ${r.status})`)
    const { reply, tools_used } = await r.json()
    messages.push({ role: 'assistant', content: reply })
    t.remove()
    add('assistant', reply || '(no answer)', tools_used)
  } catch (err) {
    t.remove()
    messages.pop() // drop the unanswered user turn so a retry doesn't send a dangling message
    add('assistant', err.message === 'Failed to fetch' ? 'Could not reach the server.' : err.message, [], true)
  } finally {
    setBusy(false)
    q.focus()
  }
}

$('f').onsubmit = (e) => { e.preventDefault(); send(q.value) }
q.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(q.value) } }
q.oninput = () => { q.style.height = 'auto'; q.style.height = q.scrollHeight + 'px' }
$('newchat').onclick = () => { messages = []; empty(); q.focus() }
$('gear').onclick = () => settings.classList.toggle('open')
jwt.oninput = () => { store.set('jwt', jwt.value.trim()); refreshStatus() }
$('clearjwt').onclick = () => { jwt.value = ''; store.set('jwt', ''); refreshStatus(); flash('Token forgotten.') }

$('connect').onclick = async () => {
  const token = $('okg').value.trim()
  if (!jwt.value.trim()) return flash('Paste your access token first.', true)
  if (!token) return flash('Paste the OkGhumo login token.', true)
  flash('Connecting…')
  try {
    const r = await fetch(`${API}/credentials`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwt.value.trim()}` },
      body: JSON.stringify({ token }),
    })
    if (r.status === 204) { $('okg').value = ''; flash('Connected. You can start asking questions.'); return }
    flash(r.status === 400 ? 'OkGhumo rejected that token.' : ERRORS[r.status] ?? `Failed (HTTP ${r.status})`, true)
  } catch { flash('Could not reach the server.', true) }
}

jwt.value = store.get('jwt')
refreshStatus()
empty()
