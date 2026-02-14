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

app.use(rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100
}));

// =========================
// PostgreSQL
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
        created_at TIMESTAMP DEFAULT NOW()
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

videoQueue.process(async (job) => {
  const { videoId } = job.data;

  await new Promise(resolve => setTimeout(resolve, 3000));

  await pool.query(
    `UPDATE videos SET status='processed' WHERE id=$1`,
    [videoId]
  );

  console.log(`Processed ${videoId}`);
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

  } catch (err) {
    console.error("Process error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// List Videos
app.get('/videos', async (req, res) => {
  try {
    const result = await pool.query(
      `SELECT * FROM videos ORDER BY created_at DESC`
    );
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
