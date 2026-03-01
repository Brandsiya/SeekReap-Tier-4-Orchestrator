require('dotenv').config();
const express = require('express');
const tierOrchestrator = require('./tierOrchestrator');

const app = express();
const PORT = process.env.PORT || 10000;

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept');
    res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    if (req.method === 'OPTIONS') return res.sendStatus(200);
    next();
});

app.use('/', tierOrchestrator);

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

app.post('/test-envelope', async (req, res) => {
    try {
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
        
        const axios = require('axios');
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

app.use((err, req, res, next) => {
    console.error('Unhandled error:', err);
    res.status(500).json({ error: 'Internal server error', message: err.message });
});

app.listen(PORT, () => {
    console.log(`🚀 Tier-4 Orchestrator running on port ${PORT}`);
    console.log(`📡 Tier-3 URL: ${process.env.TIER3_URL || 'http://localhost:10001'}`);
    console.log(`📝 Endpoints:`);
    console.log(`   GET  /health`);
    console.log(`   POST /process-envelope`);
    console.log(`   POST /process-batch`);
    console.log(`   POST /test-envelope`);
});
