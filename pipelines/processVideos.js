const axios = require('axios');
const fs = require('fs');
const path = require('path');
const PDFDocument = require('pdfkit');
const { v4: uuidv4 } = require('uuid');

const TIER5_URL = 'https://seekreap-tier-5-orchestrator.onrender.com/task';

async function processVideo(videoData) {
    try {
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
            timestamp: new Date().toISOString(),
            tier5Result: result
        };

        const reportsDir = path.join(__dirname, 'reports');
        if (!fs.existsSync(reportsDir)) fs.mkdirSync(reportsDir);
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
        doc.text(`Timestamp: ${report.timestamp}`);
        doc.moveDown();
        doc.text('--- Tier-5 Evaluation ---');
        doc.text(`Rule Check: ${result.ruleCheck}`);
        doc.text(`AI Prediction: ${result.aiPrediction}`);
        doc.text(`Final Decision: ${result.finalDecision}`);
        doc.end();

        console.log(`✅ Report generated: ${pdfPath}`);
        return report;

    } catch (err) {
        console.error('Error processing video:', err.message);
        throw new Error('Processing failed');
    }
}

module.exports = { processVideo };
