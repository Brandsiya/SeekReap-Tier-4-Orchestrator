// pipelines/processVideos.js
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const { v4: uuidv4 } = require('uuid');

// Logs directory for Tier-4
const LOG_DIR = path.join(__dirname, '../logs');
if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR);

// Log Tier-4 decisions locally
function logTier4Decision(data) {
    const timestamp = new Date().toISOString();
    const logPath = path.join(LOG_DIR, 'tier4_decisions.json');
    fs.appendFileSync(logPath, JSON.stringify({ timestamp, ...data }) + '\n');
}

// Function to process a video
async function processVideo({ creatorId, videoUrl, title, usesThirdPartyMusic }) {
    try {
        // Payload for Tier-5
        const payload = {
            metadata: {
                title,
                usesThirdPartyMusic
            }
        };

        // Call Tier-5 live endpoint
        const response = await axios.post(
            'https://seekreap-tier-5-orchestrator.onrender.com/task',
            payload,
            { timeout: 10000 } // 10 seconds timeout
        );

        // Tier-5 result
        const result = response.data.result;

        // Log locally in Tier-4
        logTier4Decision({
            taskId: uuidv4(),
            creatorId,
            videoUrl,
            title,
            usesThirdPartyMusic,
            tier5Result: result
        });

        console.log(`Processed video for creator ${creatorId}:`, result);
        return result;

    } catch (error) {
        console.error(`Error processing video for creator ${creatorId}:`, error.message);
        return { error: 'Processing failed' };
    }
}

module.exports = { processVideo };
