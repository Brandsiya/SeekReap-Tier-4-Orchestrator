const path = require('path');
const express = require('express');
const app = express();

// Serve portal folder at /portal
app.use('/portal', express.static(path.resolve(__dirname, 'SeekReap-Verif-Portal')));

// Redirect root to /portal
app.get('/', (req, res) => {
  res.redirect('/portal');
});

// Catch-all for 404s
app.use((req, res) => {
  res.status(404).send('Page not found');
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
