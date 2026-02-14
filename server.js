const express = require('express');
const path = require('path');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const Joi = require('joi');
const { Pool } = require('pg');
const Queue = require('bull');
const crypto = require('crypto');
const PDFDocument = require('pdfkit');

const app = express();
const PORT = process.env.PORT;
const DATABASE_URL = process.env.DATABASE_URL;
const REDIS_URL = process.env.REDIS_URL;

if (!PORT || !DATABASE_URL || !REDIS_URL) {
  console.error("Missing required environment variables.");
  process.exit(1);
}

app.use(helmet());
app.use(morgan('combined'));
app.use(express.json());
app.use(rateLimit({ windowMs: 15*60*1000, max: 100 }));

const pool = new Pool({
  connectionString: DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

(async () => {
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS videos (
        id TEXT PRIMARY KEY,
        creator_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        uses_third_party_music BOOLEAN,
        created_at TIMESTAMP DEFAULT NOW(),
        processed_at TIMESTAMP,
        evidence_json JSONB,
        hash TEXT
      );
    `);
    console.log("Videos table ready.");
  } catch (err) {
    console.error("Table creation failed:", err);
  }
})();

const videoQueue = new Queue('video-processing', REDIS_URL, {
  defaultJobOptions: { removeOnComplete: true, removeOnFail: true },
  limiter: { max: 50, duration: 1000 }
});

const videoSchema = Joi.object({
  creatorId: Joi.string().required(),
  videoUrl: Joi.string().uri().required(),
  title: Joi.string().min(3).required(),
  usesThirdPartyMusic: Joi.boolean().optional()
});

// =========================
// Serve standalone index.html at root
// =========================
app.use(express.static(path.join(__dirname)));

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

// =========================
// Other routes
// =========================
app.get('/health', async (req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ status: "ok", uptime: process.uptime() });
  } catch {
    res.status(500).json({ status: "db_error" });
  }
});

app.post('/process-video', async (req, res) => {
  try {
    const { error, value } = videoSchema.validate(req.body);
    if (error) return res.status(400).json({ error: error.details[0].message });

    const videoId = `vid_${Date.now()}`;

    await pool.query(
      `INSERT INTO videos (id, creator_id, title, status, uses_third_party_music, created_at)
       VALUES ($1, $2, $3, $4, $5, NOW())`,
      [videoId, value.creatorId, value.title, 'queued', value.usesThirdPartyMusic]
    );

    await videoQueue.add({ videoId, creatorId: value.creatorId, title: value.title, usesThirdPartyMusic: value.usesThirdPartyMusic });

    res.json({ status: "queued", videoId });
  } catch (err) {
    console.error("Process error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

app.get('/videos', async (req, res) => {
  try {
    const result = await pool.query(`SELECT * FROM videos ORDER BY created_at DESC`);
    res.json(result.rows);
  } catch (err) {
    console.error("Fetch error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

app.get('/evidence/:videoId/download', async (req, res) => {
  try {
    const { videoId } = req.params;
    const { format } = req.query;

    const result = await pool.query(`SELECT * FROM videos WHERE id=$1`, [videoId]);
    if (!result.rows.length) return res.status(404).json({ error: "Video not found" });
    const video = result.rows[0];

    if (format === 'pdf') {
      const doc = new PDFDocument();
      res.setHeader('Content-Disposition', `attachment; filename=${videoId}.pdf`);
      res.setHeader('Content-Type', 'application/pdf');
      doc.text(JSON.stringify(video.evidence_json, null, 2));
      doc.text(`Hash: ${video.hash}`);
      doc.pipe(res);
      doc.end();
    } else {
      res.json(video.evidence_json);
    }
  } catch (err) {
    console.error("Evidence download error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

videoQueue.process(10, async (job) => {
  const { videoId, creatorId, title, usesThirdPartyMusic } = job.data;
  const processedAt = new Date().toISOString();

  const flags = [];
  if (usesThirdPartyMusic) flags.push('third_party_music');
  if (title.toLowerCase().includes('explicit')) flags.push('explicit_content');
  if (title.length < 5) flags.push('short_title');

  const evidence = { videoId, creatorId, title, status: 'processed', usesThirdPartyMusic, created_at: processedAt, processed_at: processedAt, flags };
  const hash = crypto.createHash('sha256').update(JSON.stringify(evidence)).digest('hex');

  await pool.query(
    `UPDATE videos SET status='processed', processed_at=$1, evidence_json=$2, hash=$3 WHERE id=$4`,
    [processedAt, evidence, hash, videoId]
  );

  console.log(`Processed ${videoId} with flags: [${flags.join(', ')}] and hash ${hash}`);
});

app.listen(PORT, () => console.log(`SeekReap Tier-4 running on port ${PORT}`));
