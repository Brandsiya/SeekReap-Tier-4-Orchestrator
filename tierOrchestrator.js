const express = require('express');
const axios = require('axios');
const router = express.Router();

const TIER3_URL = "https://seekreap-tier-3-private.onrender.com";
const TIER5_URL = "https://seekreap-tier-5-orchestrator.onrender.com";

// Forward to Tier-3
router.post('/tier3-forward', async (req, res) => {
    try {
        const payload = req.body;
        const response = await axios.post(`${TIER3_URL}/compute`, payload);
        res.json({ tier3_response: response.data });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

// Forward to Tier-5
router.post('/tier5-forward', async (req, res) => {
    try {
        const payload = req.body;
        const response = await axios.post(`${TIER5_URL}/process`, payload);
        res.json({ tier5_response: response.data });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

module.exports = router;
