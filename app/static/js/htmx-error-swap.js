// Canonical fleet HTMX error swap — emitted by `luxarch --emit htmx-error-swap`.
//
// Fleet ruling (WWWLUXARDO-116, `luxarch --playbook htmx-error-ux`): an unexpected error propagates
// to the global exception handler (`luxarch --emit exception-handler`), which returns status 500 with
// an HTML error PARTIAL and the header `HX-Error-Swap: true` when the request was an HX-Request.
//
// HTMX 2 does NOT swap non-2xx responses by default — it fires `htmx:responseError` and swaps nothing —
// so without this listener the user presses the button and the page does not change at all. This swaps
// the 500 body into the failing target, but ONLY for responses carrying the marker header, so a raw 500
// or a DEBUG stack trace (or any un-negotiated 5xx) is never injected into the page.
//
// Load it once, globally (e.g. in your base layout's JS bundle).
(function () {
  "use strict";
  document.body.addEventListener("htmx:responseError", function (evt) {
    var xhr = evt.detail && evt.detail.xhr;
    var target = evt.detail && evt.detail.target;
    if (xhr && target && xhr.getResponseHeader("HX-Error-Swap") === "true") {
      target.innerHTML = xhr.response;
    }
  });
})();
