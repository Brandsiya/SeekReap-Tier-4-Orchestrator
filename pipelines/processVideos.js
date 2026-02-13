const axios = require('axios');
const fs = require('fs');
const path = require('path');
const PDFDocument = require('pdfkit');
const { v4: uuidv4 } = require('uuid');

const TIER5_URL = process.env.TIER5_URL;
if (!TIER5_URL) {
  console.error('❌ TIER5_URL environment variable is not set');
  process.exit(1);
}

async function processVideo(videoData) {
  try {
    // Call live Tier-5 service
    const response = await axios.post(TIER5_URL, {
      metadata: {
        title: videoData.title,
        usesThirdPartyMusic: videoData.usesThirdPartyMusic
      }
    });

    const result = response.data.result;

    const report = {
      reportId: uuidv4(),
      creatorId: videoData.creatorId,
      videoUrl: videoData.videoUrl,
      title: videoData.title,
      timestamp: new Date().toISOString(),
      tier5Result: result
    };

    const reportsDir = path.join(__dirname, 'reports');
    if (!fs.existsSync(reportsDir)) fs.mkdirSync(reportsDir, { recursive: true });

    const reportJsonPath = path.join(reportsDir, `${report.reportId}.json`);
    fs.writeFileSync(reportJsonPath, JSON.stringify(report, null, 2));

    const pdfPath = path.join(reportsDir, `${report.reportId}.pdf`);
    const doc = new PDFDocument({ margin: 30 });
    doc.pipe(fs.createWriteStream(pdfPath));
    doc.fontSize(20).text('SeekReap Demonetization Proof', { align: 'center' });
    doc.moveDown();
    doc.fontSize(12).text(`Report ID: ${report.reportId}`);
    doc.text(`Creator ID: ${report.creatorId}`);
    doc.text(`Video URL: ${report.videoUrl}`);
    doc.text(`Title: ${report.title}`);
    doc.text(`Timestamp: ${report.timestamp}`);
    doc.moveDown();
    doc.text('--- Tier-5 Evaluation ---');
    doc.text(`Rule Check: ${result.ruleCheck}`);
    doc.text(`AI Prediction: ${result.aiPrediction}`);
    doc.text(`Final Decision: ${result.finalDecision}`);
    doc.end();

    return report;
  } catch (err) {
    console.error('❌ Error processing video:', err.message);
    throw new Error('Processing failed');
  }
}

module.exports = { processVideo };
