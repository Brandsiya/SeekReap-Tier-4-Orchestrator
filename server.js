const express = require('express');
const path = require('path');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const cors = require('cors');
const { Pool } = require('pg');
const Queue = require('bull');
const PDFDocument = require('pdfkit');
const crypto = require('crypto');
const app = express();
const PORT = process.env.PORT || 10000;
const DATABASE_URL = process.env.DATABASE_URL;
const REDIS_URL = process.env.REDIS_URL;
// =========================
// Security & Middleware
// =========================
app.use(cors()); // CRITICAL - allows frontend to connect
app.use(helmet());
app.use(morgan('combined'));
app.use(express.json());
app.use(rateLimit({ windowMs: 15*60*1000, max: 100 }));
// =========================
// PostgreSQL Connection
// =========================
const pool = new Pool({
  connectionString: DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});
// Initialize database
(async () => {
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS videos (
        id VARCHAR(50) PRIMARY KEY,
        creator_id VARCHAR(100) NOT NULL,
        video_url TEXT NOT NULL,
        title TEXT,
        uses_third_party_music BOOLEAN DEFAULT FALSE,
        status VARCHAR(20) DEFAULT 'queued',
        flags JSONB,
        evidence_hash VARCHAR(100),
        created_at TIMESTAMP DEFAULT NOW(),
        processed_at TIMESTAMP
      )
    `);
    console.log('Videos table ready.');
  } catch (err) {
    console.error("Table creation failed:", err);
  }
})();
// =========================
// Redis Queue
// =========================
const videoQueue = new Queue('video-processing', REDIS_URL, {
  defaultJobOptions: { removeOnComplete: true, removeOnFail: true },
  limiter: { max: 50, duration: 1000 }
});
// =========================
// Serve static files
// =========================
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});
// =========================
// ✅ HEALTH CHECK ENDPOINT
// =========================
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime: process.uptime()
  });
});
// =========================
// ✅ START VERIFICATION ENDPOINT
// =========================
app.post('/process-video', async (req, res) => {
  try {
    const { creatorId, videoUrl, title, usesThirdPartyMusic } = req.body;
    if (!creatorId || !videoUrl) {
      return res.status(400).json({
        error: 'creatorId and videoUrl are required'
      });
    }
    const videoId = `vid_${Date.now()}`;
    await pool.query(
      `INSERT INTO videos 
      (id, creator_id, video_url, title, uses_third_party_music, status)
      VALUES ($1, $2, $3, $4, $5, 'queued')`,
      [videoId, creatorId, videoUrl, title || null, usesThirdPartyMusic || false]
    );
    await videoQueue.add({ videoId });
    res.json({
      status: 'queued',
      videoId
    });
  } catch (err) {
    console.error("Process error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});
// =========================
// ✅ GET VERIFICATION STATUS ENDPOINT
// =========================
app.get('/video/:videoId', async (req, res) => {
  try {
    const { videoId } = req.params;
    const result = await pool.query(
      `SELECT id, status, flags, evidence_hash, created_at, processed_at 
       FROM videos WHERE id=$1`,
      [videoId]
    );
    if (!result.rows.length) {
      return res.status(404).json({ error: 'Video not found' });
    }
    res.json(result.rows[0]);
  } catch (err) {
    console.error("Fetch error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});
// =========================
// ✅ QUEUE WORKER PROCESSOR
// =========================
videoQueue.process(10, async (job) => {
  const { videoId } = job.data;
  
  console.log(`Processing video ${videoId}`);
  
  try {
    // Update status to processing
    await pool.query(
      `UPDATE videos SET status='processing' WHERE id=$1`,
      [videoId]
    );
    // Simulate verification pipeline (replace with your actual logic)
    const flags = {
      hasAudio: true,
      hasFace: Math.random() > 0.1,
      duration: Math.floor(Math.random() * 300) + 30,
      riskScore: Math.random().toFixed(2)
    };
    
    // Generate evidence hash
    const evidenceHash = crypto
      .createHash('sha256')
      .update(JSON.stringify(flags) + Date.now())
      .digest('hex');
    // Update final status
    await pool.query(
      `UPDATE videos
       SET status='processed',
           flags=$1,
           evidence_hash=$2,
           processed_at=NOW()
       WHERE id=$3`,
      [flags, evidenceHash, videoId]
    );
    console.log(`Video ${videoId} processed successfully`);
    return { success: true, videoId };
    
  } catch (err) {
    console.error(`Error processing video ${videoId}:`, err);
    throw err;
  }
});
app.listen(PORT, () => {
  console.log(`SeekReap Tier-4 running on port ${PORT}`);
});
// =========================
// Serve static files from public directory
// =========================
app.use(express.static(path.join(__dirname, 'public')));
// =========================
// Serve static files from public directory
// =========================
app.use(express.static(path.join(__dirname, 'public')));
