const axios = require('axios');

async function sendToTier5(videoPath, metadata) {
  try {
    const response = await axios.post(
      'https://seekreap-tier-5-orchestrator.onrender.com/task',
      {
        video: videoPath,
        metadata
      },
      {
        headers: { 'Content-Type': 'application/json' }
      }
    );
    return response.data;
  } catch (err) {
    console.error('Tier-5 integration error:', err.message);
    return null;
  }
}

module.exports = sendToTier5;
