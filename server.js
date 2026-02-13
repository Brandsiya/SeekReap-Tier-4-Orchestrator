const express = require('express');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(express.json());

// Serve portal at root
app.use('/', express.static(path.join(__dirname, 'SeekReap-Verif-Portal')));

// Video processing endpoint
app.post('/process-video', (req, res) => {
  const { creatorId, videoUrl, title, usesThirdPartyMusic } = req.body;
  if (!creatorId || !videoUrl || !title) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  const videoId = `vid_${Date.now()}`;
  const result = { id: videoId, creatorId, title, status: 'processed', usesThirdPartyMusic };

  // Save to JSON "database"
  const dbPath = path.join(__dirname, 'seekreap-tier4-db', 'videos.json');
  let db = {};
  if (fs.existsSync(dbPath)) db = JSON.parse(fs.readFileSync(dbPath));
  db[videoId] = result;
  fs.writeFileSync(dbPath, JSON.stringify(db, null, 2));

  res.json({ ...result, pdf: `/seekreap-tier4-db/${videoId}.pdf` });
});

// List all videos
app.get('/videos', (req, res) => {
  const dbPath = path.join(__dirname, 'seekreap-tier4-db', 'videos.json');
  let db = {};
  if (fs.existsSync(dbPath)) db = JSON.parse(fs.readFileSync(dbPath));
  res.json(db);
});

// Serve PDFs
app.use('/seekreap-tier4-db', express.static(path.join(__dirname, 'seekreap-tier4-db')));

// Start server
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
