const fs = require('fs');
const path = require('path');
const sendToTier5 = require('../utils/sendToTier5');

const VIDEO_FOLDER = '/home/userland/videos'; // your video folder
const DB_FILE = path.join(__dirname, '../seekreap-tier4-db/videos.json');

// Load existing DB
let db = [];
if (fs.existsSync(DB_FILE)) {
  db = JSON.parse(fs.readFileSync(DB_FILE));
}

async function processAllVideos() {
  const files = fs.readdirSync(VIDEO_FOLDER).filter(f => f.endsWith('.mp4'));

  for (const file of files) {
    const videoPath = path.join(VIDEO_FOLDER, file);
    const metadata = {
      title: path.parse(file).name,
      usesThirdPartyMusic: true
    };

    console.log(`Processing ${file} → sending to Tier-5...`);

    const result = await sendToTier5(videoPath, metadata);

    if (!result) {
      console.error(`Failed to get Tier-5 decision for ${file}`);
      continue;
    }

    console.log(`Tier-5 decision for ${file}:`, result.result.finalDecision);

    // Save to Tier-4 DB
    const dbEntry = {
      video: file,
      metadata,
      tier5Result: result.result,
      timestamp: new Date().toISOString()
    };
    db.push(dbEntry);
    fs.writeFileSync(DB_FILE, JSON.stringify(db, null, 2));
  }

  console.log('All videos processed ✅');
}

processAllVideos();
