// SEC-002: login-page affordances that require JavaScript — a password reveal
// toggle and a Caps Lock warning. Loaded as an external, SAME-ORIGIN script
// because the login page's CSP is `script-src 'self'`: inline <script> blocks
// and inline event handlers (onclick=...) are blocked by design, so this file
// is the only CSP-compliant place for the behaviour.
(function () {
  "use strict";

  var pw = document.getElementById("password");
  var toggle = document.getElementById("toggle-password");
  var caps = document.getElementById("capslock-hint");

  // --- Password reveal toggle ---
  // Flips the field between password/text so an operator can confirm what they
  // typed before submitting (fewer lockouts from an unseen typo, esp. with the
  // Caps Lock hint below). Text label + aria-pressed keep it accessible.
  if (pw && toggle) {
    toggle.addEventListener("click", function () {
      var reveal = pw.type === "password";
      pw.type = reveal ? "text" : "password";
      toggle.textContent = reveal ? "隱藏" : "顯示";
      toggle.setAttribute("aria-pressed", reveal ? "true" : "false");
      pw.focus();
    });
  }

  // --- Caps Lock warning ---
  // The single most common cause of a "correct" password being rejected. Show
  // the hint whenever the modifier is engaged while the password field has
  // focus; hide it on blur so it never lingers.
  function syncCaps(e) {
    if (!caps || !e || typeof e.getModifierState !== "function") return;
    caps.classList.toggle("hidden", !e.getModifierState("CapsLock"));
  }
  if (pw) {
    pw.addEventListener("keydown", syncCaps);
    pw.addEventListener("keyup", syncCaps);
    pw.addEventListener("blur", function () {
      if (caps) caps.classList.add("hidden");
    });
  }

  // --- UX-003: lockout countdown ---
  // On a 429 lockout the server renders the error banner with data-retry-after
  // (seconds). Tick it down so the operator sees exactly when they can retry,
  // and keep the submit button disabled until then — hammering the locked form
  // does nothing but log more lockout rows. The server value is authoritative;
  // no reload needed.
  var lockAlert = document.querySelector("[data-retry-after]");
  var submitBtn = document.querySelector('form button[type="submit"]');
  if (lockAlert && submitBtn) {
    var remaining = parseInt(lockAlert.getAttribute("data-retry-after"), 10);
    if (isFinite(remaining) && remaining > 0) {
      submitBtn.disabled = true;
      var tick = function () {
        if (remaining <= 0) {
          submitBtn.disabled = false;
          lockAlert.textContent = "您現在可以再次嘗試登入";
          return;
        }
        lockAlert.textContent = "嘗試次數過多，請於 " + remaining + " 秒後再試";
        remaining -= 1;
        window.setTimeout(tick, 1000);
      };
      tick();
    }
  }
})();
