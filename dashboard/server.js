const express = require('express');
const bodyParser = require('body-parser');
const { processVideo } = require('../pipelines/processVideos.js');

const app = express();
app.use(bodyParser.json());
const PORT = process.env.PORT || 10000;

app.post('/process-video', async (req, res) => {
  try {
    const result = await processVideo(req.body);
    res.json(result);
  } catch (err) {
    console.error('Full error stack:', err);
    res.status(500).json({ error: 'Processing failed', details: err.message });
  }
});

app.listen(PORT, () => console.log(`Tier-4 API with Creator Aggregation running on port ${PORT}`));
