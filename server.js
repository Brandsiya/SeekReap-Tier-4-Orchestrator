// server.js
require('dotenv').config(); // Load .env first

const express = require('express');
const { Pool } = require('pg');

// ----- EXPRESS SETUP -----
const app = express();
const PORT = process.env.PORT || 10000;

// Avoid multiple listeners if hot-reloading
if (app.listening) {
  app.close();
}

// ----- POSTGRES (NEON) SETUP -----
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false } // Required for Neon SSL
});

// Test DB connection
(async () => {
  try {
    const client = await pool.connect();
    console.log("Connected to Neon DB successfully");
    // Example: create table if not exists
    await client.query(`
      CREATE TABLE IF NOT EXISTS test_table (
        id SERIAL PRIMARY KEY,
        data TEXT
      );
    `);
    client.release();
  } catch (err) {
    console.error("Database connection failed:", err);
  }
})();

// ----- ROUTES -----
app.get('/', (req, res) => {
  res.send('SeekReap Tier-4 Server is running!');
});

// ----- SERVER LISTEN -----
const server = app.listen(PORT, () => {
  console.log(`SeekReap Tier-4 running on port ${PORT}`);
});

// Handle port in use errors gracefully
server.on('error', err => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${PORT} is already in use. Kill the old process and try again.`);
  } else {
    console.error(err);
  }
});
