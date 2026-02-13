const express = require('express');
const path = require('path');
const fs = require('fs');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');

const app = express();
const PORT = process.env.PORT;

// =========================
// 1️⃣ Environment Validation
// =========================
if (!PORT) {
  console.error(JSON.stringify({
    level: "error",
    message: "PORT environment variable not defined"
  }));
  process.exit(1);
}

// =========================
// 2️⃣ Structured Logging
// =========================
app.use(morgan('combined'));

// =========================
// 3️⃣ Rate Limiting
// =========================
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 100, // limit each IP
  standardHeaders: true,
  legacyHeaders: false,
});
app.use(limiter);

// =========================
// Middleware
// =========================
app.use(express.json());

// =========================
// Serve Portal at Root
// =========================
app.use('/', express.static(path.join(__dirname, 'SeekReap-Verif-Portal')));

// =========================
// Health Check Endpoint
// =========================
app.get('/health', (req, res) => {
  res.status(200).json({
    status: "ok",
    uptime: process.uptime(),
    timestamp: Date.now()
  });
});

// =========================
// Video Processing Endpoint
// =========================
app.post('/process-video', (req, res) => {
  const { creatorId, videoUrl, title, usesThirdPartyMusic } = req.body;

  if (!creatorId || !videoUrl || !title) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  const videoId = `vid_${Date.now()}`;
  const result = {
    id: videoId,
    creatorId,
    title,
    status: 'processed',
    usesThirdPartyMusic,
    createdAt: new Date().toISOString()
  };

  const dbPath = path.join(__dirname, 'seekreap-tier4-db', 'videos.json');
  let db = {};
  if (fs.existsSync(dbPath)) {
    db = JSON.parse(fs.readFileSync(dbPath));
  }

  db[videoId] = result;
  fs.writeFileSync(dbPath, JSON.stringify(db, null, 2));

  res.json({
    ...result,
    pdf: `/seekreap-tier4-db/${videoId}.pdf`
  });
});

// =========================
// List Videos
// =========================
app.get('/videos', (req, res) => {
  const dbPath = path.join(__dirname, 'seekreap-tier4-db', 'videos.json');
  let db = {};
  if (fs.existsSync(dbPath)) {
    db = JSON.parse(fs.readFileSync(dbPath));
  }
  res.json(db);
});

// =========================
// Serve PDFs
// =========================
app.use('/seekreap-tier4-db', express.static(path.join(__dirname, 'seekreap-tier4-db')));

// =========================
// Start Server
// =========================
app.listen(PORT, () => {
  console.log(JSON.stringify({
    level: "info",
    message: "SeekReap Tier-4 Orchestrator running",
    port: PORT
  }));
});
