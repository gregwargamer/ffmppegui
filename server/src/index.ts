import WebSocket from 'ws';
import os from 'os';
import fs from 'fs';
import path from 'path';
import { spawn } from 'child_process';
import axios from 'axios';
import pino from 'pino';

//this part do that
//initialisation du logger vers logs/agent.log
try { fs.mkdirSync(path.join(process.cwd(), 'logs'), { recursive: true }); } catch {}
const agentLogFile = path.join(process.cwd(), 'logs', 'agent.log');
const logger = pino({ level: 'debug' }, pino.destination({ dest: agentLogFile, sync: false }));

const CONTROLLER_URL = process.env.CONTROLLER_URL || 'http://localhost:4000';
const CONTROLLER_WS = CONTROLLER_URL.replace(/^http/, 'ws');
const AGENT_TOKEN = process.env.AGENT_TOKEN || 'dev-token';
const CONCURRENCY = Number(process.env.CONCURRENCY || os.cpus().length);
const FFMPEG_PATH = process.env.FFMPEG_PATH || 'ffmpeg';

const agentId = `${os.hostname()}-${process.pid}`;
let activeJobs = 0;
let lastHeartbeatAt = Date.now();

async function execCapture(cmd: string, args: string[]): Promise<string> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'ignore'] });
    let out = '';
    child.stdout.on('data', (d) => (out += d.toString()));
    child.on('close', () => resolve(out));
  });
}

async function detectEncoders(): Promise<string[]> {
  try {
    logger.debug({ FFMPEG_PATH }, 'detectEncoders start');
    const out = await execCapture(FFMPEG_PATH, ['-hide_banner', '-encoders']);
    const lines = out.split(/\r?\n/);
    const enc: string[] = [];
    for (const line of lines) {
      const m = line.match(/^[\s\.A-Z]+\s+([a-z0-9_\-]+)\s+/i);
      if (m) enc.push(m[1]);
    }
    logger.debug({ count: enc.length }, 'detectEncoders done');
    return enc;
  } catch {
    logger.warn('detectEncoders failed');
    return [];
  }
}

logger.info({ CONTROLLER_URL, CONCURRENCY, FFMPEG_PATH }, 'agent starting');
const ws = new WebSocket(`${CONTROLLER_WS}/agent?token=${encodeURIComponent(AGENT_TOKEN)}`);

ws.on('open', async () => {
  logger.info('websocket open');
  const encoders = await detectEncoders();
  ws.send(JSON.stringify({ type: 'register', payload: { id: agentId, name: os.hostname(), concurrency: CONCURRENCY, encoders, token: AGENT_TOKEN } }));
  logger.debug({ agentId, encodersCount: encoders.length }, 'register sent');
});

ws.on('error', (err) => { logger.error({ err: String(err) }, 'websocket error'); });

ws.on('message', async (data) => {
  try {
    const msg = JSON.parse(data.toString());
    if (msg.type === 'lease') {
      const p = msg.payload || {};
      if (activeJobs >= CONCURRENCY) return;
      logger.debug({ jobId: p.jobId }, 'lease received');
      handleLease(p).catch(() => {});
    }
  } catch {}
});

ws.on('close', () => { logger.info('websocket closed'); process.exit(0); });

setInterval(() => {
  try {
    lastHeartbeatAt = Date.now();
    const memTotal = os.totalmem();
    const memFree = os.freemem();
    const memUsed = memTotal - memFree;
    const load = os.loadavg()[0] || 0;
    ws.send(JSON.stringify({ type: 'heartbeat', payload: { id: agentId, activeJobs, cpu: load, memUsed, memTotal } }));
  } catch {}
}, 10000);

async function handleLease(p: any) {
  activeJobs += 1;
  try {
    const jobId: string = p.jobId;
    const inputUrl: string = p.inputUrl;
    const outputUrl: string = p.outputUrl;
    const ffmpegArgs: string[] = Array.isArray(p.ffmpegArgs) ? p.ffmpegArgs : [];
    const outputExt: string = p.outputExt || '.out';

    const tmpDir = path.join(os.tmpdir(), 'ffmpegeasy');
    await fs.promises.mkdir(tmpDir, { recursive: true });
    const tmpOut = path.join(tmpDir, `${jobId}${outputExt}`);

  const args = ['-i', inputUrl, ...ffmpegArgs, tmpOut];
    logger.info({ jobId, args }, 'ffmpeg start');
  const child = spawn(FFMPEG_PATH, args, { stdio: ['ignore', 'pipe', 'pipe'] });

    child.stdout.on('data', (d) => {
      const text = d.toString();
      const lines = text.split(/\r?\n/);
      const payload: any = { jobId };
      for (const line of lines) { const [k, v] = line.split('='); if (k && v) payload[k.trim()] = v.trim(); }
      try { ws.send(JSON.stringify({ type: 'progress', payload })); } catch {}
    });

  //timeout de job basique (30 min)
  const rc: number = await new Promise((resolve) => {
    let done = false;
    const timer = setTimeout(() => { if (!done) { try { child.kill('SIGKILL'); } catch {} resolve(124); } }, 30 * 60 * 1000);
    child.on('close', (code) => { done = true; clearTimeout(timer); resolve(code ?? 1); });
  });
    if (rc !== 0) { logger.warn({ jobId, rc }, 'ffmpeg failed'); try { ws.send(JSON.stringify({ type: 'complete', payload: { jobId, agentId, success: false } })); } catch {} throw new Error(`ffmpeg failed rc=${rc}`); }

    const stat = await fs.promises.stat(tmpOut);
    logger.debug({ jobId, size: stat.size }, 'upload begin');
  //upload avec retries
  {
    const maxRetries = 3;
    let attempt = 0; let uploaded = false;
    while (attempt < maxRetries && !uploaded) {
      attempt += 1;
      try {
        const stream = fs.createReadStream(tmpOut);
        const r = await axios.put(outputUrl, stream, { headers: { 'Content-Length': String(stat.size) }, maxContentLength: Infinity, maxBodyLength: Infinity, timeout: 120000, validateStatus: () => true });
        if (r.status >= 200 && r.status < 300) { uploaded = true; break; }
        logger.warn({ attempt, status: r.status }, 'upload failed');
      } catch (e) {
        logger.warn({ attempt, err: String(e) }, 'upload error');
      }
      await new Promise(res => setTimeout(res, 2000));
    }
    if (!uploaded) throw new Error('upload failed after retries');
  }

    logger.info({ jobId }, 'upload done');
    try { ws.send(JSON.stringify({ type: 'complete', payload: { jobId, agentId, success: true } })); } catch {}
  } catch {} finally {
    try {
      const p = path.join(os.tmpdir(), 'ffmpegeasy');
      const files = await fs.promises.readdir(p);
      for (const f of files) { try { await fs.promises.unlink(path.join(p, f)); } catch {} }
    } catch {}
    logger.debug('cleanup tmp done');
    activeJobs -= 1;
  }
}
