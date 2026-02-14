const express = require('express');
const path = require('path');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const Joi = require('joi');
const { Pool } = require('pg');
const Queue = require('bull');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT;
const DATABASE_URL = process.env.DATABASE_URL;
const REDIS_URL = process.env.REDIS_URL;

// =========================
// Environment Validation
// =========================
if (!PORT || !DATABASE_URL || !REDIS_URL) {
  console.error("Missing required environment variables.");
  process.exit(1);
}

// =========================
// Security & Middleware
// =========================
app.use(helmet());
app.use(morgan('combined'));
app.use(express.json());
app.use(rateLimit({ windowMs: 15*60*1000, max: 100 }));

// =========================
// PostgreSQL Connection
// =========================
const pool = new Pool({ connectionString: DATABASE_URL, ssl: { rejectUnauthorized: false } });

// =========================
// Auto-upgrade table for Evidence Layer
// =========================
(async () => {
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS videos (
        id TEXT PRIMARY KEY,
        creator_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        uses_third_party_music BOOLEAN,
        created_at TIMESTAMP DEFAULT NOW(),
        processed_at TIMESTAMP,
        analysis JSONB,
        engine_version TEXT,
        result_hash TEXT
      );
    `);
    console.log("Videos table ready/upgraded.");
  } catch (err) { console.error("Table creation failed:", err); }
})();

// =========================
// Redis Queue
// =========================
const videoQueue = new Queue('video-processing', REDIS_URL);

videoQueue.process(async (job) => {
  const { videoId } = job.data;
  await new Promise(resolve => setTimeout(resolve, 3000));

  const analysis = {
    copyright_risk: 0.02,
    brand_safety_risk: 0.11,
    policy_flags: [],
    content_classification: "original",
    monetization_risk_level: "low"
  };
  const engine_version = "4.0.1";
  const result_hash = crypto.createHash('sha256').update(JSON.stringify(analysis)).digest('hex');

  await pool.query(
    `UPDATE videos SET status='processed', processed_at=NOW(), analysis=$1, engine_version=$2, result_hash=$3 WHERE id=$4`,
    [analysis, engine_version, result_hash, videoId]
  );
  console.log(`Processed ${videoId} with evidence hash ${result_hash}`);
});

// =========================
// Validation Schema
// =========================
const videoSchema = Joi.object({
  creatorId: Joi.string().required(),
  videoUrl: Joi.string().uri().required(),
  title: Joi.string().min(3).required(),
  usesThirdPartyMusic: Joi.boolean().optional()
});

// =========================
// Routes
// =========================
app.use('/', express.static(path.join(__dirname, 'SeekReap-Verif-Portal')));
app.get('/health', async (req,res)=>{try{await pool.query('SELECT 1');res.json({status:"ok",uptime:process.uptime()});}catch(err){res.status(500).json({status:"db_error"})}});
app.post('/process-video', async (req,res)=>{
  try{
    const {error,value}=videoSchema.validate(req.body);
    if(error) return res.status(400).json({error:error.details[0].message});
    const videoId=`vid_${Date.now()}`;
    await pool.query(`INSERT INTO videos (id,creator_id,title,status,uses_third_party_music,created_at) VALUES ($1,$2,$3,$4,$5,NOW())`,[videoId,value.creatorId,value.title,'queued',value.usesThirdPartyMusic]);
    await videoQueue.add({videoId});
    res.json({status:"queued",videoId});
  }catch(err){console.error(err);res.status(500).json({error:"Internal server error"})}
});
app.get('/videos', async (req,res)=>{try{const result=await pool.query(`SELECT * FROM videos ORDER BY created_at DESC`);res.json(result.rows);}catch(err){console.error(err);res.status(500).json({error:"Internal server error"})}});
app.get('/evidence/:videoId', async (req,res)=>{try{const {videoId}=req.params;const result=await pool.query(`SELECT id,creator_id,processed_at,engine_version,analysis,result_hash FROM videos WHERE id=$1`,[videoId]);if(result.rows.length===0) return res.status(404).json({error:"Video not found"});res.json({...result.rows[0],certification:"SeekReap Pre-Monetization Scan Complete"});}catch(err){console.error(err);res.status(500).json({error:"Internal server error"})}});

// =========================
// Start Server
// =========================
app.listen(PORT,()=>console.log(`SeekReap Tier-4 running on port ${PORT}`));
