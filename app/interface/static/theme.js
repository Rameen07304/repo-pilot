/* Theme control, shared by the landing page and the app shell.
 *
 * Two states, per the platform guidance: follow the system, or pin the
 * opposite of whatever the system is set to. Three states (system / light /
 * dark) would mean two of them usually look identical, so a click would
 * sometimes do nothing visible.
 *
 * CSS already defaults to the system setting, so if this never runs the
 * page is still correct; the button simply does nothing.
 */
(function () {
    "use strict";

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
           custom property underneath it changes, which left the cards and
           the nav bar light on a dark page. */
        root.classList.add("theme-switching");
        root.classList.remove("scheme-light", "scheme-dark");
        if (pinned) {
            root.classList.add("scheme-" + pinned);
        }
        if (meta) {
            meta.content = pinned || "light dark";
        }
        /* Read a computed value to force the new styles to be committed
           while transitions are still off. Releasing first lets the
           transition re-arm against the stale colour. */
        void window.getComputedStyle(document.body).backgroundColor;
        /* A timer as well as the frame: requestAnimationFrame does not run
           in a backgrounded tab, and leaving transitions suppressed forever
           would quietly kill every hover on the page. */
        var release = function () { root.classList.remove("theme-switching"); };
        window.requestAnimationFrame(release);
        window.setTimeout(release, 120);

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
                /* Pin the exact opposite of the system setting, not a value
                   recomputed later if the system setting changes. */
                localStorage.setItem(
                    "color-scheme",
                    darkQuery && darkQuery.matches ? "light" : "dark"
                );
            }
        } catch (e) { /* private mode: the class still flips for this page */ }
        render();
    });

    /* CSS reacts to a system change on its own; the button's label and icon
       do not, so they are re-rendered here. */
    if (darkQuery && darkQuery.addEventListener) {
        darkQuery.addEventListener("change", render);
    }

    render();
}());
