const axios = require('axios');

async function simpleTest() {
    console.log('=== Simple Tier-4 Test ===\n');
    
    // Test health
    try {
        const health = await axios.get('http://localhost:10000/health');
        console.log('✅ Health check passed');
        console.log('   Response:', health.data);
    } catch (error) {
        console.log('❌ Health check failed:', error.message);
        return;
    }
    
    // Test envelope
    const envelope = {
        id: 'test-123',
        timestamp: Date.now() / 1000,
        payload: { test: 'data' },
        schema_version: 'tier2-envelope-v1',
        orchestration_policy: 'test',
        signature: 'test-signature'
    };
    
    try {
        const response = await axios.post('http://localhost:10000/process-envelope', envelope);
        console.log('\n✅ Envelope processed');
        console.log('   Response:', response.data);
    } catch (error) {
        console.log('\n❌ Envelope processing failed:', error.message);
        if (error.response) {
            console.log('   Response data:', error.response.data);
        }
    }
}

simpleTest();
