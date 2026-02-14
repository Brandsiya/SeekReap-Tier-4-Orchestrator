const express = require('express');
const path = require('path');

const app = express();
const PORT = 3000;

// Serve your standalone index.html at root
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

// Serve other static files (if any)
app.use(express.static(__dirname));

app.listen(PORT, () => {
    console.log(`Test server running at http://localhost:${PORT}`);
});
