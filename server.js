const express = require('express');
const path = require('path');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const Joi = require('joi');
const { Pool } = require('pg');
const Queue = require('bull');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT;
const DATABASE_URL = process.env.DATABASE_URL;
const REDIS_URL = process.env.REDIS_URL;

// =========================
// Environment Validation
// =========================
if (!PORT || !DATABASE_URL || !REDIS_URL) {
  console.error("Missing required environment variables.");
  process.exit(1);
}

// =========================
// Security & Middleware
// =========================
app.use(helmet());
app.use(morgan('combined'));
app.use(express.json());

app.use(rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100
}));

// =========================
// PostgreSQL Connection
// =========================
const pool = new Pool({
  connectionString: DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

// Auto-create table
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

// =========================
// Redis Queue
// =========================
const videoQueue = new Queue('video-processing', REDIS_URL);

videoQueue.process(10, async (job) => {
  const { videoId, creatorId, title, usesThirdPartyMusic } = job.data;
  const processedAt = new Date().toISOString();

  // Flags generation
  const flags = [];
  if (usesThirdPartyMusic) flags.push('third_party_music');
  if (title.toLowerCase().includes('explicit')) flags.push('explicit_content');
  if (title.length < 5) flags.push('short_title');

  // Flag weights
  const weights = {
    third_party_music: 5,
    explicit_content: 4,
    short_title: 1,
    copyright_claim: 5,
    spam_keywords: 2,
    sensitive_topic: 3
  };

  // Compute risk score
  const risk_score = flags.reduce((acc, f) => acc + (weights[f] || 0), 0);
  const severity = risk_score >= 7 ? 'high' : (risk_score >= 3 ? 'medium' : 'low');

  // Evidence JSON
  const evidence = {
    videoId,
    creatorId,
    title,
    status: 'processed',
    usesThirdPartyMusic,
    created_at: processedAt,
    processed_at: processedAt,
    flags,
    risk_score,
    severity
  };

  // Hash for tamper-proof
  const hash = crypto.createHash('sha256').update(JSON.stringify(evidence)).digest('hex');

  // Update database
  await pool.query(
    `UPDATE videos SET status='processed', processed_at=$1, evidence_json=$2, hash=$3 WHERE id=$4`,
    [processedAt, evidence, hash, videoId]
  );

  console.log(`Processed ${videoId} | Flags: [${flags.join(', ')}] | Risk: ${risk_score} (${severity})`);
});

// =========================
// Validation Schema
// =========================
const videoSchema = Joi.object({
  creatorId: Joi.string().required(),
  videoUrl: Joi.string().uri().required(),
  title: Joi.string().min(3).required(),
  usesThirdPartyMusic: Joi.boolean().optional()
});

// =========================
// Routes
// =========================

// Serve Portal
app.use('/', express.static(path.join(__dirname, 'SeekReap-Verif-Portal')));

// Health
app.get('/health', async (req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ status: "ok", uptime: process.uptime() });
  } catch (err) {
    res.status(500).json({ status: "db_error" });
  }
});

// Process Video
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

// List Videos
app.get('/videos', async (req, res) => {
  try {
    const result = await pool.query(`SELECT * FROM videos ORDER BY created_at DESC`);
    res.json(result.rows);
  } catch (err) {
    console.error("Fetch error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// =========================
// Start
// =========================
app.listen(PORT, () => {
  console.log(`SeekReap Tier-4 running on port ${PORT}`);
});
