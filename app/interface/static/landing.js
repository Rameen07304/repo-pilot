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


    /* Two states, per the platform guidance: follow the system, or pin the
       opposite of whatever the system is set to. Three states (system /
       light / dark) would mean two of them usually look identical, so a
       click would sometimes do nothing visible.

       CSS already defaults to the system setting, so if this never runs the
       page is still correct; the button simply does nothing. */
    function setUpThemeToggle() {
        var button = document.querySelector("[data-theme-toggle]");
        if (!button) {
            return;
        }

        var root = document.documentElement;
        var meta = document.querySelector('meta[name="color-scheme"]');
        var label = button.querySelector("[data-theme-label]");
        var darkQuery = window.matchMedia
            ? window.matchMedia("(prefers-color-scheme: dark)")
            : null;

        function stored() {
            try {
                var v = localStorage.getItem("color-scheme");
                return (v === "light" || v === "dark") ? v : null;
            } catch (e) {
                return null;
            }
        }

        function usedScheme() {
            return stored() || (darkQuery && darkQuery.matches ? "dark" : "light");
        }

        function render() {
            var pinned = stored();
            /* Transitions are suppressed across the swap. A live
               background-color transition pins its computed value when the
               custom property underneath it changes, which left the cards
               and the nav bar light on a dark page. */
            root.classList.add("theme-switching");
            root.classList.remove("scheme-light", "scheme-dark");
            if (pinned) {
                root.classList.add("scheme-" + pinned);
            }
            /* Read a computed value to force the new styles to be committed
               while transitions are still off. Releasing first lets the
               transition re-arm against the stale colour. */
            void window.getComputedStyle(document.body).backgroundColor;
            window.requestAnimationFrame(function () {
                root.classList.remove("theme-switching");
            });
            if (meta) {
                meta.content = pinned || "light dark";
            }
            button.setAttribute("aria-pressed", pinned ? "true" : "false");
            if (label) {
                label.textContent = usedScheme() === "dark"
                    ? "Switch to light theme"
                    : "Switch to dark theme";
            }
        }

        button.addEventListener("click", function () {
            try {
                if (stored()) {
                    localStorage.removeItem("color-scheme");
                } else {
                    /* Pin the exact opposite of the system setting, not a
                       value recomputed later if the system setting changes. */
                    localStorage.setItem(
                        "color-scheme",
                        darkQuery && darkQuery.matches ? "light" : "dark"
                    );
                }
            } catch (e) { /* private mode: the class still flips for this page */ }
            render();
        });

        /* CSS reacts to a system change on its own; the button's label and
           icon do not, so they are re-rendered here. */
        if (darkQuery && darkQuery.addEventListener) {
            darkQuery.addEventListener("change", render);
        }

        render();
    }

    setUpReveal();
    setUpScrollSpy();
    setUpThemeToggle();
}());
