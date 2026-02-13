const fs = require('fs');
const path = require('path');
const DB_FILE = path.join(__dirname, '../seekreap-tier4-db/videos.json');

function getVideosByDecision(decision) {
  const db = JSON.parse(fs.readFileSync(DB_FILE));
  return db.filter(entry => entry.tier5Result.finalDecision === decision);
}

// Example usage
console.log('Approved videos:', getVideosByDecision('Approved'));
console.log('Manual Review videos:', getVideosByDecision('Manual Review'));
