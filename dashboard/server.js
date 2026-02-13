# Minimal example:
const express = require('express'); const app 
= express(); const PORT = 4000; 
app.get('/videos', (req, res) => res.json({ 
message: "List of videos" }));
app.listen(PORT, () => console.log(`Dashboard running on port ${PORT}`));
