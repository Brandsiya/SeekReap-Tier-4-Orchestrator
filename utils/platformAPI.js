# Example:
const axios = require('axios');
async function submitDispute(videoId, reason) {
  const response = await axios.post('https://youtube.googleapis.com/v3/videos:report', { videoId, reason });
  return response.data;
}
module.exports = { submitDispute };
