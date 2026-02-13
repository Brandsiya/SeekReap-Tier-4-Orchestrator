const express = require('express');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware to parse JSON
app.use(express.json());

// Serve the portal folder
app.use(express.static(path.join(__dirname, 'SeekReap-Verif-Portal')));

// Redirect root URL to portal index.html
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'SeekReap-Verif-Portal', 'index.html'));
});

// Endpoint to process videos
app.post('/process-video', (req, res) => {
  const { creatorId, videoUrl, title, usesThirdPartyMusic } = req.body;

  if (!creatorId || !videoUrl || !title) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  const videoId = `vid_${Date.now()}`;
  const result = { id: videoId, creatorId, title, status: 'processed', usesThirdPartyMusic };

  const dbPath = path.join(__dirname, 'seekreap-tier4-db', 'videos.json');
  let db = {};
  if (fs.existsSync(dbPath)) db = JSON.parse(fs.readFileSync(dbPath));
  db[videoId] = result;
  fs.writeFileSync(dbPath, JSON.stringify(db, null, 2));

  res.json({ ...result, pdf: `/seekreap-tier4-db/${videoId}.pdf` });
});

// Endpoint to list all videos
app.get('/videos', (req, res) => {
  const dbPath = path.join(__dirname, 'seekreap-tier4-db', 'videos.json');
  let db = {};
  if (fs.existsSync(dbPath)) db = JSON.parse(fs.readFileSync(dbPath));
  res.json(db);
});

// Serve PDF reports
app.use('/seekreap-tier4-db', express.static(path.join(__dirname, 'seekreap-tier4-db')));

// Start server
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
