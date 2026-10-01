/* Landing page behaviour.
 *
 * Two jobs, both of which the browser does natively where it can:
 *
 *   1. Revealing sections on scroll. Where scroll-driven animations exist,
 *      CSS runs the whole thing off the scroll position on the compositor
 *      and nothing here is needed. Firefox has no support, so there it
 *      falls back to IntersectionObserver, which is still far cheaper than
 *      a scroll listener: a scroll handler fires every frame and forces a
 *      reflow each time.
 *
 *   2. Marking the current section in the nav. CSS :target-current handles
 *      the visual state, but it is not exposed to assistive technology, so
 *      aria-current has to be mirrored onto it by hand.
 */
(function () {
    "use strict";

    var SUPPORTS_VIEW_TIMELINE = !!(window.CSS && CSS.supports
        && CSS.supports("(animation-timeline: view()) and (animation-range: entry)"));

    function setUpReveal() {
        /* Tells the failsafe in the document head that the reveal is in hand. */
        document.documentElement.setAttribute("data-reveal-ready", "");

        if (SUPPORTS_VIEW_TIMELINE) {
            return;
        }

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
    }

    function setUpScrollSpy() {
        if (!(window.CSS && CSS.supports && CSS.supports("scroll-target-group: auto"))) {
            return;
        }

        var links = document.querySelectorAll(".nav-links a");
        if (!links.length) {
            return;
        }

        function syncAriaCurrent() {
            var current = document.querySelector(".nav-links a:target-current");
            for (var i = 0; i < links.length; i++) {
                if (links[i] === current) {
                    links[i].setAttribute("aria-current", "true");
                } else {
                    links[i].removeAttribute("aria-current");
                }
            }
        }

        syncAriaCurrent();
        document.addEventListener("scrollend", syncAriaCurrent);
    }


    /* The stat numbers are already correct in the HTML, so this only
       animates up to what is on screen anyway -- if it never runs, or
       the reader asked for less motion, the right figure is still
       there. A refactor dropped the definition and left the call,
       which threw on every landing page load. */
    function setUpCounters() {
        var targets = document.querySelectorAll("[data-count-to]");
        if (!targets.length || typeof IntersectionObserver === "undefined") {
            return;
        }
        var reduced = window.matchMedia
            && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduced) {
            return;
        }

        var observer = new IntersectionObserver(function (entries) {
            for (var i = 0; i < entries.length; i++) {
                if (entries[i].isIntersecting) {
                    countUp(entries[i].target);
                    observer.unobserve(entries[i].target);
                }
            }
        }, { threshold: 0.6 });

        for (var j = 0; j < targets.length; j++) {
            observer.observe(targets[j]);
        }
    }

    function countUp(node) {
        var target = parseInt(node.getAttribute("data-count-to"), 10);
        if (!isFinite(target) || target <= 0) {
            return;  /* nothing to count to; leave the printed value alone */
        }
        var started = null;
        var DURATION = 900;
        function frame(now) {
            if (started === null) {
                started = now;
            }
            var t = Math.min((now - started) / DURATION, 1);
            /* ease-out, so it decelerates into the final number */
            node.textContent = String(Math.round(target * (1 - Math.pow(1 - t, 3))));
            if (t < 1) {
                requestAnimationFrame(frame);
            } else {
                node.textContent = String(target);
            }
        }
        requestAnimationFrame(frame);
    }

    setUpReveal();
    setUpScrollSpy();
    setUpCounters();
}());
