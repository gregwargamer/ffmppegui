import Fastify from 'fastify';
import websocket from '@fastify/websocket';
import fastifyStatic from '@fastify/static';
import fastifyMultipart from '@fastify/multipart';
import { randomBytes } from 'crypto';
import { promises as fsp } from 'fs';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { v4 as uuidv4 } from 'uuid';
import mime from 'mime-types';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';
import pino from 'pino';

// logs setup
const logsDir = path.join(process.cwd(), 'logs');
try { fs.mkdirSync(logsDir, { recursive: true }); } catch {}
const guiLogFile = path.join(logsDir, 'gui-server.log');
const logger = pino({ level: 'debug' }, pino.destination({ dest: guiLogFile, sync: false }));

// serveur HTTP principal (GUI + API + endpoints de flux)
const app = Fastify({ logger });

// configuration basique
const PORT = Number(process.env.PORT || 4000);
const HOST = process.env.HOST || '0.0.0.0';
const DEFAULT_PUBLIC_BASE_URL = process.env.PUBLIC_BASE_URL || `http://localhost:${PORT}`;
const AGENT_SHARED_TOKEN = process.env.AGENT_SHARED_TOKEN || 'dev-token';

// structures de données typées
export type MediaType = 'audio' | 'video' | 'image';

export interface ScanRequest {
  inputRoot: string;
  outputRoot: string;
  recursive: boolean;
  mirrorStructure: boolean;
  mediaType: MediaType;
  codec: string;
  options?: Record<string, any>;
}

export interface PlanJob {
  sourcePath: string;
  relativePath: string;
  mediaType: MediaType;
  sizeBytes: number;
  outputPath: string;
  codec: string;
  options?: Record<string, any>;
}

export interface Job extends PlanJob {
  id: string;
  status: 'pending' | 'assigned' | 'running' | 'uploaded' | 'completed' | 'failed';
  nodeId?: string;
  inputToken: string;
  outputToken: string;
  createdAt: number;
  updatedAt: number;
}

export interface AgentInfo {
  id: string;
  name: string;
  concurrency: number;
  encoders: string[];
  activeJobs: number;
  lastHeartbeat: number;
}

// registre des agents connectés
const agents = new Map<string, {
  info: AgentInfo;
  socket: any;
}>();

// file d'attente des jobs
const jobs = new Map<string, Job>();
const pendingJobs: Job[] = [];

//this part do that
//liste des tokens autorisés pour l'appairage (en mémoire pour v1)
const allowedTokens = new Set<string>([AGENT_SHARED_TOKEN]);

//this other part do that
//paramètre d'URL publique configurable pour les agents
let publicBaseUrl = DEFAULT_PUBLIC_BASE_URL;
function getPublicBaseUrl(): string { return publicBaseUrl; }

// utilitaires de chemin/FS
function isSubPath(parent: string, child: string): boolean {
  const rel = path.relative(parent, child);
  return !!rel && !rel.startsWith('..') && !path.isAbsolute(rel);
}

// mappage simple codec -> extension et générateur d'arguments ffmpeg
function computeOutputExt(mediaType: MediaType, codec: string): string {
  if (mediaType === 'audio') {
    const map: Record<string, string> = {
      flac: '.flac',
      alac: '.m4a',
      aac: '.m4a',
      mp3: '.mp3',
      opus: '.opus',
      ogg: '.ogg',
      vorbis: '.ogg'
    };
    return map[codec] || '.m4a';
  }
  if (mediaType === 'video') {
    const map: Record<string, string> = {
      h264: '.mp4',
      h265: '.mp4',
      hevc: '.mp4',
      av1: '.mkv',
      vp9: '.webm'
    };
    return map[codec] || '.mp4';
  }
  const map: Record<string, string> = {
    avif: '.avif',
    heic: '.heic',
    heif: '.heif',
    webp: '.webp',
    png: '.png',
    jpeg: '.jpg',
    jpg: '.jpg'
  };
  return map[codec] || '.png';
}

