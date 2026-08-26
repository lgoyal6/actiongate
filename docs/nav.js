// Marks the tab for whichever section you are looking at.
//
// Its own scope on purpose: these pages load app.js as a classic script in the
// global scope, and a second one declaring the same helper names would take the
// instrument down with it.
(() => {
  const tabs = [...document.querySelectorAll('.tabs a')];
  const seen = tabs
    .map((a) => document.querySelector(a.getAttribute('href')))
    .filter(Boolean);
  if (!seen.length) return;
  const spy = new IntersectionObserver((entries) => {
    const best = entries
      .filter((e) => e.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!best) return;
    tabs.forEach((a) =>
      a.setAttribute('aria-current', String(a.getAttribute('href') === `#${best.target.id}`)));
  }, { rootMargin: '-90px 0px -55% 0px', threshold: [0.05, 0.3] });
  seen.forEach((el) => spy.observe(el));
})();
