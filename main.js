const sendToTier5 = require('./utils/sendToTier5');

async function processVideo(videoPath, metadata) {
  console.log('Tier-4 processed video, sending to Tier-5...');
  
  const tier5Result = await sendToTier5(videoPath, metadata);

  if (tier5Result) {
    console.log('Tier-5 decision:', tier5Result.result.finalDecision);
    // Here you can save to Tier-4 DB or further process
  }
}

// Example usage
processVideo('/home/userland/sample.mp4', { title: 'Test Video', usesThirdPartyMusic: true });
