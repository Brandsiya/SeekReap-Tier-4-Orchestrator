const express = require('express');
const path = require('path');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const { Pool } = require('pg');
const Queue = require('bull');
const PDFDocument = require('pdfkit');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 10000;
const DATABASE_URL = process.env.DATABASE_URL;
const REDIS_URL = process.env.REDIS_URL;

// Security & Middleware
app.use(helmet());
app.use(morgan('combined'));
app.use(express.json());
app.use(rateLimit({ windowMs: 15*60*1000, max: 100 }));

// PostgreSQL Connection
const pool = new Pool({
  connectionString: DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

// Auto-create / update table
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

// Redis Queue
const videoQueue = new Queue('video-processing', REDIS_URL, {
  defaultJobOptions: { removeOnComplete: true, removeOnFail: true },
  limiter: { max: 50, duration: 1000 }
});

// Serve standalone index.html as landing page
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

// Serve all other static files (terms.html, privacy.html, etc.)
app.use(express.static(__dirname));

// Start Server
app.listen(PORT, () => console.log(`SeekReap Tier-4 running on port ${PORT}`));
