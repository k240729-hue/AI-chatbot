/* ── Department class helper ────────────────────────────────────────────── */
function deptClass(dept) {
  if (!dept) return 'default';
  const d = dept.toLowerCase();
  if (d.includes('cardio'))   return 'cardiology';
  if (d.includes('neuro'))    return 'neurology';
  if (d.includes('ortho'))    return 'orthopedics';
  if (d.includes('general') || d.includes('medicine')) return 'general';
  if (d.includes('pediatr') || d.includes('paediatr')) return 'pediatrics';
  if (d.includes('derma'))    return 'dermatology';
  return 'default';
}

function initials(name) {
  return name.replace(/^Dr\.?\s*/i, '')
             .split(' ').slice(0, 2)
             .map(w => w[0]).join('').toUpperCase();
}

/* ── Live clock ─────────────────────────────────────────────────────────── */
function updateClock() {
  const el = document.getElementById('topbarTime');
  if (!el) return;
  el.textContent = new Date().toLocaleTimeString('en-GB',
    {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}
setInterval(updateClock, 1000);
updateClock();

/* ── DB status + badge ──────────────────────────────────────────────────── */
async function updateGlobalStats() {
  try {
    const stats = await fetch('/api/stats').then(r => r.json());
    if (stats.error) throw new Error(stats.error);
    const badge = document.getElementById('sideApptBadge');
    if (badge) {
      badge.textContent   = stats.total_appointments;
      badge.style.display = stats.total_appointments > 0 ? 'inline' : 'none';
    }
    const dot  = document.getElementById('dbStatusDot');
    const text = document.getElementById('dbStatusText');
    if (dot)  dot.classList.add('online');
    if (text) text.textContent = 'MongoDB Online';
  } catch(e) {
    const dot  = document.getElementById('dbStatusDot');
    const text = document.getElementById('dbStatusText');
    if (dot)  dot.classList.remove('online');
    if (text) text.textContent = 'DB Offline';
  }
}
updateGlobalStats();
setInterval(updateGlobalStats, 10000);

/* ═══════════════════════════════════════════════════════════════════════════
   FLOATING CHAT WIDGET (patient only; only on non-chat pages)
   ═══════════════════════════════════════════════════════════════════════════ */

let fabOpen     = false;
let fabWaiting  = false;
let fabPrimed   = false;

function openFab() {
  const panel = document.getElementById('fabPanel');
  if (!panel) return;
  fabOpen = true;
  panel.style.display = 'flex';
  document.getElementById('fabBtn').style.display = 'none';
  if (!fabPrimed) {
    fabPrimed = true;
    primeFab();
  }
  document.getElementById('fabInput').focus();
}

function closeFab() {
  const panel = document.getElementById('fabPanel');
  if (!panel) return;
  fabOpen = false;
  panel.style.display = 'none';
  document.getElementById('fabBtn').style.display = 'flex';
}

async function primeFab() {
  try {
    const data = await fetch('/api/chat-state').then(r => r.json());
    const box  = document.getElementById('fabMessages');
    if (!box) return;
    box.innerHTML = '';
    if (data.history && data.history.length > 0) {
      data.history.slice(-6).forEach(m =>
        fabAppendMsg(m.role === 'user' ? 'user' : 'bot', m.content));
    } else if (data.patient) {
      const first = data.patient.name.split(' ')[0];
      fabAppendMsg('bot',
        `Hi ${first}! I'm MedAssist. Try: 'Book a cardiologist tomorrow morning' or 'Show my appointments'.`);
    }
  } catch(e) {}
}

function fabQuick(text) {
  document.getElementById('fabInput').value = text;
  sendFabMessage();
}

async function sendFabMessage() {
  if (fabWaiting) return;
  const input = document.getElementById('fabInput');
  const text  = input.value.trim();
  if (!text) return;
  input.value = '';
  input.style.height = 'auto';
  await sendFabMsg(text);
}

function fabAppendMsg(role, text) {
  const box = document.getElementById('fabMessages');
  if (!box) return;
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  const b = document.createElement('div');
  b.className = 'msg-bubble';
  b.innerText = text;
  const ts = document.createElement('span');
  ts.className = 'msg-ts';
  const now = new Date();
  ts.innerText = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
  div.appendChild(b);
  div.appendChild(ts);
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function showFabTyping() {
  const box = document.getElementById('fabMessages');
  if (!box) return;
  const el  = document.createElement('div');
  el.className = 'msg bot';
  el.id = 'fabTyping';
  el.innerHTML = '<div class="msg-bubble typing-bubble"><span></span><span></span><span></span></div>';
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}
function hideFabTyping() {
  const el = document.getElementById('fabTyping');
  if (el) el.remove();
}

async function sendFabMsg(text) {
  fabWaiting = true;
  const btn = document.getElementById('fabSendBtn');
  if (btn) btn.disabled = true;
  fabAppendMsg('user', text);
  showFabTyping();

  try {
    const res  = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });
    const data = await res.json();
    hideFabTyping();
    if (data.reply) fabAppendMsg('bot', data.reply);
    if (data.redirect) window.location.href = data.redirect;
  } catch(e) {
    hideFabTyping();
    fabAppendMsg('bot', 'Connection error. Try again.');
  }

  fabWaiting = false;
  if (btn) btn.disabled = false;
}

async function resetFabChat() {
  const data = await fetch('/reset', {method: 'POST'}).then(r => r.json());
  const box  = document.getElementById('fabMessages');
  if (box) box.innerHTML = '';
  fabAppendMsg('bot', data.reply);
}

document.addEventListener('DOMContentLoaded', () => {
  const panel = document.getElementById('fabPanel');
  if (panel) panel.style.display = 'none';
});
