const express = require("express");
const processVideo = require("../pipelines/processVideos");

const app = express();
app.use(express.json());

app.post("/upload", async (req, res) => {
    try {
        const { videoUrl, title, usesThirdPartyMusic } = req.body;

        if (!videoUrl) {
            return res.status(400).json({ error: "Missing videoUrl" });
        }

        const metadata = {
            title,
            usesThirdPartyMusic
        };

        const result = await processVideo(videoUrl, metadata);

        if (!result.success) {
            return res.status(500).json({ error: result.error });
        }

        return res.json({
            success: true,
            decision: result.tier5.finalDecision,
            details: result.tier5
        });

    } catch (err) {
        console.error("Upload route error:", err.message);
        return res.status(500).json({ error: "Internal processing error" });
    }
});

const PORT = process.env.PORT || 10000;
app.listen(PORT, () => {
    console.log(`Tier-4 API running on port ${PORT}`);
});
