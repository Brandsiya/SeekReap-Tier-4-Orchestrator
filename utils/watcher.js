# Example watcher:
const chokidar = require('chokidar'); const 
sendToTier5 = require('./sendToTier5'); 
chokidar.watch('/path/to/creator_uploads').on('add', 
async filePath => {
    await sendToTier5(filePath, { title: 
    filePath.split('/').pop(), 
    usesThirdPartyMusic: true });
});
