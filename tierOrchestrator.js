const express = require('express');
const axios = require('axios');
const router = express.Router();

const TIER3_URL = process.env.TIER3_URL || "http://localhost:10001";
const TIER5_URL = process.env.TIER5_URL || "https://seekreap-tier-5-orchestrator.onrender.com";

router.get('/health', (req, res) => {
    res.json({
        status: 'healthy',
        tier: 4,
        timestamp: new Date().toISOString(),
        services: { tier3: TIER3_URL, tier5: TIER5_URL }
    });
});

router.post('/process-envelope', async (req, res) => {
    try {
        const envelope = req.body;
        console.log(`Tier-4 received envelope: ${envelope.id}`);
        
        const tier3Response = await axios.post(`${TIER3_URL}/process-envelope`, envelope, {
            timeout: 10000,
            headers: { 'Content-Type': 'application/json' }
        });
        
        res.json({
            ...tier3Response.data,
            routing: {
                tier3_processed: true,
                tier4_timestamp: new Date().toISOString(),
                envelope_id: envelope.id
            }
        });
        
    } catch (error) {
        console.error(`Error: ${error.message}`);
        res.status(500).json({
            error: 'Internal server error',
            message: error.message,
            envelope_id: req.body.id
        });
    }
});

router.post('/process-batch', async (req, res) => {
    try {
        const envelopes = req.body.envelopes || req.body;
        if (!Array.isArray(envelopes)) {
            return res.status(400).json({ error: 'Expected array of envelopes' });
        }
        
        const results = [];
        for (const envelope of envelopes) {
            try {
                const response = await axios.post(`${TIER3_URL}/process-envelope`, envelope);
                results.push({
                    ...response.data,
                    routing: { tier3_processed: true, envelope_id: envelope.id }
                });
            } catch (error) {
                results.push({
                    envelope_id: envelope.id,
                    decision: 'ERROR',
                    confidence: 0,
                    risk_factors: [`Error: ${error.message}`]
                });
            }
        }
        res.json({ results });
        
    } catch (error) {
        res.status(500).json({ error: 'Batch processing failed' });
    }
});

router.post('/tier3-forward', async (req, res) => {
    try {
        const response = await axios.post(`${TIER3_URL}/process-envelope`, req.body);
        res.json({ tier3_response: response.data });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

router.post('/tier5-forward', async (req, res) => {
    try {
        const response = await axios.post(`${TIER5_URL}/process`, req.body);
        res.json({ tier5_response: response.data });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

module.exports = router;
