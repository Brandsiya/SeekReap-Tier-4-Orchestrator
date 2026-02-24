require('dotenv').config();
const express = require('express');
const tierOrchestrator = require('./tierOrchestrator');
const axios = require('axios');

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// CORS middleware
app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept');
    res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    if (req.method === 'OPTIONS') {
        return res.sendStatus(200);
    }
    next();
});

// Routes
app.use('/', tierOrchestrator);

// Root endpoint
app.get('/', (req, res) => {
    res.json({ 
        message: 'SeekReap Tier-4 Orchestrator running',
        version: '2.0.0',
        endpoints: {
            health: 'GET /health',
            processEnvelope: 'POST /process-envelope',
            processBatch: 'POST /process-batch',
            tier3Forward: 'POST /tier3-forward (deprecated)',
            tier5Forward: 'POST /tier5-forward'
        }
    });
});

// Test endpoint for envelope routing
app.post('/test-envelope', async (req, res) => {
    try {
        // Create a test envelope if none provided
        const envelope = req.body.envelope || {
            id: `test-envelope-${Date.now()}`,
            timestamp: Date.now() / 1000,
            payload: {
                status: "verified",
                score: 0.85,
                behaviors: ["b1", "b2", "b3"]
            },
            schema_version: "tier2-envelope-v1",
            orchestration_policy: "reap_verification",
            signature: `tier2-semantic-reap_verification-${Date.now()}-test`,
            metadata: { source: "tier4_test" }
        };
        
        // Forward to Tier-3
        const tier3Response = await axios.post(
            `${process.env.TIER3_URL || 'http://localhost:10001'}/process-envelope`,
            envelope
        );
        
        res.json({
            test: true,
            envelope: envelope,
            tier3_response: tier3Response.data,
            timestamp: new Date().toISOString()
        });
        
    } catch (error) {
        res.status(500).json({ 
            test: true,
            error: error.message,
            details: error.response?.data || 'No details'
        });
    }
});

// Error handling middleware
app.use((err, req, res, next) => {
    console.error('Unhandled error:', err);
    res.status(500).json({ error: 'Internal server error', message: err.message });
});

// Start server
app.listen(PORT, () => {
    console.log(`🚀 Tier-4 Orchestrator running on port ${PORT}`);
    console.log(`📡 Tier-3 URL: ${process.env.TIER3_URL || 'http://localhost:10001'}`);
    console.log(`📡 Tier-5 URL: ${process.env.TIER5_URL || 'https://seekreap-tier-5-orchestrator.onrender.com'}`);
    console.log(`📝 Endpoints:`);
    console.log(`   GET  /health`);
    console.log(`   POST /process-envelope`);
    console.log(`   POST /process-batch`);
    console.log(`   POST /test-envelope`);
});
