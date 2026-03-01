const axios = require('axios');

const TIER4_URL = process.env.TIER4_URL || 'http://localhost:10000';

async function testTier4() {
    console.log('=== Testing Tier-4 Orchestrator ===\n');
    
    // Test health endpoint
    try {
        const health = await axios.get(`${TIER4_URL}/health`);
        console.log('✅ Health check:', health.data);
    } catch (error) {
        console.log('❌ Health check failed:', error.message);
    }
    
    // Create a test envelope
    const testEnvelope = {
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
    
    console.log('\n📦 Test envelope:', testEnvelope.id);
    
    // Test single envelope processing
    try {
        console.log('\n1. Testing single envelope processing...');
        const response = await axios.post(`${TIER4_URL}/process-envelope`, testEnvelope);
        console.log('   ✅ Success');
        console.log('   Decision:', response.data.decision);
        console.log('   Confidence:', response.data.confidence);
        console.log('   Routing:', response.data.routing);
    } catch (error) {
        console.log('   ❌ Failed:', error.message);
        if (error.response) {
            console.log('   Response:', error.response.data);
        }
    }
    
    // Test batch processing
    try {
        console.log('\n2. Testing batch processing...');
        const batch = [
            testEnvelope,
            {
                ...testEnvelope,
                id: `test-envelope-${Date.now() + 1}`,
                orchestration_policy: "behavior_recording",
                payload: { type: "playback", intensity: 0.8 }
            }
        ];
        const response = await axios.post(`${TIER4_URL}/process-batch`, { envelopes: batch });
        console.log('   ✅ Success');
        console.log(`   Processed ${response.data.results.length} envelopes`);
        response.data.results.forEach((r, i) => {
            console.log(`   Result ${i+1}: ${r.decision} (${r.confidence})`);
        });
    } catch (error) {
        console.log('   ❌ Failed:', error.message);
    }
    
    // Test the test endpoint
    try {
        console.log('\n3. Testing test endpoint...');
        const response = await axios.post(`${TIER4_URL}/test-envelope`);
        console.log('   ✅ Success');
        console.log('   Tier-3 response:', response.data.tier3_response.decision);
    } catch (error) {
        console.log('   ❌ Failed:', error.message);
    }
}

testTier4();
