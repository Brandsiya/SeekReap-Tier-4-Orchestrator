const axios = require("axios");

const TIER5_BASE_URL = "https://seekreap-tier-5-orchestrator.onrender.com";
const TIER5_TASK_URL = `${TIER5_BASE_URL}/task`;
const TIER5_PING_URL = `${TIER5_BASE_URL}/ping`;

// Wake Tier-5 (handles Render cold starts)
async function wakeTier5() {
    try {
        console.log("Waking Tier-5 service...");
        await axios.get(TIER5_PING_URL, { timeout: 15000 });
        console.log("Tier-5 is awake.");
    } catch (err) {
        console.log("Tier-5 wake attempt failed (may still be starting)...");
    }
}

async function processVideo(videoUrl, metadata = {}) {
    try {
        console.log("Processing video:", videoUrl);

        // Step 1 — Wake Tier-5 (prevents 502 cold start)
        await wakeTier5();

        // Step 2 — Send task to Tier-5
        const response = await axios.post(
            TIER5_TASK_URL,
            {
                video: videoUrl,
                metadata: metadata
            },
            {
                headers: { "Content-Type": "application/json" },
                timeout: 30000 // 30 seconds for cold start safety
            }
        );

        if (!response.data || !response.data.success) {
            throw new Error("Tier-5 returned invalid response");
        }

        console.log("Tier-5 Decision:", response.data.result);

        return {
            success: true,
            tier5: response.data.result
        };

    } catch (error) {

        console.error("Tier-5 integration error:", 
            error.response?.data || error.message
        );

        return {
            success: false,
            error: "Tier-5 processing failed"
        };
    }
}

module.exports = processVideo;