function buildFfmpegArgs(job: Job): { args: string[] } {
  const { mediaType, codec, options } = job;
  const args: string[] = [];

  args.push('-hide_banner');
  args.push('-nostdin');
  args.push('-y');
  args.push('-progress', 'pipe:1');
  args.push('-loglevel', 'error');

  if (mediaType === 'audio') {
    args.push('-vn');
    switch (codec) {
      case 'flac':
        args.push('-c:a', 'flac');
        break;
      case 'alac':
        args.push('-c:a', 'alac');
        break;
      case 'aac':
        args.push('-c:a', 'aac');
        args.push('-b:a', (options?.bitrate ?? '192k'));
        break;
      case 'mp3':
        args.push('-c:a', 'libmp3lame');
        args.push('-b:a', (options?.bitrate ?? '192k'));
        break;
      case 'opus':
        args.push('-c:a', 'libopus');
        args.push('-b:a', (options?.bitrate ?? '160k'));
        break;
      case 'ogg':
      case 'vorbis':
        args.push('-c:a', 'libvorbis');
        args.push('-q:a', String(options?.q ?? 5));
        break;
      default:
        args.push('-c:a', 'aac');
        args.push('-b:a', (options?.bitrate ?? '192k'));
    }
  } else if (mediaType === 'video') {
    args.push('-pix_fmt', 'yuv420p');
    const crf = options?.crf ?? (codec === 'h265' || codec === 'hevc' ? 28 : codec === 'av1' ? 32 : 23);
    const preset = options?.preset ?? 'medium';
    switch (codec) {
      case 'h264':
        args.push('-c:v', 'libx264', '-preset', String(preset), '-crf', String(crf));
        break;
      case 'h265':
      case 'hevc':
        args.push('-c:v', 'libx265', '-preset', String(preset), '-crf', String(crf));
        break;
      case 'av1':
        args.push('-c:v', 'libsvtav1', '-preset', String(options?.svtPreset ?? 6), '-crf', String(crf));
        break;
      case 'vp9':
        args.push('-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', String(crf), '-row-mt', '1');
        break;
      default:
        args.push('-c:v', 'libx264', '-preset', String(preset), '-crf', String(crf));
    }
    args.push('-c:a', options?.audioCopy === false ? 'aac' : 'copy');
    if (options?.audioCopy === false) {
      args.push('-b:a', (options?.audioBitrate ?? '160k'));
    }
  } else {
    switch (codec) {
      case 'avif':
        args.push('-c:v', 'libaom-av1', '-still-picture', '1', '-b:v', '0', '-crf', String(options?.crf ?? 28));
        break;
      case 'heic':
      case 'heif':
        args.push('-c:v', 'libx265');
        break;
      case 'webp':
        if (options?.lossless) {
          args.push('-c:v', 'libwebp', '-lossless', '1');
        } else {
          args.push('-c:v', 'libwebp', '-q:v', String(options?.quality ?? 80));
        }
        break;
      case 'png':
        args.push('-c:v', 'png');
        break;
      case 'jpeg':
      case 'jpg':
        args.push('-c:v', 'mjpeg', '-q:v', String(options?.quality ?? 2));
        break;
      default:
        args.push('-c:v', 'png');
    }
    args.push('-frames:v', '1');
  }

  return { args };
}

async function scanFiles(root: string, recursive: boolean, mediaType: MediaType): Promise<{ path: string; size: number; }[]> {
  const results: { path: string; size: number; }[] = [];
  const extsAudio = new Set(['.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg', '.opus', '.wma', '.aiff', '.alac']);
  const extsVideo = new Set(['.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v']);
  const extsImage = new Set(['.jpg', '.jpeg', '.png', '.webp', '.tiff', '.bmp', '.heic', '.heif', '.avif']);
  app.log.debug({ root, recursive, mediaType }, 'scanFiles start');
  async function walk(dir: string) {
    const entries = await fsp.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (recursive) {
          await walk(full);
        }
      } else if (entry.isFile()) {
        const ext = path.extname(entry.name).toLowerCase();
        if ((mediaType === 'audio' && extsAudio.has(ext)) || (mediaType === 'video' && extsVideo.has(ext)) || (mediaType === 'image' && extsImage.has(ext))) {
          const stat = await fsp.stat(full);
          results.push({ path: full, size: stat.size });
        }
      }
    }
  }

  await walk(root);
  app.log.debug({ count: results.length }, 'scanFiles done');
  return results;
}

//this part do that
//ouverture d'une boîte de dialogue native pour choisir un dossier/fichiers
async function pickPathNative(kind: 'dir' | 'files'): Promise<string[]> {
  return new Promise((resolve) => {
    if (process.platform === 'darwin') {
      const script = kind === 'dir'
        ? 'choose folder with prompt "Select a folder"\nPOSIX path of result'
        : 'choose file with prompt "Select file(s)" with multiple selections allowed\nset p to {}\nrepeat with f in result\nset end of p to POSIX path of f\nend repeat\nreturn p as string';
      const child = spawn('osascript', ['-e', script]);
      let out = '';
      child.stdout.on('data', (d) => out += d.toString());
      child.on('close', () => {
        const list = out.split(', ').map(s => s.trim()).filter(Boolean);
        resolve(list);
      });
      child.on('error', () => resolve([]));
      return;
    }
    if (process.platform === 'linux') {
      const args = kind === 'dir'
        ? ['--file-selection', '--directory']
        : ['--file-selection', '--multiple', '--separator=::'];
      const child = spawn('zenity', args);
      let out = '';
      child.stdout.on('data', (d) => out += d.toString());
      child.on('close', () => {
        const sep = kind === 'dir' ? '\n' : '::';
        const list = out.split(sep).map(s => s.trim()).filter(Boolean);
        resolve(list);
      });
      child.on('error', () => resolve([]));
      return;
    }
    resolve([]);
  });
}

