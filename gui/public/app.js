const el = (id) => document.getElementById(id);

function fillCodecs() {
  const media = el('mediaType').value;
  const codec = el('codec');
  codec.innerHTML = '';
  const add = (v) => { const o = document.createElement('option'); o.value = v; o.textContent = v; codec.appendChild(o); };
  if (media === 'audio') ['flac','alac','aac','mp3','opus','ogg'].forEach(add);
  else if (media === 'video') ['h264','h265','av1','vp9'].forEach(add);
  else ['avif','heic','webp','png','jpeg'].forEach(add);
  updateFields();
}

function updateFields() {
  const media = el('mediaType').value;
  const c = el('codec').value;
  const showPreset = media === 'video';
  const showCrf = media === 'video' || (media === 'image' && (c === 'avif' || c === 'webp'));
  const showBitrate = media === 'audio' && (c === 'aac' || c === 'mp3' || c === 'opus');
  el('rowPreset').style.display = showPreset ? '' : 'none';
  el('rowCrf').style.display = showCrf ? '' : 'none';
  el('rowBitrate').style.display = showBitrate ? '' : 'none';
}

async function pair() {
  const token = el('pairToken').value.trim();
  if (token.length !== 25) { alert('token must be 25 chars'); return; }
  const r = await fetch('/api/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
  if (!r.ok) { alert('pair failed'); return; }
  alert('paired');
}

async function pick(where) {
  const r = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'dir' })});
  if (!r.ok) return;
  const j = await r.json();
  if (Array.isArray(j.paths) && j.paths.length > 0) el(where).value = j.paths[0];
}

async function scan() {
  const body = {
    inputRoot: el('inputRoot').value.trim(),
    outputRoot: el('outputRoot').value.trim(),
    recursive: el('recursive').checked,
    mirrorStructure: el('mirror').checked,
    mediaType: el('mediaType').value,
    codec: el('codec').value,
    options: { crf: Number(el('crf').value||'23'), preset: el('preset').value, bitrate: el('bitrate').value.trim() }
  };
  const r = await fetch('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) { alert('scan failed'); return; }
  const j = await r.json();
  el('scanSummary').textContent = `files=${j.count} bytes=${j.totalBytes}`;
  el('btnStart').disabled = false;
}

async function start() {
  const s = el('scanSummary').textContent;
  if (!s) { alert('scan first'); return; }
  // placeholder: front-end demo; real jobs would be remembered client-side
  alert('Start pressed (submit previously planned jobs)');
}

async function refreshNodes() {
  try {
    const r = await fetch('/api/nodes'); if (!r.ok) return; const j = await r.json();
    el('nodes').textContent = JSON.stringify(j, null, 2);
  } catch {}
}

el('mediaType').addEventListener('change', fillCodecs);
el('codec').addEventListener('change', updateFields);
el('btnPair').addEventListener('click', pair);
el('pickInput').addEventListener('click', () => pick('inputRoot'));
el('pickOutput').addEventListener('click', () => pick('outputRoot'));
el('btnScan').addEventListener('click', scan);
el('btnStart').addEventListener('click', start);

fillCodecs();
setInterval(refreshNodes, 1500);
const mediaTypeEl = document.getElementById('mediaType');
const codecEl = document.getElementById('codec');
const inputRootEl = document.getElementById('inputRoot');
const outputRootEl = document.getElementById('outputRoot');
const recursiveEl = document.getElementById('recursive');
const mirrorEl = document.getElementById('mirror');
const crfEl = document.getElementById('crf');
const bitrateEl = document.getElementById('bitrate');
const presetEl = document.getElementById('preset');
const scanBtn = document.getElementById('scanBtn');
const startBtn = document.getElementById('startBtn');
const scanSummary = document.getElementById('scanSummary');
const nodesDiv = document.getElementById('nodes');
const pairBtn = document.getElementById('pairBtn');
const pairTokenEl = document.getElementById('pairToken');
const publicUrlEl = document.getElementById('publicUrl');
const pickInputBtn = document.getElementById('pickInput');
const pickOutputBtn = document.getElementById('pickOutput');
const uploadLocalBtn = document.getElementById('uploadLocal');

let lastPlan = null;

const codecChoices = { audio: [ { value: 'flac', label: 'FLAC' }, { value: 'alac', label: 'ALAC (m4a)' }, { value: 'aac', label: 'AAC (m4a)' }, { value: 'mp3', label: 'MP3' }, { value: 'opus', label: 'Opus' }, { value: 'ogg', label: 'Vorbis (ogg)' } ], video: [ { value: 'h264', label: 'H.264 (x264)' }, { value: 'h265', label: 'H.265/HEVC (x265)' }, { value: 'av1', label: 'AV1 (SVT-AV1)' }, { value: 'vp9', label: 'VP9' } ], image: [ { value: 'avif', label: 'AVIF' }, { value: 'heic', label: 'HEIC' }, { value: 'webp', label: 'WebP' }, { value: 'png', label: 'PNG' }, { value: 'jpeg', label: 'JPEG' } ] };

function saveLocal(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch {} }
function loadLocal(key, def) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : def; } catch { return def; } }

async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    if (res.ok && data.publicBaseUrl) publicUrlEl.value = data.publicBaseUrl;
  } catch {}
  inputRootEl.value = loadLocal('inputRoot', inputRootEl.value || '');
  outputRootEl.value = loadLocal('outputRoot', outputRootEl.value || '');
}

function fillCodecs() {
  const type = mediaTypeEl.value;
  codecEl.innerHTML = '';
  codecChoices[type].forEach(c => {
    const opt = document.createElement('option'); opt.value = c.value; opt.textContent = c.label; codecEl.appendChild(opt);
  });
  updateUiFields();
}

