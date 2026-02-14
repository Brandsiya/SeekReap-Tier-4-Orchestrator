const axios = require('axios');
const fs = require('fs');
const path = require('path');
const { v4: uuidv4 } = require('uuid');

const TIER5_URL = 'https://seekreap-tier-5-orchestrator.onrender.com/task'; // Tier-5 Render URL

async function processVideo(videoData) {
    try {
        // Send metadata to Tier-5
        const response = await axios.post(TIER5_URL, {
            metadata: {
                title: videoData.title,
                usesThirdPartyMusic: videoData.usesThirdPartyMusic
            }
        });

        const result = response.data.result;

        // Save a creator-proof JSON report
        const report = {
            reportId: uuidv4(),
            creatorId: videoData.creatorId,
            videoUrl: videoData.videoUrl,
            timestamp: new Date().toISOString(),
            tier5Result: result
        };

        const reportsDir = path.join(__dirname, 'reports');
        if (!fs.existsSync(reportsDir)) fs.mkdirSync(reportsDir);

        const reportPath = path.join(reportsDir, `${report.reportId}.json`);
        fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));

        return report; // This is the proof creators can submit
    } catch (err) {
        console.error('Error processing video:', err.message);
        throw new Error('Processing failed');
    }
}

module.exports = { processVideo };
