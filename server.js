const express = require('express');
const bodyParser = require('body-parser');
const { processVideo } = require('./pipelines/processVideos');

const app = express();
app.use(bodyParser.json());

app.post('/process-video', async (req, res) => {
  try {
    console.log('📩 Incoming request:', req.body);

    const report = await processVideo(req.body);

    res.json({
      success: true,
      report
    });

  } catch (err) {
    console.error('❌ Server error:', err);
    res.status(500).json({
      success: false,
      error: err.message
    });
  }
});

const PORT = process.env.PORT || 10000;

app.listen(PORT, () => {
  console.log(`🚀 Tier-4 server running on port ${PORT}`);
});
