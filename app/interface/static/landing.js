/* Scroll reveal for the landing page.
 *
 * IntersectionObserver rather than a scroll listener: a scroll handler fires
 * on every frame and forces a reflow each time, which is what makes pages
 * like this stutter on a phone. Elements are revealed once and then released.
 *
 * If the observer is missing, or the reader has asked for reduced motion,
 * everything is shown immediately. The content never depends on the script.
 */
(function () {
    "use strict";

    /* Tells the failsafe in the document head that the reveal is in hand. */
    document.documentElement.setAttribute("data-reveal-ready", "");

    var targets = document.querySelectorAll("[data-reveal]");
    if (!targets.length) {
        return;
    }

    var reduced = window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduced || typeof IntersectionObserver === "undefined") {
        for (var i = 0; i < targets.length; i++) {
            targets[i].classList.add("is-revealed");
        }
        return;
    }

    var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) {
                entry.target.classList.add("is-revealed");
                observer.unobserve(entry.target);
            }
        });
    }, { rootMargin: "0px 0px -12% 0px", threshold: 0.12 });

    for (var j = 0; j < targets.length; j++) {
        observer.observe(targets[j]);
    }
}());
