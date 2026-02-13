const express = require("express");
const axios = require("axios");
const { v4: uuidv4 } = require("uuid");
const fs = require("fs");
const path = require("path");

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 10000;

// ==============================
// FILE STORAGE (FREE INSTANCE SAFE)
// ==============================
const auditLogPath = path.join(__dirname, "audit-log.json");
const creatorProfilePath = path.join(__dirname, "creator-profiles.json");

if (!fs.existsSync(auditLogPath)) {
    fs.writeFileSync(auditLogPath, JSON.stringify([]));
}

if (!fs.existsSync(creatorProfilePath)) {
    fs.writeFileSync(creatorProfilePath, JSON.stringify({}));
}

// ==============================
// RISK SCORING ENGINE
// ==============================
function calculateRiskScore({ finalDecision, reason, metadata, ruleCheck, aiPrediction }) {
    let score = 0;

    if (finalDecision === "Rejected") score += 40;
    if (ruleCheck !== aiPrediction) score += 15;

    if (reason.severity === "HIGH") score += 30;
    if (reason.severity === "MEDIUM") score += 15;

    if (metadata) {
        if (metadata.previousViolations > 0) {
            score += Math.min(metadata.previousViolations * 5, 20);
        }
    }

    if (score > 100) score = 100;

    let tier = "LOW";
    if (score >= 30 && score < 60) tier = "MEDIUM";
    if (score >= 60 && score < 80) tier = "HIGH";
    if (score >= 80) tier = "CRITICAL";

    return { score, tier };
}

// ==============================
// CREATOR AGGREGATION ENGINE
// ==============================
function updateCreatorProfile(creatorId, videoRiskScore) {

    const profiles = JSON.parse(fs.readFileSync(creatorProfilePath));

    if (!profiles[creatorId]) {
        profiles[creatorId] = {
            totalVideos: 0,
            cumulativeRisk: 0,
            averageRisk: 0,
            recentScores: [],
            weightedRisk: 0,
            creatorRiskScore: 0,
            riskTier: "LOW",
            lastUpdated: null
        };
    }

    const profile = profiles[creatorId];

    profile.totalVideos += 1;
    profile.cumulativeRisk += videoRiskScore;
    profile.averageRisk = profile.cumulativeRisk / profile.totalVideos;

    // Keep last 5 for weighting
    profile.recentScores.push(videoRiskScore);
    if (profile.recentScores.length > 5) {
        profile.recentScores.shift();
    }

    const recentAvg =
        profile.recentScores.reduce((a, b) => a + b, 0) /
        profile.recentScores.length;

    profile.weightedRisk =
        (0.6 * recentAvg) +
        (0.4 * profile.averageRisk);

    profile.creatorRiskScore = Math.round(profile.weightedRisk);

    if (profile.creatorRiskScore < 30) profile.riskTier = "LOW";
    else if (profile.creatorRiskScore < 60) profile.riskTier = "MEDIUM";
    else if (profile.creatorRiskScore < 80) profile.riskTier = "HIGH";
    else profile.riskTier = "CRITICAL";

    profile.lastUpdated = new Date().toISOString();

    profiles[creatorId] = profile;
    fs.writeFileSync(creatorProfilePath, JSON.stringify(profiles, null, 2));

    return profile;
}

// ==============================
// PROCESS VIDEO
// ==============================
app.post("/process-video", async (req, res) => {
    try {

        const { creatorId, videoUrl, metadata } = req.body;

        if (!creatorId || !videoUrl) {
            return res.status(400).json({ error: "creatorId and videoUrl required" });
        }

        const tier5Response = await axios.post(
            "https://seekreap-tier5.onrender.com/evaluate",
            { videoUrl, metadata }
        );

        const { ruleCheck, aiPrediction, finalDecision } = tier5Response.data;

        const reason = {
            reasonCode: finalDecision === "Rejected" ? "POLICY_RISK" : "NO_VIOLATION",
            severity: finalDecision === "Rejected" ? "HIGH" : "NONE"
        };

        const risk = calculateRiskScore({
            finalDecision,
            reason,
            metadata,
            ruleCheck,
            aiPrediction
        });

        const decisionRecord = {
            decisionId: uuidv4(),
            creatorId,
            timestamp: new Date().toISOString(),
            videoUrl,
            metadata,
            ruleCheck,
            aiPrediction,
            finalDecision,
            videoRiskScore: risk.score,
            videoRiskTier: risk.tier
        };

        // Save audit log
        const logs = JSON.parse(fs.readFileSync(auditLogPath));
        logs.push(decisionRecord);
        fs.writeFileSync(auditLogPath, JSON.stringify(logs, null, 2));

        // 🔥 Update Creator Profile
        const creatorProfile = updateCreatorProfile(
            creatorId,
            risk.score
        );

        return res.json({
            success: true,
            decision: decisionRecord,
            creatorProfile
        });

    } catch (error) {
        console.error("Processing error:", error.message);
        return res.status(500).json({ error: "Processing failed" });
    }
});

// ==============================
// VIEW CREATOR PROFILE
// ==============================
app.get("/creator/:id", (req, res) => {
    const profiles = JSON.parse(fs.readFileSync(creatorProfilePath));
    const profile = profiles[req.params.id];

    if (!profile) {
        return res.status(404).json({ error: "Creator not found" });
    }

    res.json(profile);
});

app.listen(PORT, () => {
    console.log(`Tier-4 API with Creator Aggregation running on port ${PORT}`);
});
