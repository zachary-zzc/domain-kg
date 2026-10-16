"use strict";
window.addEventListener("pageshow", function(event) {
  if (event.persisted) window.location.reload();
  else document.documentElement.classList.remove("leaving");
});
window.addEventListener("pagehide", function() {
  document.documentElement.classList.add("leaving");
});