function updateUiFields() {
  const type = mediaTypeEl.value;
  const codec = codecEl.value;
  const showPreset = (type === 'video');
  const showCrf = (type === 'video') || (type === 'image' && (codec === 'avif' || codec === 'webp'));
  const showBitrate = (type === 'audio' && (codec === 'aac' || codec === 'mp3' || codec === 'opus'));
  document.getElementById('presetRow').style.display = showPreset ? '' : 'none';
  document.getElementById('qualityRow').style.display = showCrf ? '' : 'none';
  document.getElementById('bitrateRow').style.display = showBitrate ? '' : 'none';
}

mediaTypeEl.addEventListener('change', fillCodecs);
codecEl.addEventListener('change', updateUiFields);

fillCodecs();
loadSettings();

pairBtn.addEventListener('click', async () => {
  const url = (publicUrlEl.value || '').trim();
  const token = (pairTokenEl.value || '').trim();
  if (!/^https?:\/\//.test(url)) { alert('Enter a valid URL'); return; }
  if (token.length !== 25) { alert('Token must be 25 characters'); return; }
  try {
    const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ publicBaseUrl: url }) });
    const sdata = await res.json(); if (!res.ok) { alert('Save URL failed: ' + (sdata.error || 'unknown')); return; }
    const res2 = await fetch('/api/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
    const data = await res2.json(); if (!res2.ok) { alert('Pair failed: ' + (data.error || 'unknown')); return; }
    alert('Paired and URL saved.');
  } catch { alert('Pair error'); }
});

pickInputBtn.addEventListener('click', async () => {
  try {
    const res = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'dir' }) });
    const data = await res.json(); if (res.ok && data.paths && data.paths[0]) { inputRootEl.value = data.paths[0]; saveLocal('inputRoot', inputRootEl.value); }
    else alert(data.error || 'No selection');
  } catch { alert('Picker error'); }
});

pickOutputBtn.addEventListener('click', async () => {
  try {
    const res = await fetch('/api/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'dir' }) });
    const data = await res.json(); if (res.ok && data.paths && data.paths[0]) { outputRootEl.value = data.paths[0]; saveLocal('outputRoot', outputRootEl.value); }
    else alert(data.error || 'No selection');
  } catch { alert('Picker error'); }
});

uploadLocalBtn.addEventListener('click', async () => {
  try {
    const inputRoot = (inputRootEl.value || '').trim();
    if (!inputRoot) { alert('Set the input root first'); return; }
    const picker = document.createElement('input'); picker.type = 'file'; picker.multiple = true; picker.style.display = 'none'; document.body.appendChild(picker);
    picker.addEventListener('change', async () => {
      if (!picker.files || picker.files.length === 0) { document.body.removeChild(picker); return; }
      const form = new FormData();
      form.append('dest', inputRoot);
      Array.from(picker.files).forEach(f => form.append('files', f, f.name));
      try {
        const res = await fetch('/api/upload', { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) alert('Upload failed: ' + (data.error || 'unknown')); else alert('Uploaded');
      } catch { alert('Upload error'); }
      document.body.removeChild(picker);
    });
    picker.click();
  } catch { alert('Upload error'); }
});

scanBtn.addEventListener('click', async () => {
  const payload = { inputRoot: inputRootEl.value, outputRoot: outputRootEl.value, recursive: recursiveEl.checked, mirrorStructure: mirrorEl.checked, mediaType: mediaTypeEl.value, codec: codecEl.value, options: { crf: parseInt(crfEl.value, 10), preset: presetEl.value, bitrate: bitrateEl.value } };
  scanSummary.textContent = 'Scanning...';
  try {
    const res = await fetch('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (res.ok) { lastPlan = data; startBtn.disabled = data.count === 0; scanSummary.textContent = `${data.count} files, total ${(data.totalBytes / (1024*1024)).toFixed(1)} MiB`; }
    else { startBtn.disabled = true; scanSummary.textContent = data.error || 'Scan failed'; }
  } catch { startBtn.disabled = true; scanSummary.textContent = 'Scan error'; }
});

startBtn.addEventListener('click', async () => {
  if (!lastPlan) return;
  startBtn.disabled = true;
  try {
    const res = await fetch('/api/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jobs: lastPlan.jobs }) });
    const data = await res.json(); if (!res.ok) alert('Start failed: ' + (data.error || 'unknown'));
  } catch { alert('Start error'); }
});

async function pollNodes() {
  try {
    const res = await fetch('/api/nodes');
    const data = await res.json();
    if (res.ok) {
      nodesDiv.innerHTML = '';
      const table = document.createElement('table');
      const head = document.createElement('tr'); head.innerHTML = '<th>Name</th><th>Concurrency</th><th>Active</th><th>Last heartbeat</th>'; table.appendChild(head);
      data.agents.forEach(a => { const tr = document.createElement('tr'); const hb = new Date(a.lastHeartbeat).toLocaleTimeString(); tr.innerHTML = `<td>${a.name}</td><td>${a.concurrency}</td><td>${a.activeJobs}</td><td>${hb}</td>`; table.appendChild(tr); });
      const sum = document.createElement('div'); sum.textContent = `Jobs: total=${data.totals.totalJobs}, pending=${data.totals.pendingJobs}, running=${data.totals.runningJobs}`;
      nodesDiv.appendChild(sum);
      nodesDiv.appendChild(table);
    }
  } catch {}
  setTimeout(pollNodes, 1500);
}

pollNodes();
