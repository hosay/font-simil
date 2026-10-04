/* Microsoft Clarity, loaded with consent where the law requires it.
 *
 * Visitors whose browser time zone is in Europe (EEA, UK, Switzerland) get a
 * consent banner, and Clarity loads only after "Accept". Elsewhere it loads
 * unless the visitor declined or sends Global Privacy Control. The choice is
 * kept in localStorage; "Cookie settings" in the footer reopens the banner.
 * Pages that show uploaded content don't include this script at all.
 */
(function () {
    "use strict";
    var script = document.currentScript;
    var projectId = script && script.getAttribute("data-clarity");
    if (!projectId) return;
    var KEY = "dupefont-analytics-consent";
    var loaded = false;

    function getChoice() {
        try { return localStorage.getItem(KEY); } catch (e) { return null; }
    }
    function setChoice(value) {
        try { localStorage.setItem(KEY, value); } catch (e) { /* private mode */ }
    }

    var tz = "";
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* old browser */ }
    var needsConsent = /^Europe\//.test(tz) ||
        /^Atlantic\/(Reykjavik|Canary|Madeira|Azores|Faroe)$/.test(tz) || tz === "Arctic/Longyearbyen";
    var gpc = navigator.globalPrivacyControl === true;

    function loadClarity() {
        if (loaded) return;
        loaded = true;
        (function (c, l, a, r, i, t, y) {
            c[a] = c[a] || function () { (c[a].q = c[a].q || []).push(arguments); };
            t = l.createElement(r); t.async = 1; t.src = "https://www.clarity.ms/tag/" + i;
            y = l.getElementsByTagName(r)[0]; y.parentNode.insertBefore(t, y);
        })(window, document, "clarity", "script", projectId);
        window.clarity("consentv2", { ad_Storage: "denied", analytics_Storage: "granted" });
    }

    function hideBanner() {
        var old = document.getElementById("consent-banner");
        if (old) old.remove();
    }

    function showBanner() {
        hideBanner();
        var bar = document.createElement("div");
        bar.id = "consent-banner";
        bar.className = "consent-banner";
        bar.setAttribute("role", "dialog");
        bar.setAttribute("aria-label", "Analytics cookies");
        bar.innerHTML =
            '<p>We use Microsoft Clarity cookies to see how people use this site so we can improve it. ' +
            'No advertising. <a href="/privacy#analytics">Privacy policy</a></p>' +
            '<div class="consent-actions">' +
            '<button type="button" class="btn btn-outline btn-sm" data-choice="denied">Decline</button>' +
            '<button type="button" class="btn btn-primary btn-sm" data-choice="granted">Accept</button></div>';
        bar.addEventListener("click", function (e) {
            var choice = e.target.getAttribute && e.target.getAttribute("data-choice");
            if (!choice) return;
            setChoice(choice);
            hideBanner();
            if (choice === "granted") {
                loadClarity();
            } else if (loaded && window.clarity) {
                window.clarity("consentv2", { ad_Storage: "denied", analytics_Storage: "denied" });
                window.clarity("consent", false); // erase Clarity cookies
            }
        });
        document.body.appendChild(bar);
    }

    window.dupefontCookieSettings = showBanner;
    document.addEventListener("click", function (e) {
        var link = e.target.closest && e.target.closest("[data-cookie-settings]");
        if (link) { e.preventDefault(); showBanner(); }
    });

    var choice = getChoice();
    if (choice === "granted" || (choice === null && !needsConsent && !gpc)) {
        loadClarity();
    } else if (choice === null && needsConsent && !gpc) {
        if (document.body) showBanner();
        else document.addEventListener("DOMContentLoaded", showBanner);
    }
})();
