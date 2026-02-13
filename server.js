const express = require('express');
const { processVideo } = require('./pipelines/processVideos');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 5000;

app.post('/process-video', async (req, res) => {
    const videoData = req.body;
    try {
        const report = await processVideo(videoData);
        res.json({ success: true, report });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

app.get('/health', (req, res) => res.send('Tier-4 is healthy ✅'));

app.listen(PORT, () => console.log(`Tier-4 running on port ${PORT}`));
