const express = require('express');
const axios = require('axios');
const router = express.Router();

// Configuration
const TIER3_URL = process.env.TIER3_URL || "https://seekreap-tier-3-private.onrender.com";
const TIER5_URL = process.env.TIER5_URL || "https://seekreap-tier-5-orchestrator.onrender.com";

// Health check endpoint
router.get('/health', (req, res) => {
    res.json({ 
        status: 'healthy', 
        tier: 4,
        timestamp: new Date().toISOString(),
        services: {
            tier3: TIER3_URL,
            tier5: TIER5_URL
        }
    });
});

// Forward envelope to Tier-3 for processing
router.post('/process-envelope', async (req, res) => {
    try {
        const envelope = req.body;
        console.log(`Tier-4 received envelope: ${envelope.id}`);
        console.log(`Policy: ${envelope.orchestration_policy}`);
        
        // Validate envelope structure
        if (!envelope.id || !envelope.orchestration_policy || !envelope.payload) {
            return res.status(400).json({ 
                error: 'Invalid envelope structure',
                required: ['id', 'orchestration_policy', 'payload']
            });
        }
        
        // Forward to Tier-3 for decision
        console.log(`Forwarding to Tier-3: ${TIER3_URL}/process-envelope`);
        const tier3Response = await axios.post(`${TIER3_URL}/process-envelope`, envelope, {
            timeout: 10000,
            headers: { 'Content-Type': 'application/json' }
        });
        
        // Add routing metadata
        const response = {
            ...tier3Response.data,
            routing: {
                tier3_processed: true,
                tier4_timestamp: new Date().toISOString(),
                envelope_id: envelope.id
            }
        };
        
        // Log for audit
        console.log(`Envelope ${envelope.id} processed by Tier-3: ${tier3Response.data.decision}`);
        
        res.json(response);
        
    } catch (error) {
        console.error(`Error processing envelope: ${error.message}`);
        
        // Handle different error types
        if (error.code === 'ECONNREFUSED') {
            res.status(503).json({ 
                error: 'Tier-3 service unavailable',
                envelope_id: req.body.id,
                timestamp: new Date().toISOString()
            });
        } else if (error.response) {
            // Tier-3 responded with an error
            res.status(error.response.status).json({
                error: 'Tier-3 processing error',
                details: error.response.data,
                envelope_id: req.body.id
            });
        } else {
            res.status(500).json({ 
                error: 'Internal server error',
                message: error.message,
                envelope_id: req.body.id
            });
        }
    }
});

// Batch process multiple envelopes
router.post('/process-batch', async (req, res) => {
    try {
        const envelopes = req.body.envelopes || req.body;
        if (!Array.isArray(envelopes)) {
            return res.status(400).json({ error: 'Expected array of envelopes' });
        }
        
        console.log(`Tier-4 received batch of ${envelopes.length} envelopes`);
        
        // Process envelopes in parallel with concurrency limit
        const concurrencyLimit = 5;
        const results = [];
        
        for (let i = 0; i < envelopes.length; i += concurrencyLimit) {
            const batch = envelopes.slice(i, i + concurrencyLimit);
            const batchPromises = batch.map(async (envelope) => {
                try {
                    const response = await axios.post(`${TIER3_URL}/process-envelope`, envelope, {
                        timeout: 10000
                    });
                    return {
                        ...response.data,
                        routing: {
                            tier3_processed: true,
                            tier4_timestamp: new Date().toISOString(),
                            envelope_id: envelope.id
                        }
                    };
                } catch (error) {
                    console.error(`Batch error for envelope ${envelope.id}: ${error.message}`);
                    return {
                        envelope_id: envelope.id,
                        decision: 'ERROR',
                        confidence: 0,
                        risk_factors: [`Routing error: ${error.message}`],
                        appeal_text: null,
                        routing: {
                            tier3_processed: false,
                            tier4_timestamp: new Date().toISOString(),
                            error: error.message
                        }
                    };
                }
            });
            
            const batchResults = await Promise.all(batchPromises);
            results.push(...batchResults);
        }
        
        res.json({ results, total: results.length });
        
    } catch (error) {
        console.error(`Batch processing error: ${error.message}`);
        res.status(500).json({ error: 'Batch processing failed', message: error.message });
    }
});

// Legacy endpoint support (for backward compatibility)
router.post('/tier3-forward', async (req, res) => {
    try {
        const response = await axios.post(`${TIER3_URL}/process-envelope`, req.body);
        res.json({ 
            tier3_response: response.data,
            note: 'This endpoint is deprecated. Use /process-envelope instead.'
        });
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
