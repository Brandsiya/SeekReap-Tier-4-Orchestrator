const express = require('express');
const bodyParser = require('body-parser');
const { processVideo } = require('../pipelines/processVideos');

const app = express();
app.use(bodyParser.json());

app.post('/upload', async (req, res) => {
  const { videoUrl, title, usesThirdPartyMusic } = req.body;
  if (!videoUrl || !title) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  const decision = await processVideo(videoUrl, { title, usesThirdPartyMusic });
  if (!decision) return res.status(500).json({ error: 'Processing failed' });

  res.json({ status: 'success', finalDecision: decision });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Tier-4 API running on port ${PORT}`));
