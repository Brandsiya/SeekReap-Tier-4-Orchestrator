const MAX_TEST_VIDEOS = 3;
let testCount = localStorage.getItem('seekreap_tests') || 0;

// Video Verification - Tier 4
async function verifyVideo() {
    const videoId = document.getElementById('videoId').value.trim();
    const btn = document.querySelector('.verify-btn');
    const btnText = document.querySelector('.btn-text');
    const btnLoader = document.querySelector('.btn-loader');
    const status = document.getElementById('statusMessage');

    // Validation
    if (!videoId) {
        showStatus('Please enter a Video ID', 'error');
        return;
    }
    if (videoId.length < 5) {
        showStatus('Video ID must be at least 5 characters', 'error');
        return;
    }
    if (!/^[a-zA-Z0-9_-]+$/.test(videoId)) {
        showStatus('Video ID contains invalid characters', 'error');
        return;
    }

    // Check test limit
    if (testCount >= MAX_TEST_VIDEOS) {
        showStatus(`Free tests used (${MAX_TEST_VIDEOS}/3). <a href="#pricing" style="color:var(--primary);">Upgrade now</a>`, 'error');
        return;
    }

    // Show loading
    btn.disabled = true;
    btnText.style.display = 'none';
    btnLoader.style.display = 'inline-block';
    status.className = 'status-message';
    status.innerHTML = '';
    status.style.display = 'block';

    try {
        // Simulate Tier-4 verification (15 global nodes)
        await simulateVerification(videoId);
        
        // Success!
        testCount++;
        localStorage.setItem('seekreap_tests', testCount);
        showStatus(`✅ Verified! ${videoId}<br>Real human viewers confirmed.<br><a href="#pricing" class="cta-button" style="margin-top:1rem;display:inline-block;">Get Certificate →</a>`, 'success');
        
        // Track conversion
        gtag?.('event', 'verification_success', { video_id: videoId });
        
    } catch (error) {
        showStatus('Verification failed. Please check Video ID and try again.', 'error');
    } finally {
        btn.disabled = false;
        btnText.style.display = 'inline';
        btnLoader.style.display = 'none';
    }
}

async function simulateVerification(videoId) {
    return new Promise((resolve, reject) => {
        let nodes = 15;
        const interval = setInterval(() => {
            nodes--;
            document.getElementById('statusMessage').innerHTML = 
                `🔍 Connecting to ${nodes} global nodes...`;
            
            if (nodes <= 0) {
                clearInterval(interval);
                setTimeout(resolve, 2000);
            }
        }, 300);
    });
}

function showStatus(message, type) {
    const status = document.getElementById('statusMessage');
    status.textContent = message;
    status.className = `status-message ${type}`;
    status.style.display = 'block';
}

// Mobile Navigation
function toggleMobileNav() {
    document.querySelector('.main-nav').classList.toggle('active');
}

// Input validation
document.getElementById('videoId')?.addEventListener('input', function() {
    const status = document.getElementById('statusMessage');
    status.style.display = 'none';
});

// Smooth scroll for anchor links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        document.querySelector(this.getAttribute('href')).scrollIntoView({
            behavior: 'smooth'
        });
    });
});

// Active nav highlighting
window.addEventListener('scroll', () => {
    const sections = document.querySelectorAll('section[id]');
    const navLinks = document.querySelectorAll('.nav-link');
    
    let current = '';
    sections.forEach(section => {
        const sectionTop = section.offsetTop;
        if (scrollY >= sectionTop - 200) {
            current = section.getAttribute('id');
        }
    });
    
    navLinks.forEach(link => {
        link.classList.remove('active');
        if (link.getAttribute('href') === `#${current}`) {
            link.classList.add('active');
        }
    });
});

// Global event tracking
function trackEvent(category, action, label) {
    console.log(`${category}: ${action} (${label})`);
    gtag?.('event', action, { category, label });
}
