const axios = require('axios');

async function testFullPipeline() {
    console.log('=== Testing Full SeekReap Pipeline ===\n');
    
    // Create a test envelope (simulating what Tier-2 would produce)
    const envelope = {
        id: `reap-verification-${Date.now()}`,
        timestamp: Date.now() / 1000,
        payload: {
            status: "verified",
            score: 0.92,
            behaviors: ["b1", "b2", "b3", "b4"]
        },
        schema_version: "tier2-envelope-v1",
        orchestration_policy: "reap_verification",
        signature: `tier2-semantic-reap_verification-${Date.now()}-test`,
        metadata: { source: "full_pipeline_test" }
    };
    
    console.log('1. Sending envelope to Tier-4...');
    console.log('   Envelope ID:', envelope.id);
    console.log('   Policy:', envelope.orchestration_policy);
    
    try {
        // Send to Tier-4
        const tier4Response = await axios.post('http://localhost:10000/process-envelope', envelope, {
            timeout: 5000
        });
        
        console.log('\n2. Tier-4 Response:');
        console.log('   Status:', tier4Response.status);
        console.log('   Decision:', tier4Response.data.decision);
        console.log('   Confidence:', tier4Response.data.confidence);
        console.log('   Risk Factors:', tier4Response.data.risk_factors);
        console.log('   Appeal Text:', tier4Response.data.appeal_text);
        console.log('   Routing:', tier4Response.data.routing);
        
        if (tier4Response.data.decision === 'APPROVE') {
            console.log('\n✅ Pipeline test PASSED!');
        } else {
            console.log('\n⚠️ Unexpected decision:', tier4Response.data.decision);
        }
        
    } catch (error) {
        console.error('\n❌ Pipeline test FAILED:', error.message);
        if (error.response) {
            console.error('Response data:', error.response.data);
        }
    }
}

testFullPipeline();
