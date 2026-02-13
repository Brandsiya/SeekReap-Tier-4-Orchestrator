const express = require('express');
const bodyParser = require('body-parser');
const path = require('path');
const { processVideo } = require('./pipelines/processVideos');

const app = express();
app.use(bodyParser.json());

// Serve reports folder
app.use('/reports', express.static(path.join(__dirname, 'pipelines/reports')));

app.post('/process-video', async (req, res) => {
  try {
    const videoData = req.body;
    if (!videoData.creatorId || !videoData.videoUrl || !videoData.title) {
      return res.status(400).json({ error: 'Missing required fields' });
    }

    const report = await processVideo(videoData);

    // Return public URLs for Render
    const baseUrl = process.env.BASE_URL || 'https://seekreap-system.onrender.com';
    res.json({
      success: true,
      reportId: report.reportId,
      jsonUrl: `${baseUrl}/reports/${report.reportId}.json`,
      pdfUrl: `${baseUrl}/reports/${report.reportId}.pdf`,
      tier5Result: report.tier5Result
    });
  } catch (err) {
    console.error('Processing failed:', err.message);
    res.status(500).json({ error: 'Processing failed' });
  }
});

const PORT = process.env.PORT || 10000;
app.listen(PORT, () => console.log(`🚀 Tier-4 server running on port ${PORT}`));
