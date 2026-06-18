/* Landing page behavior — externalized from inline <script> so it runs under the
   app's strict (nonce-based) CSP. Depends on jQuery + slick (loaded before this). */
$(document).ready(function () {
    // Mobile menu toggle
    $('.mob-mnu-ic, ul.mobilemenu li a').click(function () {
        $('.mobilemenu').slideToggle();
        $('.dl-trigger').toggleClass('dl-active');
    });

    // Partner-logo marquee (slick) — only on small screens; unslick above 1023px
    $('.banner-btm-list').slick({
        autoplay: true,
        arrows: false,
        autoplaySpeed: 0,
        speed: 4000,
        cssEase: 'linear',
        responsive: [
            { breakpoint: 9999, settings: 'unslick' },
            {
                breakpoint: 1023,
                settings: {
                    slidesToShow: 2,
                    slidesToScroll: 1,
                    centerMode: true,
                    adaptiveHeight: true,
                    variableWidth: true,
                },
            },
        ],
    });

    // Smooth-scroll the desktop anchor menu
    if ($.fn.scroller) { $('.topMenu').scroller(); }

    // Footer year (replaces the old inline document.write, CSP-safe)
    var y = document.getElementById('footer-year');
    if (y) { y.textContent = new Date().getFullYear(); }

    // ── Advertiser contact form ──────────────────────────────────────────
    var form = document.getElementById('advertiseForm');
    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            var statusEl = document.getElementById('advertiseStatus');
            var btn = form.querySelector('button[type=submit]');
            statusEl.style.display = 'none';
            btn.disabled = true;
            var prev = btn.textContent;
            btn.textContent = 'Sending…';
            try {
                const r = await fetch('/api/landing/contact', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: document.getElementById('adv_name').value,
                        email: document.getElementById('adv_email').value,
                        company: document.getElementById('adv_company').value,
                        message: document.getElementById('adv_message').value,
                        // honeypot — bots fill this; humans never see it
                        website: document.getElementById('adv_website').value,
                    }),
                });
                const body = await r.json().catch(function () { return {}; });
                if (!r.ok) throw new Error(body.error || ('HTTP ' + r.status));
                form.reset();
                statusEl.textContent = body.message ||
                    'Thanks — we got your message and will be in touch shortly.';
                statusEl.className = 'advertise-status ok';
            } catch (err) {
                statusEl.textContent = err.message ||
                    'Something went wrong. Please email hello@gravitasleads.com.';
                statusEl.className = 'advertise-status err';
            } finally {
                statusEl.style.display = 'block';
                btn.disabled = false;
                btn.textContent = prev;
            }
        });
    }
});

// Sticky header on scroll
$(document).scroll(function () {
    $('.top-fix-bar').toggleClass('fixed-nav', $(this).scrollTop() > 110);
    $('.mobilemenu').toggleClass('mobimenu-top', $(this).scrollTop() > 10);
});