function registerRoutes() {
  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const publicDir = path.join(__dirname, '..', 'public');

  app.register(websocket as any);
  app.register(fastifyStatic as any, { root: publicDir, prefix: '/' } as any);
  app.register(fastifyMultipart as any);

  app.get('/agent', { websocket: true } as any, (conn: any, req: any) => {
    app.log.info('agent websocket connected');
    conn.socket.on('message', (data: Buffer) => {
      try {
        const msg = JSON.parse(data.toString());
        app.log.debug({ msgType: msg?.type }, 'agent message');
        if (msg.type === 'register') {
          const tokenReceived: string | undefined = msg.payload?.token;
          if (!tokenReceived || !allowedTokens.has(tokenReceived)) {
            app.log.warn('unauthorized agent token');
            try { if (typeof (conn.socket as any).close === 'function') (conn.socket as any).close(1008, 'unauthorized'); } catch {}
            return;
          }
          const id = msg.payload?.id || uuidv4();
          const info: AgentInfo = {
            id,
            name: msg.payload?.name || `agent-${id.slice(0, 6)}`,
            concurrency: Math.max(1, Number(msg.payload?.concurrency || os.cpus().length)),
            encoders: Array.isArray(msg.payload?.encoders) ? msg.payload.encoders : [],
            activeJobs: 0,
            lastHeartbeat: Date.now(),
          };
          agents.set(id, { info, socket: conn.socket });
          app.log.info({ agent: info }, 'agent registered');
          sendToAgent(id, { type: 'registered', payload: { id } });
          tryDispatch();
        } else if (msg.type === 'heartbeat') {
          const id = msg.payload?.id;
          const rec = id ? agents.get(id) : undefined;
          if (rec) rec.info.lastHeartbeat = Date.now();
        } else if (msg.type === 'lease-accepted') {
          const id = msg.payload?.agentId;
          const jobId = msg.payload?.jobId;
          app.log.debug({ id, jobId }, 'lease accepted');
          const rec = id ? agents.get(id) : undefined;
          const job = jobs.get(jobId);
          if (rec && job) { job.status = 'running'; job.updatedAt = Date.now(); }
        } else if (msg.type === 'progress') {
          // progression
        } else if (msg.type === 'complete') {
          const jobId = msg.payload?.jobId;
          const success = !!msg.payload?.success;
          const agentId = msg.payload?.agentId;
          app.log.info({ jobId, success, agentId }, 'job completed');
          const rec = agentId ? agents.get(agentId) : undefined;
          const job = jobs.get(jobId);
          if (rec && rec.info.activeJobs > 0) rec.info.activeJobs -= 1;
          if (job) { job.status = success ? 'uploaded' : 'failed'; job.updatedAt = Date.now(); }
          tryDispatch();
        }
      } catch (e) { app.log.error(e); }
    });
    conn.socket.on('close', () => { app.log.debug('agent socket closed'); });
  });

  app.get('/', (_req: any, reply: any) => { (reply as any).sendFile('index.html'); });

  //this part do that
  //API d'appairage: ajouter un token autorisé
  app.post('/api/pair', async (req: any, reply: any) => {
    const body = req.body as { token?: string };
    const token = (body?.token || '').trim();
    if (!token || token.length !== 25) return reply.status(400).send({ error: 'invalid token' });
    allowedTokens.add(token);
    app.log.info({ token }, 'pair token added');
    return { ok: true };
  });

  //this other part do that
  //API des paramètres (URL publique)
  app.get('/api/settings', async () => ({ publicBaseUrl }));
  app.post('/api/settings', async (req: any, reply: any) => {
    const body = req.body as { publicBaseUrl?: string };
    const val = (body?.publicBaseUrl || '').trim();
    if (!/^https?:\/\//.test(val)) return reply.status(400).send({ error: 'invalid URL' });
    publicBaseUrl = val.replace(/\/$/, '');
    app.log.info({ publicBaseUrl }, 'settings updated');
    return { ok: true, publicBaseUrl };
  });

  //this part do that
  //API pour déclencher une boîte de dialogue native côté serveur
  app.post('/api/pick', async (req: any, reply: any) => {
    const body = req.body as { type?: 'dir' | 'files' };
    const kind = body?.type === 'files' ? 'files' : 'dir';
    const paths = await pickPathNative(kind);
    app.log.debug({ kind, count: paths.length }, 'native pick');
    if (!paths || paths.length === 0) return reply.status(400).send({ error: 'no selection' });
    return { paths };
  });

  //this part do that
  //upload de fichiers locaux vers un dossier de destination
  app.post('/api/upload', async (req: any, reply: any) => {
    const mp = await (req as any).multipart((field: string, file: any, filename: string, encoding: string, mimetype: string) => {}, async (_err: any) => {});
    const parts: { data: Buffer; filename: string }[] = [];
    let dest = '';
    for await (const part of mp as any) {
      if (part.type === 'file') {
        const chunks: Buffer[] = [];
        for await (const chunk of part.file as any) { chunks.push(chunk as Buffer); }
        parts.push({ data: Buffer.concat(chunks), filename: part.filename as string });
      } else if (part.type === 'field' && part.fieldname === 'dest') {
        dest = part.value as string;
      }
    }
    if (!dest) return reply.status(400).send({ error: 'missing dest' });
    await fsp.mkdir(dest, { recursive: true });
    for (const p of parts) {
      const outPath = path.join(dest, path.basename(p.filename));
      await fsp.writeFile(outPath, p.data);
    }
    app.log.info({ count: parts.length, dest }, 'upload saved');
    return reply.send({ ok: true, count: parts.length });
  });

  app.get('/api/nodes', async () => {
    const list = Array.from(agents.values()).map(a => a.info);
    const pending = pendingJobs.length;
    const totals = { totalJobs: jobs.size, pendingJobs: pending, runningJobs: Array.from(agents.values()).reduce((acc, a) => acc + a.info.activeJobs, 0) };
    return { agents: list, totals };
  });

  app.post('/api/scan', async (req: any, reply: any) => {
    const body = req.body as ScanRequest;
    if (!body || !body.inputRoot || !body.outputRoot || !body.mediaType || !body.codec) return reply.status(400).send({ error: 'invalid request' });
    const inputRoot = path.resolve(body.inputRoot);
    const outputRoot = path.resolve(body.outputRoot);
    app.log.info({ inputRoot, outputRoot, mediaType: body.mediaType, codec: body.codec, recursive: body.recursive, mirror: body.mirrorStructure }, 'scan request');
    try { const st = await fsp.stat(inputRoot); if (!st.isDirectory()) return reply.status(400).send({ error: 'inputRoot must be a directory' }); }
    catch { return reply.status(400).send({ error: 'inputRoot not found' }); }
    await fsp.mkdir(outputRoot, { recursive: true });
    const files = await scanFiles(inputRoot, !!body.recursive, body.mediaType);
    const outputExt = computeOutputExt(body.mediaType, body.codec);
    const plan: PlanJob[] = files.map(f => {
      const rel = path.relative(inputRoot, f.path);
      const base = body.mirrorStructure ? path.join(outputRoot, rel) : path.join(outputRoot, path.basename(rel));
      const outPath = base.replace(path.extname(base), outputExt);
      return { sourcePath: f.path, relativePath: rel, mediaType: body.mediaType, sizeBytes: f.size, outputPath: outPath, codec: body.codec, options: body.options || {} };
    });
    const totalSize = files.reduce((a, b) => a + b.size, 0);
    app.log.info({ count: plan.length, totalBytes: totalSize }, 'scan result');
    return { count: plan.length, totalBytes: totalSize, jobs: plan };
  });

  app.post('/api/start', async (req: any, reply: any) => {
    const body = req.body as { jobs: PlanJob[] };
    if (!body || !Array.isArray(body.jobs) || body.jobs.length === 0) return reply.status(400).send({ error: 'no jobs' });
    for (const pj of body.jobs) {
      const id = uuidv4();
      const j: Job = { ...pj, id, status: 'pending', inputToken: randomBytes(16).toString('hex'), outputToken: randomBytes(16).toString('hex'), createdAt: Date.now(), updatedAt: Date.now() };
      jobs.set(id, j);
      pendingJobs.push(j);
    }
    app.log.info({ accepted: body.jobs.length }, 'start accepted');
    tryDispatch();
    return { accepted: body.jobs.length };
  });

  app.get('/stream/input/:jobId', async (req: any, reply: any) => {
    const { jobId } = req.params as any;
    const { token } = req.query as any;
    const job = jobs.get(jobId);
    if (!job || token !== job.inputToken) return reply.status(403).send({ error: 'forbidden' });
    const stat = await fsp.stat(job.sourcePath);
    const size = stat.size;
    const range = (req.headers['range'] || '') as string;
    const mimeType = (mime.lookup(path.extname(job.sourcePath)) as string) || 'application/octet-stream';
    if (range && range.startsWith('bytes=')) {
      const [startStr, endStr] = range.replace('bytes=', '').split('-');
      const start = parseInt(startStr, 10);
      const end = endStr ? parseInt(endStr, 10) : size - 1;
      const chunkSize = (end - start) + 1;
      app.log.debug({ jobId, start, end, size }, 'input range');
      reply.header('Content-Range', `bytes ${start}-${end}/${size}`).header('Accept-Ranges', 'bytes').header('Content-Length', chunkSize).header('Content-Type', mimeType).status(206);
      const stream = fs.createReadStream(job.sourcePath, { start, end });
      return reply.send(stream);
    }
    app.log.debug({ jobId, size }, 'input full');
    reply.header('Content-Length', size).header('Accept-Ranges', 'bytes').header('Content-Type', mimeType).status(200);
    const stream = fs.createReadStream(job.sourcePath);
    return reply.send(stream);
  });

  app.put('/stream/output/:jobId', async (req: any, reply: any) => {
    const { jobId } = req.params as any;
    const { token } = req.query as any;
    const job = jobs.get(jobId);
    if (!job || token !== job.outputToken) return reply.status(403).send({ error: 'forbidden' });
    await fsp.mkdir(path.dirname(job.outputPath), { recursive: true });
    const tmpPath = job.outputPath + '.part';
    const writeStream = fs.createWriteStream(tmpPath);
    app.log.debug({ jobId, outputPath: job.outputPath }, 'upload begin');
    await new Promise<void>((resolve, reject) => { req.raw.pipe(writeStream); req.raw.on('error', reject); writeStream.on('error', reject); writeStream.on('finish', () => resolve()); });
    await fsp.rename(tmpPath, job.outputPath);
    job.status = 'completed';
    job.updatedAt = Date.now();
    app.log.info({ jobId }, 'upload completed');
    return reply.status(200).send({ ok: true });
  });
}

function sendToAgent(agentId: string, msg: any) {
  const rec = agents.get(agentId);
  if (!rec) return;
  try { rec.socket.send(JSON.stringify(msg)); } catch {}
}

function pickAgentForJob(job: Job): string | undefined {
  let bestId: string | undefined; let bestFree = -1;
  for (const [id, rec] of agents.entries()) {
    const free = rec.info.concurrency - rec.info.activeJobs;
    if (free > bestFree) { bestFree = free; bestId = id; }
  }
  return bestId;
}

function tryDispatch() {
  pendingJobs.sort((a, b) => b.sizeBytes - a.sizeBytes);
  let madeProgress = true;
  while (madeProgress) {
    madeProgress = false;
    for (const [id, rec] of agents.entries()) {
      const free = rec.info.concurrency - rec.info.activeJobs;
      if (free <= 0) continue;
      const job = pendingJobs.shift();
      if (!job) break;
      const agentId = pickAgentForJob(job);
      if (!agentId) { pendingJobs.unshift(job); continue; }
      const assigned = agents.get(agentId);
      if (!assigned) { pendingJobs.unshift(job); continue; }
      const base = getPublicBaseUrl();
      const inputUrl = `${base}/stream/input/${encodeURIComponent(job.id)}?token=${job.inputToken}`;
      const outputUrl = `${base}/stream/output/${encodeURIComponent(job.id)}?token=${job.outputToken}`;
      const { args } = buildFfmpegArgs(job);
      const outputExt = path.extname(job.outputPath) || computeOutputExt(job.mediaType, job.codec);
      job.status = 'assigned'; job.nodeId = agentId; job.updatedAt = Date.now(); assigned.info.activeJobs += 1;
      app.log.debug({ agentId, jobId: job.id, inputUrl }, 'dispatch job');
      sendToAgent(agentId, { type: 'lease', payload: { jobId: job.id, inputUrl, outputUrl, ffmpegArgs: args, outputExt, threads: 0 } });
      madeProgress = true;
    }
  }
}

async function main() {
  registerRoutes();
  try { await app.listen({ host: HOST, port: PORT }); app.log.info(`GUI controller listening at ${getPublicBaseUrl()}`); }
  catch (err) { app.log.error(err); process.exit(1); }
}

main();
