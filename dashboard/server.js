const express = require('express');
const fs = require('fs');
const path = require('path');
const { processVideo } = require('../pipelines/processVideos.js');

const app = express();
app.use(express.json());
const PORT = process.env.PORT || 10000;

// Serve PDF reports & JSON DB
app.use('/seekreap-tier4-db', express.static(path.join(__dirname, '../seekreap-tier4-db')));

// Video processing endpoint
app.post('/process-video', async (req, res) => {
  try {
    const result = await processVideo(req.body);
    res.json(result);
  } catch (err) {
    console.error('Full error stack:', err);
    res.status(500).json({ error:'Processing failed', details: err.message });
  }
});

// Fetch all videos
app.get('/videos', (req, res) => {
  const dbPath = path.join(__dirname, '../seekreap-tier4-db/videos.json');
  const db = fs.existsSync(dbPath) ? JSON.parse(fs.readFileSync(dbPath)) : {};
  res.json(db);
});

app.listen(PORT, () => console.log(`Tier-4 API running on port ${PORT}`));
