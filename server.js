const express = require('express');
const path = require('path');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const Joi = require('joi');
const { Pool } = require('pg');
const Queue = require('bull');

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

const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100,
});
app.use(limiter);

// =========================
// PostgreSQL Connection
// =========================
const pool = new Pool({
  connectionString: DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

// =========================
// Redis Queue
// =========================
const videoQueue = new Queue('video-processing', REDIS_URL);

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
// Serve Portal
// =========================
app.use('/', express.static(path.join(__dirname, 'SeekReap-Verif-Portal')));

// =========================
// Health Check
// =========================
app.get('/health', async (req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ status: "ok", uptime: process.uptime() });
  } catch (err) {
    res.status(500).json({ status: "db_error" });
  }
});

// =========================
// Process Video (Queue)
// =========================
app.post('/process-video', async (req, res) => {
  const { error, value } = videoSchema.validate(req.body);
  if (error) {
    return res.status(400).json({ error: error.details[0].message });
  }

  const videoId = `vid_${Date.now()}`;

  await pool.query(
    `INSERT INTO videos (id, creator_id, title, status, uses_third_party_music, created_at)
     VALUES ($1, $2, $3, $4, $5, NOW())`,
    [videoId, value.creatorId, value.title, 'queued', value.usesThirdPartyMusic]
  );

  await videoQueue.add({ videoId });

  res.json({ status: "queued", videoId });
});

// =========================
// Queue Processor
// =========================
videoQueue.process(async (job) => {
  const { videoId } = job.data;

  // Simulated processing
  await new Promise(resolve => setTimeout(resolve, 3000));

  await pool.query(
    `UPDATE videos SET status='processed' WHERE id=$1`,
    [videoId]
  );

  console.log(`Processed ${videoId}`);
});

// =========================
// List Videos
// =========================
app.get('/videos', async (req, res) => {
  const result = await pool.query(`SELECT * FROM videos ORDER BY created_at DESC`);
  res.json(result.rows);
});

// =========================
// Start Server
// =========================
app.listen(PORT, () => {
  console.log(`SeekReap Tier-4 running on port ${PORT}`);
});
