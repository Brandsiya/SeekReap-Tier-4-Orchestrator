const axios = require('axios');
const fs = require('fs');
const path = require('path');

const DB_FILE = path.join(__dirname, 'seekreap-tier4-db/videos.json');

// Load DB or initialize
let db = [];
if (fs.existsSync(DB_FILE)) {
  db = JSON.parse(fs.readFileSync(DB_FILE));
}

// Function to send to Tier-5
async function sendToTier5(videoUrl, metadata) {
  try {
    const response = await axios.post(
      'https://seekreap-tier-5-orchestrator.onrender.com/task', // live Tier-5 URL
      { video: videoUrl, metadata },
      { headers: { 'Content-Type': 'application/json' } }
    );
    return response.data;
  } catch (err) {
    console.error('Tier-5 integration error:', err.message);
    return null;
  }
}

// Main function: process a video (triggered by API/webhook)
async function processVideo(videoUrl, metadata) {
  console.log('Processing video:', videoUrl);

  const result = await sendToTier5(videoUrl, metadata);
  if (!result) {
    console.error('Failed to get Tier-5 decision');
    return null;
  }

  console.log('Tier-5 decision:', result.result.finalDecision);

  // Save to DB
  db.push({
    video: videoUrl,
    metadata,
    tier5Result: result.result,
    timestamp: new Date().toISOString()
  });
  fs.writeFileSync(DB_FILE, JSON.stringify(db, null, 2));

  return result.result.finalDecision;
}

// Export for external triggers (like API)
module.exports = { processVideo };
