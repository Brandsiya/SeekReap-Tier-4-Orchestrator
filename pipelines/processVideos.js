const fs = require('fs');
const path = require('path');
const PDFDocument = require('pdfkit');
const axios = require('axios');
const { v4: uuidv4 } = require('uuid');

const dbPath = path.join(__dirname, '../seekreap-tier4-db/videos.json');
if (!fs.existsSync(dbPath)) fs.writeFileSync(dbPath, JSON.stringify({}));

async function processVideo({ creatorId, videoUrl, title, usesThirdPartyMusic }) {
  try {
    const db = JSON.parse(fs.readFileSync(dbPath));
    const videoId = uuidv4();

    // Simulate processing
    const processed = {
      id: videoId,
      creatorId,
      title,
      url: videoUrl,
      usesThirdPartyMusic,
      status: 'processed',
      timestamp: new Date().toISOString()
    };

    db[videoId] = processed;
    fs.writeFileSync(dbPath, JSON.stringify(db, null, 2));

    // Create PDF report
    const pdfPath = path.join(__dirname, `../seekreap-tier4-db/${videoId}.pdf`);
    const doc = new PDFDocument();
    doc.pipe(fs.createWriteStream(pdfPath));
    doc.text(`Video Report\n\nID: ${videoId}\nCreator: ${creatorId}\nTitle: ${title}\nUses 3rd-party music: ${usesThirdPartyMusic}`);
    doc.end();

    return processed;
  } catch (err) {
    console.error('Video processing error:', err);
    throw err;
  }
}

module.exports = { processVideo };
